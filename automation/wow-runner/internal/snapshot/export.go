package snapshot

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

// ExportJSONL makes two streaming passes over one validated Lua snapshot.
// The scope table may appear after the records, so it must be collected first.
func ExportJSONL(source, output string) error { return ExportJSONLExpected(source, output, "") }

func ExportJSONLExpected(source, output, expectedSHA string) (err error) {
	if _, e := os.Stat(output); !os.IsNotExist(e) {
		return fmt.Errorf("export output already exists or unavailable")
	}
	info, e := os.Stat(source)
	if e != nil {
		return e
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf("snapshot is not a regular file")
	}
	scopes := map[int64]string{}
	fields := map[string]any{}
	date := ""
	firstHash := sha256.New()
	f, e := os.Open(source)
	if e != nil {
		return e
	}
	p := newParser(bufio.NewReaderSize(io.TeeReader(f, firstHash), 256*1024))
	p.onDay = func(day string) error {
		if date != "" {
			return fmt.Errorf("multiple snapshot days")
		}
		date = day
		return nil
	}
	p.onScalar = func(role tableRole, key string, t token) error {
		switch role {
		case roleScopes:
			id, e := strconv.ParseInt(key, 10, 64)
			if e != nil || id <= 0 || t.kind != tokenString || (t.text != "region" && t.text != "realm") {
				return fmt.Errorf("invalid item market scope")
			}
			if _, exists := scopes[id]; exists {
				return fmt.Errorf("duplicate item market scope")
			}
			scopes[id] = t.text
		case roleScan:
			switch key {
			case "realmID", "realmName", "normalizedRealmName", "regionID", "regionName":
				if _, exists := fields[key]; exists {
					return fmt.Errorf("duplicate scan field %s", key)
				}
				v, e := scalarValue(t)
				if e != nil {
					return e
				}
				fields[key] = v
			}
		}
		return nil
	}
	report, e := p.parse()
	closeErr := f.Close()
	if e != nil {
		return e
	}
	if closeErr != nil {
		return closeErr
	}
	if expectedSHA != "" && hex.EncodeToString(firstHash.Sum(nil)) != expectedSHA {
		return fmt.Errorf("snapshot checksum does not match archive manifest")
	}
	if len(report.Scans) != 1 || report.Latest.ActualItemCount <= 0 || report.Latest.ActualItemCount > 1_000_000 {
		return fmt.Errorf("exactly one non-empty complete snapshot is required")
	}
	s := report.Latest
	if s.Metadata.LinkedItemCount != s.ActualItemCount || s.Metadata.MissingCoreCount != 0 || s.Metadata.IncompleteInfoCount != 0 || s.Metadata.APIErrorCount != 0 || s.MarketScopeUnknownCount == nil || *s.MarketScopeUnknownCount != 0 {
		return fmt.Errorf("snapshot completeness validation failed")
	}
	realmID, rok := fields["realmID"].(int64)
	regionID, gok := fields["regionID"].(int64)
	realmName, nok := fields["realmName"].(string)
	if !rok || !gok || !nok || realmID <= 0 || regionID <= 0 || regionID > 5 || realmName == "" || s.Timestamp <= 0 {
		return fmt.Errorf("source region or realm identity missing")
	}
	if _, e = time.Parse("2006-01-02", date); e != nil {
		return fmt.Errorf("invalid snapshot date")
	}
	if e = os.MkdirAll(filepath.Dir(output), 0700); e != nil {
		return e
	}
	tmp, e := os.CreateTemp(filepath.Dir(output), ".snapshot-*.tmp")
	if e != nil {
		return e
	}
	tmpName := tmp.Name()
	defer os.Remove(tmpName)
	defer func() { _ = tmp.Close() }()
	z := gzip.NewWriter(tmp)
	enc := json.NewEncoder(z)
	enc.SetEscapeHTML(false)
	header := map[string]any{"type": "snapshot", "schema_version": 1, "complete": true, "region_id": regionID, "region_name": fields["regionName"], "realm_id": realmID, "realm_name": realmName, "normalized_realm_name": fields["normalizedRealmName"], "captured_at": s.Timestamp, "source_date": date, "record_count": s.ActualItemCount}
	if e = enc.Encode(header); e != nil {
		return e
	}
	f, e = os.Open(source)
	if e != nil {
		return e
	}
	secondHash := sha256.New()
	p = newParser(bufio.NewReaderSize(io.TeeReader(f, secondHash), 256*1024))
	written := int64(0)
	p.onItem = func(item map[string]any) error {
		id, ok := item["itemID"].(int64)
		if !ok || id <= 0 {
			return fmt.Errorf("auction has no itemID")
		}
		scope, ok := scopes[id]
		if !ok {
			return fmt.Errorf("market scope missing for item %d", id)
		}
		item["type"] = "auction"
		item["marketScope"] = scope
		written++
		if written > s.ActualItemCount {
			return fmt.Errorf("too many auction rows")
		}
		return enc.Encode(item)
	}
	second, e := p.parse()
	closeErr = f.Close()
	if e != nil {
		return e
	}
	if closeErr != nil {
		return closeErr
	}
	if written != s.ActualItemCount || len(second.Scans) != 1 || hex.EncodeToString(firstHash.Sum(nil)) != hex.EncodeToString(secondHash.Sum(nil)) {
		return fmt.Errorf("snapshot changed during export or record count mismatch")
	}
	if e = z.Close(); e != nil {
		return e
	}
	if e = tmp.Sync(); e != nil {
		return e
	}
	if e = tmp.Close(); e != nil {
		return e
	}
	// A same-directory hard link publishes only if the target does not exist.
	if e = os.Link(tmpName, output); e != nil {
		return fmt.Errorf("publish export: %w", e)
	}
	return nil
}

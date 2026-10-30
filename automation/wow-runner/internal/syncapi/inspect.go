package syncapi

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
)

// Inspect builds the server manifest from the exact compressed file.
func Inspect(path string) (Manifest, error) {
	var m Manifest
	f, e := os.Open(path)
	if e != nil {
		return m, e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return m, e
	}
	z, e := gzip.NewReader(f)
	if e != nil {
		return m, e
	}
	line, e := bufio.NewReader(io.LimitReader(z, 65537)).ReadBytes('\n')
	closeErr := z.Close()
	if e != nil || closeErr != nil || len(line) > 65536 {
		return m, fmt.Errorf("invalid snapshot header")
	}
	var header struct {
		Type          string `json:"type"`
		Complete      bool   `json:"complete"`
		SchemaVersion int    `json:"schema_version"`
		RegionID      int64  `json:"region_id"`
		RealmID       int64  `json:"realm_id"`
		CapturedAt    int64  `json:"captured_at"`
		RecordCount   int64  `json:"record_count"`
	}
	if e = json.Unmarshal(line, &header); e != nil || header.Type != "snapshot" || !header.Complete {
		return m, fmt.Errorf("invalid snapshot metadata")
	}
	if _, e = f.Seek(0, io.SeekStart); e != nil {
		return m, e
	}
	h := sha256.New()
	if _, e = io.Copy(h, f); e != nil {
		return m, e
	}
	m = Manifest{SchemaVersion: header.SchemaVersion, CompressedSHA256: hex.EncodeToString(h.Sum(nil)), SizeBytes: info.Size(), RegionID: header.RegionID, RealmID: header.RealmID, CapturedAt: header.CapturedAt, RecordCount: header.RecordCount}
	return m, m.Validate()
}

//go:build windows

package main

import (
	"archive/tar"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"

	"wow-auction/automation/wow-runner/internal/snapshot"
)

type recoveryManifest struct {
	SnapshotSHA256    string `json:"snapshotSha256"`
	ArchiveSHA256     string `json:"archiveSha256"`
	ActualRecordCount int64  `json:"actualRecordCount"`
}

func countVerifiedArchives(root string, characters []collectCharacter, started time.Time) (int, error) {
	count := 0
	for i, ch := range characters {
		ok, e := hasVerifiedArchive(root, i, ch, started)
		if e != nil {
			return 0, e
		}
		if ok {
			count++
		}
	}
	return count, nil
}

func hasVerifiedArchive(root string, index int, ch collectCharacter, started time.Time) (bool, error) {
	characterDir := filepath.Join(root, fmt.Sprintf("character-%02d", index+1))
	patterns := []string{filepath.Join(characterDir, "auction-*.lua.tgz"), filepath.Join(characterDir, "attempt-*", "auction-*.lua.tgz")}
	for _, pattern := range patterns {
		paths, e := filepath.Glob(pattern)
		if e != nil {
			return false, e
		}
		for _, archive := range paths {
			// A missing/invalid pair is not a completion; preserve it and retry in a new attempt.
			ok, e := verifyArchive(archive, ch, started)
			if e != nil {
				return false, e
			}
			if ok {
				return true, nil
			}
		}
	}
	return false, nil
}

func verifyArchive(path string, ch collectCharacter, started time.Time) (bool, error) {
	manifestPath := path[:len(path)-len(".tgz")] + ".manifest.json"
	b, e := os.ReadFile(manifestPath)
	if os.IsNotExist(e) {
		return false, nil
	}
	if e != nil {
		return false, e
	}
	var m recoveryManifest
	if json.Unmarshal(b, &m) != nil || len(m.SnapshotSHA256) != 64 || len(m.ArchiveSHA256) != 64 {
		return false, nil
	}
	f, e := os.Open(path)
	if e != nil {
		return false, e
	}
	defer f.Close()
	h := sha256.New()
	if _, e = io.Copy(h, f); e != nil {
		return false, e
	}
	if hex.EncodeToString(h.Sum(nil)) != m.ArchiveSHA256 {
		return false, nil
	}
	if _, e = f.Seek(0, io.SeekStart); e != nil {
		return false, e
	}
	z, e := gzip.NewReader(f)
	if e != nil {
		return false, nil
	}
	defer z.Close()
	t := tar.NewReader(z)
	header, e := t.Next()
	if e != nil || !header.FileInfo().Mode().IsRegular() || filepath.Ext(header.Name) != ".lua" {
		return false, nil
	}
	contentHash := sha256.New()
	r, e := snapshot.Parse(io.TeeReader(t, contentHash))
	if e != nil {
		return false, nil
	}
	if _, e = t.Next(); e != io.EOF {
		return false, nil
	}
	if _, e = io.Copy(io.Discard, z); e != nil {
		return false, nil
	}
	s := r.Latest
	if hex.EncodeToString(contentHash.Sum(nil)) != m.SnapshotSHA256 || s.ActualItemCount != m.ActualRecordCount || s.ActualItemCount <= 0 {
		return false, nil
	}
	if s.Metadata.RecordCount != s.ActualItemCount || s.Metadata.ItemCount != s.ActualItemCount || s.Metadata.LinkedItemCount != s.ActualItemCount || s.Metadata.MissingCoreCount != 0 || s.Metadata.APIErrorCount != 0 || s.Metadata.IncompleteInfoCount != 0 {
		return false, nil
	}
	if s.CharacterName != ch.Name || s.RealmName != ch.Realm || s.CharacterGUID == "" || s.RunID == "" || s.MarketScopeUnknownCount == nil || *s.MarketScopeUnknownCount != 0 {
		return false, nil
	}
	if !started.IsZero() && s.Timestamp < started.Unix() {
		return false, nil
	}
	return true, nil
}

package snapshot

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestArchiveValidatedSnapshotRoundTrip(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "AuctionSearchExample.lua")
	content := []byte(validSnapshotLua(200, 200, 2))
	if err := os.WriteFile(source, content, 0o600); err != nil {
		t.Fatal(err)
	}
	report, err := ValidateFile(source, time.Time{})
	if err != nil {
		t.Fatal(err)
	}
	result, err := ArchiveValidatedSnapshot(source, filepath.Join(root, "archive"), report)
	if err != nil {
		t.Fatal(err)
	}
	if result.SnapshotSHA256 == "" || result.ArchiveSHA256 == "" {
		t.Fatalf("missing hashes: %+v", result)
	}
	archiveDigest, err := hashFile(result.ArchivePath)
	if err != nil {
		t.Fatal(err)
	}
	if archiveDigest != result.ArchiveSHA256 {
		t.Fatalf("archive hash=%s, want %s", archiveDigest, result.ArchiveSHA256)
	}

	f, err := os.Open(result.ArchivePath)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		t.Fatal(err)
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	header, err := tr.Next()
	if err != nil {
		t.Fatal(err)
	}
	if header.Name != "auction.lua" {
		t.Fatalf("archive entry=%q", header.Name)
	}
	got, err := io.ReadAll(tr)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(content) {
		t.Fatal("archive content differs from source")
	}
	if _, err := tr.Next(); err != io.EOF {
		t.Fatalf("unexpected second archive entry: %v", err)
	}

	manifestBytes, err := os.ReadFile(result.ManifestPath)
	if err != nil {
		t.Fatal(err)
	}
	var manifest archiveManifest
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		t.Fatal(err)
	}
	if manifest.SnapshotSHA256 != result.SnapshotSHA256 || manifest.RecordCount != 2 || manifest.ActualRecordCount != 2 {
		t.Fatalf("manifest mismatch: %+v", manifest)
	}
}

func TestResetSavedVariablesRequiresExactHash(t *testing.T) {
	root := t.TempDir()
	source := filepath.Join(root, "AuctionSearchExample.lua")
	content := []byte(validSnapshotLua(200, 200, 2))
	if err := os.WriteFile(source, content, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := ResetSavedVariablesIfMatch(source, "not-the-current-hash"); err == nil {
		t.Fatal("hash mismatch unexpectedly reset source")
	}
	unchanged, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	if string(unchanged) != string(content) {
		t.Fatal("source changed after rejected reset")
	}

	digest, err := hashFile(source)
	if err != nil {
		t.Fatal(err)
	}
	if err := ResetSavedVariablesIfMatch(source, digest); err != nil {
		t.Fatal(err)
	}
	reset, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	if string(reset) != emptySavedVariables {
		t.Fatalf("reset content=%q", reset)
	}
}

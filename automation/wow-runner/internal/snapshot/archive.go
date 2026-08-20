package snapshot

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
)

const emptySavedVariables = `AuctionSearchDB = {
["auctions"] = {},
["lastScanTime"] = 0,
["settings"] = {},
}
`

type ArchiveResult struct {
	ArchivePath    string
	ManifestPath   string
	SnapshotSHA256 string
	ArchiveSHA256  string
	SourceSize     int64
}

type archiveManifest struct {
	Version           int    `json:"version"`
	CreatedAt         string `json:"createdAt"`
	SnapshotSHA256    string `json:"snapshotSha256"`
	ArchiveSHA256     string `json:"archiveSha256"`
	SourceSize        int64  `json:"sourceSize"`
	LastScanTime      int64  `json:"lastScanTime"`
	ItemCount         int64  `json:"itemCount"`
	RecordCount       int64  `json:"recordCount"`
	ActualRecordCount int64  `json:"actualRecordCount"`
	LinkedItemCount   int64  `json:"linkedItemCount"`
	MissingCoreCount  int64  `json:"missingCoreCount"`
	APIErrorCount     int64  `json:"apiErrorCount"`
}

func hashFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// ArchiveValidatedSnapshot creates a streaming .lua.tgz plus a JSON integrity
// manifest. It never loads the snapshot into memory and atomically replaces
// only files inside archiveDir.
func ArchiveValidatedSnapshot(source, archiveDir string, report Report) (ArchiveResult, error) {
	if len(report.Scans) == 0 || report.Latest.Timestamp <= 0 {
		return ArchiveResult{}, fmt.Errorf("cannot archive an empty report")
	}
	sourceAbs, err := filepath.Abs(source)
	if err != nil {
		return ArchiveResult{}, fmt.Errorf("archive source path: %w", err)
	}
	archiveDirAbs, err := filepath.Abs(archiveDir)
	if err != nil {
		return ArchiveResult{}, fmt.Errorf("archive directory path: %w", err)
	}
	if err := os.MkdirAll(archiveDirAbs, 0o755); err != nil {
		return ArchiveResult{}, fmt.Errorf("create archive directory: %w", err)
	}

	src, err := os.Open(sourceAbs)
	if err != nil {
		return ArchiveResult{}, fmt.Errorf("open archive source: %w", err)
	}
	defer src.Close()
	before, err := src.Stat()
	if err != nil {
		return ArchiveResult{}, fmt.Errorf("stat archive source: %w", err)
	}
	if !before.Mode().IsRegular() {
		return ArchiveResult{}, fmt.Errorf("archive source is not a regular file: %q", sourceAbs)
	}

	snapshotHash := sha256.New()
	if _, err := io.Copy(snapshotHash, src); err != nil {
		return ArchiveResult{}, fmt.Errorf("hash archive source: %w", err)
	}
	snapshotSHA := hex.EncodeToString(snapshotHash.Sum(nil))
	if _, err := src.Seek(0, io.SeekStart); err != nil {
		return ArchiveResult{}, fmt.Errorf("rewind archive source: %w", err)
	}

	stamp := time.Unix(report.Latest.Timestamp, 0).UTC().Format("20060102-150405")
	base := fmt.Sprintf("auction-%s-%s.lua", stamp, snapshotSHA[:12])
	archivePath := filepath.Join(archiveDirAbs, base+".tgz")
	manifestPath := filepath.Join(archiveDirAbs, base+".manifest.json")
	tmp, err := os.CreateTemp(archiveDirAbs, "."+base+".tmp-*")
	if err != nil {
		return ArchiveResult{}, fmt.Errorf("create archive temporary file: %w", err)
	}
	tmpPath := tmp.Name()
	tmpOpen := true
	keepTemp := true
	defer func() {
		if tmpOpen {
			_ = tmp.Close()
		}
		if keepTemp {
			_ = os.Remove(tmpPath)
		}
	}()

	archiveHash := sha256.New()
	gz, err := gzip.NewWriterLevel(io.MultiWriter(tmp, archiveHash), gzip.BestSpeed)
	if err != nil {
		return ArchiveResult{}, fmt.Errorf("create gzip writer: %w", err)
	}
	gz.Name = "auction.lua"
	gz.ModTime = time.Unix(report.Latest.Timestamp, 0).UTC()
	tw := tar.NewWriter(gz)
	header := &tar.Header{
		Name:    "auction.lua",
		Mode:    0o600,
		Size:    before.Size(),
		ModTime: time.Unix(report.Latest.Timestamp, 0).UTC(),
	}
	if err := tw.WriteHeader(header); err != nil {
		return ArchiveResult{}, fmt.Errorf("write archive header: %w", err)
	}
	if _, err := io.Copy(tw, src); err != nil {
		return ArchiveResult{}, fmt.Errorf("write archive snapshot: %w", err)
	}
	if err := tw.Close(); err != nil {
		return ArchiveResult{}, fmt.Errorf("close tar writer: %w", err)
	}
	if err := gz.Close(); err != nil {
		return ArchiveResult{}, fmt.Errorf("close gzip writer: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		return ArchiveResult{}, fmt.Errorf("flush archive: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return ArchiveResult{}, fmt.Errorf("close archive: %w", err)
	}
	tmpOpen = false

	after, err := src.Stat()
	if err != nil {
		return ArchiveResult{}, fmt.Errorf("stat archive source after copy: %w", err)
	}
	if before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return ArchiveResult{}, fmt.Errorf("archive source changed while being copied: %q", sourceAbs)
	}
	if err := replaceFile(tmpPath, archivePath); err != nil {
		return ArchiveResult{}, fmt.Errorf("publish archive: %w", err)
	}
	keepTemp = false
	archiveSHA := hex.EncodeToString(archiveHash.Sum(nil))

	manifest := archiveManifest{
		Version:           1,
		CreatedAt:         time.Now().UTC().Format(time.RFC3339),
		SnapshotSHA256:    snapshotSHA,
		ArchiveSHA256:     archiveSHA,
		SourceSize:        before.Size(),
		LastScanTime:      report.LastScanTime,
		ItemCount:         report.LastScan.ItemCount,
		RecordCount:       report.LastScan.RecordCount,
		ActualRecordCount: report.Latest.ActualItemCount,
		LinkedItemCount:   report.LastScan.LinkedItemCount,
		MissingCoreCount:  report.LastScan.MissingCoreCount,
		APIErrorCount:     report.LastScan.APIErrorCount,
	}
	manifestBytes, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return ArchiveResult{}, fmt.Errorf("encode archive manifest: %w", err)
	}
	manifestBytes = append(manifestBytes, '\n')
	if err := writeAtomic(manifestPath, manifestBytes, 0o600); err != nil {
		return ArchiveResult{}, fmt.Errorf("publish archive manifest: %w", err)
	}

	return ArchiveResult{
		ArchivePath:    archivePath,
		ManifestPath:   manifestPath,
		SnapshotSHA256: snapshotSHA,
		ArchiveSHA256:  archiveSHA,
		SourceSize:     before.Size(),
	}, nil
}

func writeAtomic(path string, content []byte, mode os.FileMode) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	keep := true
	defer func() {
		_ = tmp.Close()
		if keep {
			_ = os.Remove(tmpPath)
		}
	}()
	if err := tmp.Chmod(mode); err != nil {
		return err
	}
	if _, err := tmp.Write(content); err != nil {
		return err
	}
	if err := tmp.Sync(); err != nil {
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := replaceFile(tmpPath, path); err != nil {
		return err
	}
	keep = false
	return nil
}

// ResetSavedVariablesIfMatch replaces source with a minimal addon table only
// when its current bytes still match the snapshot that was archived/imported.
func ResetSavedVariablesIfMatch(source, expectedSHA256 string) error {
	actual, err := hashFile(source)
	if err != nil {
		return fmt.Errorf("hash SavedVariables before reset: %w", err)
	}
	if actual != expectedSHA256 {
		return fmt.Errorf("SavedVariables changed before reset: got %s want %s", actual, expectedSHA256)
	}
	info, err := os.Stat(source)
	if err != nil {
		return fmt.Errorf("stat SavedVariables before reset: %w", err)
	}
	if err := writeAtomic(source, []byte(emptySavedVariables), info.Mode().Perm()); err != nil {
		return fmt.Errorf("reset SavedVariables: %w", err)
	}
	return nil
}

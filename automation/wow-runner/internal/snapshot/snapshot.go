// Package snapshot validates and atomically synchronizes AuctionSearchExample
// SavedVariables snapshots without loading the (potentially hundreds of MB)
// Lua file into memory.
package snapshot

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

var (
	// ErrInvalidSnapshot identifies malformed or internally inconsistent data.
	ErrInvalidSnapshot = errors.New("invalid auction snapshot")
	// ErrStaleSnapshot identifies a structurally valid snapshot older than the
	// caller's scan trigger.
	ErrStaleSnapshot = errors.New("stale auction snapshot")
)

// Metadata is the compact integrity summary written by the addon both to
// AuctionSearchDB.lastScan and to every auctions[*].scans[*] entry.
type Metadata struct {
	ItemCount           int64
	RecordCount         int64
	MissingCoreCount    int64
	APIErrorCount       int64
	LinkedItemCount     int64
	IncompleteInfoCount int64
}

// ScanSummary contains the declared summary and the independently counted
// number of direct record tables in the scan's items table.
type ScanSummary struct {
	Timestamp       int64
	Metadata        Metadata
	ActualItemCount int64
}

// Report is the validated compact representation of a SavedVariables file.
// It intentionally does not retain individual auction records.
type Report struct {
	LastScanTime int64
	LastScan     Metadata
	Scans        []ScanSummary
	Latest       ScanSummary
}

// Parse reads and validates one complete AuctionSearchDB assignment. Parsing
// is streaming: memory use is bounded by an individual Lua token plus the
// small number of scan summaries.
func Parse(r io.Reader) (Report, error) {
	if r == nil {
		return Report{}, fmt.Errorf("%w: nil reader", ErrInvalidSnapshot)
	}
	p := newParser(bufio.NewReaderSize(r, 256*1024))
	report, err := p.parse()
	if err != nil {
		return Report{}, fmt.Errorf("%w: %v", ErrInvalidSnapshot, err)
	}
	return report, nil
}

// ValidateFile parses path and optionally requires the latest scan timestamp
// to be at least minScanTime. A zero minScanTime disables freshness checking.
func ValidateFile(path string, minScanTime time.Time) (Report, error) {
	f, err := os.Open(path)
	if err != nil {
		return Report{}, fmt.Errorf("open snapshot %q: %w", path, err)
	}
	defer f.Close()

	report, err := Parse(f)
	if err != nil {
		return Report{}, err
	}
	if err := validateFreshness(report, minScanTime); err != nil {
		return Report{}, err
	}
	return report, nil
}

// SyncAndValidate streams source into a temporary file in dest's directory,
// validates those exact bytes, fsyncs them, and then atomically replaces dest.
// If copying, parsing, freshness validation, or replacement fails, the prior
// destination remains in place and the temporary file is removed.
func SyncAndValidate(source, dest string, minScanTime time.Time) (Report, error) {
	return SyncAndValidateWithCheck(source, dest, minScanTime, nil)
}

// SyncAndValidateWithCheck performs the same atomic copy as SyncAndValidate,
// but runs check against the exact parsed bytes before replacing dest. A check
// failure leaves the previous destination untouched.
func SyncAndValidateWithCheck(
	source, dest string,
	minScanTime time.Time,
	check func(Report) error,
) (Report, error) {
	sourceAbs, err := filepath.Abs(source)
	if err != nil {
		return Report{}, fmt.Errorf("source path: %w", err)
	}
	destAbs, err := filepath.Abs(dest)
	if err != nil {
		return Report{}, fmt.Errorf("destination path: %w", err)
	}
	if filepath.Clean(sourceAbs) == filepath.Clean(destAbs) {
		return Report{}, fmt.Errorf("source and destination are the same file: %q", sourceAbs)
	}

	src, err := os.Open(sourceAbs)
	if err != nil {
		return Report{}, fmt.Errorf("open source %q: %w", sourceAbs, err)
	}
	defer src.Close()
	before, err := src.Stat()
	if err != nil {
		return Report{}, fmt.Errorf("stat source %q: %w", sourceAbs, err)
	}
	if !before.Mode().IsRegular() {
		return Report{}, fmt.Errorf("source is not a regular file: %q", sourceAbs)
	}
	if destinationInfo, statErr := os.Stat(destAbs); statErr == nil {
		if os.SameFile(before, destinationInfo) {
			return Report{}, fmt.Errorf("source and destination are the same file: %q", sourceAbs)
		}
	} else if !errors.Is(statErr, os.ErrNotExist) {
		return Report{}, fmt.Errorf("stat destination %q: %w", destAbs, statErr)
	}

	destDir := filepath.Dir(destAbs)
	if err := os.MkdirAll(destDir, 0o755); err != nil {
		return Report{}, fmt.Errorf("create destination directory %q: %w", destDir, err)
	}
	tmp, err := os.CreateTemp(destDir, "."+filepath.Base(destAbs)+".tmp-*")
	if err != nil {
		return Report{}, fmt.Errorf("create destination temporary file: %w", err)
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

	report, err := Parse(io.TeeReader(src, tmp))
	if err != nil {
		return Report{}, fmt.Errorf("validate copied source: %w", err)
	}
	if err := validateFreshness(report, minScanTime); err != nil {
		return Report{}, err
	}
	if check != nil {
		if err := check(report); err != nil {
			return Report{}, fmt.Errorf("%w: acceptance check: %w", ErrInvalidSnapshot, err)
		}
	}
	after, err := src.Stat()
	if err != nil {
		return Report{}, fmt.Errorf("stat source after copy: %w", err)
	}
	if before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return Report{}, fmt.Errorf("source changed while being copied: %q", sourceAbs)
	}
	if err := tmp.Chmod(before.Mode().Perm()); err != nil {
		return Report{}, fmt.Errorf("set temporary file mode: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		return Report{}, fmt.Errorf("flush temporary snapshot: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return Report{}, fmt.Errorf("close temporary snapshot: %w", err)
	}
	tmpOpen = false

	if err := replaceFile(tmpPath, destAbs); err != nil {
		return Report{}, fmt.Errorf("replace destination %q: %w", destAbs, err)
	}
	keepTemp = false
	return report, nil
}

func validateFreshness(report Report, minScanTime time.Time) error {
	if minScanTime.IsZero() {
		return nil
	}
	minimum := minScanTime.Unix()
	if report.Latest.Timestamp < minimum {
		return fmt.Errorf(
			"%w: latest scan timestamp %d is older than required %d",
			ErrStaleSnapshot,
			report.Latest.Timestamp,
			minimum,
		)
	}
	return nil
}

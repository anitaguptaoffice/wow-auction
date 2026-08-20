package snapshot

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func metadataLua(count int64) string {
	return fmt.Sprintf(`
["apiErrorCount"] = 0,
["itemCount"] = %d,
["linkedItemCount"] = %d,
["recordCount"] = %d,
["missingCoreCount"] = 0,
["incompleteInfoCount"] = 0,
`, count, count, count)
}

func validSnapshotLua(lastTimestamp, latestTimestamp, latestDeclaredCount int64) string {
	return fmt.Sprintf(`AuctionSearchDB = {
["lastScan"] = {%s},
["lastScanTime"] = %d,
["settings"] = { ["maxHistoryDays"] = 7 },
["auctions"] = {
  ["2026-08-19"] = {
    ["timestamp"] = 100,
    ["scans"] = {
      {
        %s
        ["timestamp"] = 100,
        ["items"] = {
          { ["itemID"] = 1, ["name"] = "old record with } and escaped quote \"" },
        },
      },
    },
  },
  ["2026-08-20"] = {
    ["scans"] = {
      {
        %s
        ["items"] = {
          { ["itemID"] = 2, ["note"] = [=[long string with { } and "quotes"]=] },
          { ["itemID"] = 3, ["enabled"] = true },
        },
        ["timestamp"] = %d,
      },
    },
  },
},
}
`, metadataLua(latestDeclaredCount), lastTimestamp, metadataLua(1), metadataLua(latestDeclaredCount), latestTimestamp)
}

func TestParseCompleteSnapshotCountsEveryScan(t *testing.T) {
	report, err := Parse(strings.NewReader(validSnapshotLua(200, 200, 2)))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if report.LastScanTime != 200 || report.Latest.Timestamp != 200 {
		t.Fatalf("timestamps: %+v", report)
	}
	if len(report.Scans) != 2 {
		t.Fatalf("scan count=%d, want 2", len(report.Scans))
	}
	if report.Scans[0].ActualItemCount != 1 || report.Scans[1].ActualItemCount != 2 {
		t.Fatalf("independent item counts: %+v", report.Scans)
	}
	if report.LastScan.RecordCount != 2 || report.Latest.Metadata != report.LastScan {
		t.Fatalf("last/latest metadata mismatch: %+v", report)
	}
}

func TestParseRejectsTruncatedTableAndString(t *testing.T) {
	complete := validSnapshotLua(200, 200, 2)
	for name, input := range map[string]string{
		"missing closing braces": complete[:len(complete)-8],
		"unterminated string":    `AuctionSearchDB = { ["lastScan"] = { ["name"] = "cut`,
	} {
		t.Run(name, func(t *testing.T) {
			_, err := Parse(strings.NewReader(input))
			if !errors.Is(err, ErrInvalidSnapshot) {
				t.Fatalf("error=%v, want ErrInvalidSnapshot", err)
			}
		})
	}
}

func TestParseRejectsPerScanCountMismatch(t *testing.T) {
	_, err := Parse(strings.NewReader(validSnapshotLua(200, 200, 3)))
	if !errors.Is(err, ErrInvalidSnapshot) {
		t.Fatalf("error=%v, want ErrInvalidSnapshot", err)
	}
	if err == nil || !strings.Contains(err.Error(), "items contains 2 records") {
		t.Fatalf("unexpected mismatch error: %v", err)
	}
}

func TestParseRejectsLatestMetadataOrTimestampMismatch(t *testing.T) {
	for name, input := range map[string]string{
		"timestamp": validSnapshotLua(199, 200, 2),
		"metadata": strings.Replace(
			validSnapshotLua(200, 200, 2),
			`["linkedItemCount"] = 2`,
			`["linkedItemCount"] = 1`,
			1,
		),
	} {
		t.Run(name, func(t *testing.T) {
			_, err := Parse(strings.NewReader(input))
			if !errors.Is(err, ErrInvalidSnapshot) {
				t.Fatalf("error=%v, want ErrInvalidSnapshot", err)
			}
		})
	}
}

func TestValidateFileRejectsStaleSnapshot(t *testing.T) {
	path := filepath.Join(t.TempDir(), "AuctionSearchExample.lua")
	if err := os.WriteFile(path, []byte(validSnapshotLua(200, 200, 2)), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := ValidateFile(path, time.Unix(201, 0))
	if !errors.Is(err, ErrStaleSnapshot) {
		t.Fatalf("error=%v, want ErrStaleSnapshot", err)
	}
}

func TestSyncAndValidatePreservesOldDestinationOnFailure(t *testing.T) {
	dir := t.TempDir()
	source := filepath.Join(dir, "source.lua")
	destination := filepath.Join(dir, "auction.lua")
	old := []byte("previous valid destination")
	if err := os.WriteFile(destination, old, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(source, []byte(validSnapshotLua(200, 200, 3)), 0o600); err != nil {
		t.Fatal(err)
	}

	if _, err := SyncAndValidate(source, destination, time.Time{}); !errors.Is(err, ErrInvalidSnapshot) {
		t.Fatalf("error=%v, want ErrInvalidSnapshot", err)
	}
	got, err := os.ReadFile(destination)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(old) {
		t.Fatalf("destination changed after failed validation: %q", got)
	}

	valid := []byte(validSnapshotLua(200, 200, 2))
	if err := os.WriteFile(source, valid, 0o640); err != nil {
		t.Fatal(err)
	}
	report, err := SyncAndValidate(source, destination, time.Unix(200, 0))
	if err != nil {
		t.Fatalf("successful sync: %v", err)
	}
	if report.Latest.ActualItemCount != 2 {
		t.Fatalf("report=%+v", report)
	}
	got, err = os.ReadFile(destination)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(valid) {
		t.Fatal("destination does not contain the validated source bytes")
	}
}

func TestSyncAndValidateWithCheckPreservesOldDestination(t *testing.T) {
	dir := t.TempDir()
	source := filepath.Join(dir, "source.lua")
	destination := filepath.Join(dir, "auction.lua")
	old := []byte("previous accepted destination")
	if err := os.WriteFile(source, []byte(validSnapshotLua(200, 200, 2)), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(destination, old, 0o600); err != nil {
		t.Fatal(err)
	}
	rejected := errors.New("core rows missing")
	_, err := SyncAndValidateWithCheck(source, destination, time.Time{}, func(Report) error {
		return rejected
	})
	if !errors.Is(err, rejected) {
		t.Fatalf("error=%v, want acceptance error", err)
	}
	got, readErr := os.ReadFile(destination)
	if readErr != nil {
		t.Fatal(readErr)
	}
	if string(got) != string(old) {
		t.Fatalf("destination changed after acceptance failure: %q", got)
	}
}

// This opt-in test lets development machines exercise the streaming parser
// against a real, very large SavedVariables export without making CI depend on
// a local WoW installation.
func TestValidateExternalSnapshot(t *testing.T) {
	path := os.Getenv("WOW_SNAPSHOT_TEST_FILE")
	if path == "" {
		t.Skip("WOW_SNAPSHOT_TEST_FILE is not set")
	}
	report, err := ValidateFile(path, time.Time{})
	if err != nil {
		t.Fatalf("ValidateFile(%q): %v", path, err)
	}
	t.Logf(
		"validated %d scans; latest timestamp=%d records=%d",
		len(report.Scans),
		report.Latest.Timestamp,
		report.Latest.ActualItemCount,
	)
}

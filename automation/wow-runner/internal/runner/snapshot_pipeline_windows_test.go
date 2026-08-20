//go:build windows

package runner

import (
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
)

const pipelineFixture = `AuctionSearchDB = {
["lastScan"] = {
  ["apiErrorCount"] = 0, ["itemCount"] = 1, ["linkedItemCount"] = 1,
  ["recordCount"] = 1, ["missingCoreCount"] = 0, ["incompleteInfoCount"] = 0,
  ["realmName"] = "Test Realm", ["normalizedRealmName"] = "TestRealm",
  ["realmID"] = 123, ["regionID"] = 5, ["regionName"] = "CN",
},
["lastScanTime"] = 200,
["settings"] = {},
["auctions"] = {
  ["1970-01-01"] = {
    ["timestamp"] = 200,
    ["scans"] = {
      {
        ["apiErrorCount"] = 0, ["itemCount"] = 1, ["linkedItemCount"] = 1,
        ["recordCount"] = 1, ["missingCoreCount"] = 0, ["incompleteInfoCount"] = 0,
        ["timestamp"] = 200, ["durationMs"] = 10,
        ["realmName"] = "Test Realm", ["normalizedRealmName"] = "TestRealm",
        ["realmID"] = 123, ["regionID"] = 5, ["regionName"] = "CN",
        ["itemMarketScopes"] = { [123] = "realm" },
        ["items"] = {
          {
            ["itemID"] = 123, ["name"] = "Test Item", ["texture"] = 1,
            ["quantity"] = 2, ["qualityID"] = 1, ["minBid"] = 10,
            ["buyoutAmount"] = 20, ["bidAmount"] = 0, ["timeLeftBand"] = 4,
            ["itemLink"] = "|cffffffff|Hitem:123::::::::80:::::|h[Test Item]|h|r",
            ["hasAllInfo"] = true,
          },
        },
      },
    },
  },
},
}
`

// Opt-in because it invokes the repository's real Python/SQLAlchemy importer.
func TestSnapshotPipelineExternalImporterAndReset(t *testing.T) {
	python := os.Getenv("WOW_PIPELINE_PYTHON")
	importer := os.Getenv("WOW_PIPELINE_IMPORTER")
	if python == "" || importer == "" {
		t.Skip("WOW_PIPELINE_PYTHON and WOW_PIPELINE_IMPORTER are not set")
	}
	root := t.TempDir()
	source := filepath.Join(root, "AuctionSearchExample.lua")
	destination := filepath.Join(root, "auction.lua")
	archiveDir := filepath.Join(root, "archive")
	database := filepath.Join(root, "pipeline.db")
	if err := os.WriteFile(source, []byte(pipelineFixture), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := &config.Root{Snapshot: config.Snapshot{
		Source:                 source,
		Destination:            destination,
		ArchiveDir:             archiveDir,
		ImportEnabled:          true,
		PythonExe:              python,
		ImporterScript:         importer,
		DatabaseURL:            "sqlite:///" + filepath.ToSlash(database),
		ClearSourceAfterImport: true,
	}}
	log := logx.New(io.Discard, "pipeline-test", "test")
	processed, err := syncSnapshotAfterFlush(log, cfg, time.Time{})
	if err != nil {
		t.Fatal(err)
	}
	if err := clearProcessedSnapshotSources(log, cfg, map[string]string{
		processed.Source: processed.SnapshotSHA256,
	}); err != nil {
		t.Fatal(err)
	}
	reset, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(reset), "Test Item") || !strings.Contains(string(reset), `["lastScanTime"] = 0`) {
		t.Fatalf("source was not compacted: %q", reset)
	}
	archives, err := filepath.Glob(filepath.Join(archiveDir, "*.tgz"))
	if err != nil || len(archives) != 1 {
		t.Fatalf("archives=%v err=%v", archives, err)
	}
	if info, err := os.Stat(database); err != nil || info.Size() == 0 {
		t.Fatalf("database was not created: info=%v err=%v", info, err)
	}
}

func TestDiscoverSnapshotSourcePrefersNewestCharacterFile(t *testing.T) {
	root := t.TempDir()
	retail := filepath.Join(root, "_retail_")
	legacy := filepath.Join(retail, "WTF", "Account", "ACCOUNT#1", "SavedVariables", snapshotFileName)
	character := filepath.Join(
		retail, "WTF", "Account", "ACCOUNT#1", "Realm", "Character", "SavedVariables", snapshotFileName,
	)
	for _, path := range []string{legacy, character} {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(pipelineFixture), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	now := time.Now()
	if err := os.Chtimes(legacy, now.Add(-time.Minute), now.Add(-time.Minute)); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(character, now, now); err != nil {
		t.Fatal(err)
	}
	cfg := &config.Root{Snapshot: config.Snapshot{RetailRoot: retail, Account: "ACCOUNT#1"}}
	got, err := discoverSnapshotSource(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if got != character {
		t.Fatalf("source=%q, want character file %q", got, character)
	}
}

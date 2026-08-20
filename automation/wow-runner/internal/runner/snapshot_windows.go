//go:build windows

package runner

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/snapshot"
)

const snapshotFileName = "AuctionSearchExample.lua"

type importerResult struct {
	OK                   bool    `json:"ok"`
	Error                string  `json:"error"`
	SnapshotSHA256       string  `json:"snapshot_sha256"`
	SourceScanCount      int     `json:"source_scan_count"`
	ImportedScanCount    int     `json:"imported_scan_count"`
	SkippedScanCount     int     `json:"skipped_scan_count"`
	ImportedListingCount int64   `json:"imported_listing_count"`
	ExistingListingCount int64   `json:"existing_listing_count"`
	DuplicateSnapshot    bool    `json:"duplicate_snapshot"`
	ScanIDs              []int64 `json:"scan_ids"`
}

type processedSnapshot struct {
	Source         string
	SnapshotSHA256 string
}

func importSnapshotToDatabase(cfg *config.Root, source, expectedSHA string, expectedScans int) (importerResult, error) {
	python := cfg.ResolveSnapshotPath(cfg.Snapshot.PythonExe)
	importer := cfg.ResolveSnapshotPath(cfg.Snapshot.ImporterScript)
	if info, err := os.Stat(python); err != nil || !info.Mode().IsRegular() {
		return importerResult{}, fmt.Errorf("snapshot python executable is not a file: %q", python)
	}
	if info, err := os.Stat(importer); err != nil || !info.Mode().IsRegular() {
		return importerResult{}, fmt.Errorf("snapshot importer script is not a file: %q", importer)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, python, importer, source, "--json")
	cmd.Dir = filepath.Dir(importer)
	cmd.Env = os.Environ()
	if databaseURL := strings.TrimSpace(cfg.Snapshot.DatabaseURL); databaseURL != "" {
		cmd.Env = append(cmd.Env, "DATABASE_URL="+databaseURL)
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	output, err := cmd.Output()
	if ctx.Err() != nil {
		return importerResult{}, fmt.Errorf("snapshot importer timed out after 30 minutes")
	}
	if err != nil {
		return importerResult{}, fmt.Errorf("snapshot importer failed: %w: %s", err, strings.TrimSpace(stderr.String()))
	}
	var result importerResult
	if err := json.Unmarshal(output, &result); err != nil {
		return importerResult{}, fmt.Errorf("decode snapshot importer JSON: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if !result.OK {
		return importerResult{}, fmt.Errorf("snapshot importer rejected data: %s", result.Error)
	}
	if result.SnapshotSHA256 != expectedSHA {
		return importerResult{}, fmt.Errorf("database imported SHA %s, expected %s", result.SnapshotSHA256, expectedSHA)
	}
	if result.SourceScanCount != expectedScans || result.ImportedScanCount+result.SkippedScanCount != expectedScans {
		return importerResult{}, fmt.Errorf(
			"database scan reconciliation failed: source=%d imported=%d skipped=%d expected=%d",
			result.SourceScanCount, result.ImportedScanCount, result.SkippedScanCount, expectedScans,
		)
	}
	if len(result.ScanIDs) != expectedScans {
		return importerResult{}, fmt.Errorf("database returned %d scan IDs, expected %d", len(result.ScanIDs), expectedScans)
	}
	return result, nil
}

func candidateRetailRoots(cfg *config.Root) []string {
	if root := strings.TrimSpace(cfg.Snapshot.RetailRoot); root != "" {
		return []string{cfg.ResolveSnapshotPath(root)}
	}
	roots := make([]string, 0, 26)
	for drive := 'A'; drive <= 'Z'; drive++ {
		root := fmt.Sprintf("%c:\\World of Warcraft", drive)
		if info, err := os.Stat(root); err == nil && info.IsDir() {
			roots = append(roots, root)
		}
	}
	return roots
}

func discoverSnapshotSource(cfg *config.Root) (string, error) {
	if source := strings.TrimSpace(cfg.Snapshot.Source); source != "" {
		resolved := cfg.ResolveSnapshotPath(source)
		if info, err := os.Stat(resolved); err != nil || !info.Mode().IsRegular() {
			return "", fmt.Errorf("configured snapshot.source is not a file: %q", resolved)
		}
		return resolved, nil
	}

	accountFilter := strings.ToLower(strings.TrimSpace(cfg.Snapshot.Account))
	type candidate struct {
		path    string
		account string
		mtime   time.Time
	}
	var candidates []candidate
	for _, root := range candidateRetailRoots(cfg) {
		retail := root
		if filepath.Base(filepath.Clean(root)) != "_retail_" {
			retail = filepath.Join(root, "_retail_")
		}
		accountsDir := filepath.Join(retail, "WTF", "Account")
		patterns := []string{
			// 2.6+：角色级 SavedVariables。
			filepath.Join(accountsDir, "*", "*", "*", "SavedVariables", snapshotFileName),
			// 兼容旧版账号级 SavedVariables，便于一次性迁移。
			filepath.Join(accountsDir, "*", "SavedVariables", snapshotFileName),
		}
		for _, pattern := range patterns {
			matches, _ := filepath.Glob(pattern)
			for _, path := range matches {
				relative, relErr := filepath.Rel(accountsDir, path)
				if relErr != nil {
					continue
				}
				parts := strings.Split(relative, string(filepath.Separator))
				if len(parts) < 3 {
					continue
				}
				account := parts[0]
				if accountFilter != "" && !strings.Contains(strings.ToLower(account), accountFilter) {
					continue
				}
				info, err := os.Stat(path)
				if err == nil && info.Mode().IsRegular() {
					candidates = append(candidates, candidate{path: path, account: account, mtime: info.ModTime()})
				}
			}
		}
	}
	if len(candidates) == 0 {
		return "", fmt.Errorf("no %s found; set snapshot.source or snapshot.retail_root/account", snapshotFileName)
	}
	if accountFilter == "" {
		accounts := map[string]bool{}
		for _, candidate := range candidates {
			accounts[strings.ToLower(candidate.account)] = true
		}
		if len(accounts) > 1 {
			return "", fmt.Errorf("multiple WoW accounts contain snapshots; set snapshot.account explicitly")
		}
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].mtime.After(candidates[j].mtime) })
	return candidates[0].path, nil
}

func syncSnapshotAfterFlush(log *logx.Logger, cfg *config.Root, scanTrigger time.Time) (processedSnapshot, error) {
	source, err := discoverSnapshotSource(cfg)
	if err != nil {
		return processedSnapshot{}, err
	}
	destination := cfg.ResolveSnapshotPath(cfg.Snapshot.Destination)
	if destination == "" {
		return processedSnapshot{}, fmt.Errorf("snapshot.destination is required")
	}

	var report snapshot.Report
	deadline := time.Now().Add(20 * time.Second)
	for {
		report, err = snapshot.SyncAndValidateWithCheck(source, destination, scanTrigger, validateSnapshotCompleteness)
		if err == nil || time.Now().After(deadline) {
			break
		}
		if errors.Is(err, snapshot.ErrInvalidSnapshot) {
			break
		}
		// A normal process exit usually implies the file is closed already, but
		// antivirus/filesystem filters can delay visibility briefly.
		time.Sleep(500 * time.Millisecond)
	}
	if err != nil {
		return processedSnapshot{}, fmt.Errorf("sync and validate SavedVariables: %w", err)
	}
	meta := report.LastScan
	level := "INFO"
	if meta.LinkedItemCount < meta.ItemCount || meta.IncompleteInfoCount > 0 {
		level = "WARN"
	}
	log.Emit(level, "snapshot_validated", "SavedVariables copied and independently validated", map[string]any{
		"source": source, "destination": destination, "last_scan_time": report.LastScanTime,
		"item_count": meta.ItemCount, "record_count": meta.RecordCount,
		"actual_item_count": report.Latest.ActualItemCount, "linked_item_count": meta.LinkedItemCount,
		"incomplete_info_count": meta.IncompleteInfoCount, "missing_core_count": meta.MissingCoreCount,
		"api_error_count": meta.APIErrorCount,
	})

	archive, err := snapshot.ArchiveValidatedSnapshot(destination, cfg.ResolveSnapshotPath(cfg.Snapshot.ArchiveDir), report)
	if err != nil {
		return processedSnapshot{}, fmt.Errorf("archive validated snapshot: %w", err)
	}
	log.Emit("INFO", "snapshot_archived", "validated snapshot compressed and archived", map[string]any{
		"archive": archive.ArchivePath, "manifest": archive.ManifestPath,
		"snapshot_sha256": archive.SnapshotSHA256, "archive_sha256": archive.ArchiveSHA256,
		"source_size": archive.SourceSize,
	})

	if !cfg.Snapshot.ImportEnabled {
		return processedSnapshot{Source: source, SnapshotSHA256: archive.SnapshotSHA256}, nil
	}
	imported, err := importSnapshotToDatabase(cfg, destination, archive.SnapshotSHA256, len(report.Scans))
	if err != nil {
		return processedSnapshot{}, fmt.Errorf("import validated snapshot: %w", err)
	}
	log.Emit("INFO", "snapshot_imported", "snapshot appended to website database and reconciled", map[string]any{
		"snapshot_sha256":        imported.SnapshotSHA256,
		"source_scan_count":      imported.SourceScanCount,
		"imported_scan_count":    imported.ImportedScanCount,
		"skipped_scan_count":     imported.SkippedScanCount,
		"imported_listing_count": imported.ImportedListingCount,
		"existing_listing_count": imported.ExistingListingCount,
		"duplicate_snapshot":     imported.DuplicateSnapshot,
		"scan_ids":               imported.ScanIDs,
	})

	return processedSnapshot{Source: source, SnapshotSHA256: archive.SnapshotSHA256}, nil
}

func clearProcessedSnapshotSources(log *logx.Logger, cfg *config.Root, processed map[string]string) error {
	if !cfg.Snapshot.ClearSourceAfterImport {
		return nil
	}
	paths := make([]string, 0, len(processed))
	for source := range processed {
		paths = append(paths, source)
	}
	sort.Strings(paths)
	for _, source := range paths {
		expectedSHA := processed[source]
		if err := snapshot.ResetSavedVariablesIfMatch(source, expectedSHA); err != nil {
			return fmt.Errorf("clear imported WoW SavedVariables %q: %w", source, err)
		}
		log.Emit("INFO", "snapshot_source_cleared", "character SavedVariables compacted after all imports", map[string]any{
			"source": source, "snapshot_sha256": expectedSHA,
		})
	}
	return nil
}

func validateSnapshotCompleteness(report snapshot.Report) error {
	meta := report.LastScan
	if meta.ItemCount <= 0 || meta.RecordCount != meta.ItemCount || report.Latest.ActualItemCount != meta.ItemCount {
		return fmt.Errorf(
			"snapshot completeness failed: itemCount=%d recordCount=%d actual=%d",
			meta.ItemCount, meta.RecordCount, report.Latest.ActualItemCount,
		)
	}
	if meta.MissingCoreCount != 0 || meta.APIErrorCount != 0 {
		return fmt.Errorf(
			"snapshot core data incomplete: missingCoreCount=%d apiErrorCount=%d",
			meta.MissingCoreCount, meta.APIErrorCount,
		)
	}
	return nil
}

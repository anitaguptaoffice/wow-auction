//go:build windows

package runner

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/snapshot"
)

const snapshotFileName = "AuctionSearchExample.lua"

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
		pattern := filepath.Join(retail, "WTF", "Account", "*", "SavedVariables", snapshotFileName)
		matches, _ := filepath.Glob(pattern)
		for _, path := range matches {
			account := filepath.Base(filepath.Dir(filepath.Dir(path)))
			if accountFilter != "" && !strings.Contains(strings.ToLower(account), accountFilter) {
				continue
			}
			info, err := os.Stat(path)
			if err == nil && info.Mode().IsRegular() {
				candidates = append(candidates, candidate{path: path, account: account, mtime: info.ModTime()})
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

func syncSnapshotAfterExit(log *logx.Logger, cfg *config.Root, scanTrigger time.Time) error {
	source, err := discoverSnapshotSource(cfg)
	if err != nil {
		return err
	}
	destination := cfg.ResolveSnapshotPath(cfg.Snapshot.Destination)
	if destination == "" {
		return fmt.Errorf("snapshot.destination is required")
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
		return fmt.Errorf("sync and validate SavedVariables: %w", err)
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

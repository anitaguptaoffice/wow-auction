//go:build windows

package runner

import (
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
)

// IngestLatestSnapshot discovers the newest account/character SavedVariables,
// validates, archives and imports it without controlling WoW or clearing the
// source. It is intended for a human-driven game session after /logout.
func IngestLatestSnapshot(log *logx.Logger, cfg *config.Root) error {
	_, err := syncSnapshotAfterFlush(log, cfg, time.Time{})
	return err
}

// Package runner executes FSM steps toward DEVELOPMENT_PLAN.md §4.
package runner

import (
	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
)

// RunFSM performs INIT → BNET_START → WOW_FOREGROUND (first milestones). Platform-specific.
func RunFSM(log *logx.Logger, cfg *config.Root) error {
	return runPlatform(log, cfg)
}

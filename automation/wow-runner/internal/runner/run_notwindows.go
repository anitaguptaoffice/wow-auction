//go:build !windows

package runner

import (
	"runtime"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
)

func runPlatform(log *logx.Logger, _ *config.Root) error {
	log.Emit("WARN", "transition", "RunFSM skipped on "+runtime.GOOS, map[string]any{
		"from_state": "INIT",
		"to_state":   "INIT",
		"trigger":    "unsupported_os",
	})
	return nil
}

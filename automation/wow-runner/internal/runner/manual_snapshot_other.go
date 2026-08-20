//go:build !windows

package runner

import (
	"fmt"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
)

func IngestLatestSnapshot(_ *logx.Logger, _ *config.Root) error {
	return fmt.Errorf("manual snapshot ingestion is only supported on Windows")
}

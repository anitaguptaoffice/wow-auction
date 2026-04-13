//go:build !windows

package proc

import (
	"fmt"
	"time"
)

func StartExeDetached(exePath string) error {
	_ = exePath
	return fmt.Errorf("proc.StartExeDetached: not supported on this platform")
}

func WaitForExe(wantExe string, deadline time.Time) ([]int32, error) {
	_, _ = wantExe, deadline
	return nil, fmt.Errorf("proc.WaitForExe: not supported on this platform")
}

func WaitProcessGone(pid int32, deadline time.Time) error {
	_, _ = pid, deadline
	return fmt.Errorf("proc.WaitProcessGone: not supported on this platform")
}

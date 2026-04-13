//go:build windows

package proc

import (
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/shirou/gopsutil/v3/process"
)

// StartExeDetached starts an executable without waiting for it to exit (GUI launcher).
func StartExeDetached(exePath string) error {
	exePath = strings.TrimSpace(exePath)
	if exePath == "" {
		return fmt.Errorf("empty exe path")
	}
	cmd := exec.Command(exePath)
	cmd.Dir = filepath.Dir(exePath)
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start %s: %w", exePath, err)
	}
	return nil
}

// WaitForExe polls until wantExe appears among running processes or deadline passes.
func WaitForExe(wantExe string, deadline time.Time) ([]int32, error) {
	wantExe = strings.TrimSpace(wantExe)
	if wantExe == "" {
		return nil, fmt.Errorf("empty exe name")
	}
	for time.Now().Before(deadline) {
		pids, err := PIDsByExe(wantExe)
		if err != nil {
			return nil, err
		}
		if len(pids) > 0 {
			return pids, nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	return nil, fmt.Errorf("timeout waiting for process %s", wantExe)
}

// WaitProcessGone polls until pid no longer exists or deadline.
func WaitProcessGone(pid int32, deadline time.Time) error {
	for time.Now().Before(deadline) {
		exists, err := process.PidExists(int32(pid))
		if err != nil {
			return err
		}
		if !exists {
			return nil
		}
		time.Sleep(300 * time.Millisecond)
	}
	return fmt.Errorf("pid %d still alive after deadline", pid)
}

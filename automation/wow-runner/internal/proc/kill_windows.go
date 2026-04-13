//go:build windows

package proc

import (
	"fmt"

	"golang.org/x/sys/windows"
)

// KillPID terminates a process by PID (TerminateProcess).
func KillPID(pid int32) error {
	if pid <= 0 {
		return fmt.Errorf("invalid pid")
	}
	h, err := windows.OpenProcess(windows.PROCESS_TERMINATE, false, uint32(pid))
	if err != nil {
		return fmt.Errorf("OpenProcess: %w", err)
	}
	defer windows.CloseHandle(h)
	if err := windows.TerminateProcess(h, 1); err != nil {
		return fmt.Errorf("TerminateProcess: %w", err)
	}
	return nil
}

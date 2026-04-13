//go:build !windows

package proc

import "fmt"

// KillPID is only implemented on Windows for wow-runner.
func KillPID(pid int32) error {
	_ = pid
	return fmt.Errorf("proc.KillPID: not supported on this platform")
}

// Package proc finds OS processes by executable name (for Wow.exe / Battle.net.exe).
package proc

import (
	"fmt"
	"strings"

	"github.com/shirou/gopsutil/v3/process"
)

// PIDsByExe returns PIDs whose executable name matches want (e.g. "Wow.exe").
// Matching is case-insensitive; "Wow" matches "Wow.exe" on Windows.
func PIDsByExe(want string) ([]int32, error) {
	want = strings.TrimSpace(want)
	if want == "" {
		return nil, fmt.Errorf("empty executable name")
	}
	procs, err := process.Processes()
	if err != nil {
		return nil, err
	}
	var out []int32
	for _, p := range procs {
		name, err := p.Name()
		if err != nil {
			continue
		}
		if exeMatch(want, name) {
			out = append(out, p.Pid)
		}
	}
	return out, nil
}

func exeMatch(want, got string) bool {
	want = strings.ToLower(strings.TrimSpace(want))
	got = strings.ToLower(strings.TrimSpace(got))
	if got == want {
		return true
	}
	// "wow" <-> "wow.exe"
	if !strings.Contains(want, ".") && got == want+".exe" {
		return true
	}
	if !strings.Contains(got, ".") && want == got+".exe" {
		return true
	}
	return false
}

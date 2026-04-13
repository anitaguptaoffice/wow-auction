//go:build !windows

package winutil

import "fmt"

// HWND is a placeholder when not on Windows.
type HWND = uintptr

// ErrNotWindows is returned when winutil is used off Windows.
var ErrNotWindows = fmt.Errorf("winutil: only supported on windows")

func FindTopLevelVisibleHWND(pid uint32) HWND {
	return 0
}

func FocusWindow(hwnd HWND) error {
	return ErrNotWindows
}

func Click(x, y int32) error {
	return ErrNotWindows
}

func KeyTap(vk uint16) error {
	return ErrNotWindows
}

//go:build windows

package winutil

import (
	"fmt"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

// HWND is a Windows window handle (uintptr for cross-package builds).
type HWND = uintptr

// RECT matches Win32 RECT for GetClientRect.
type RECT struct {
	Left, Top, Right, Bottom int32
}

// POINT matches Win32 POINT for ClientToScreen.
type POINT struct {
	X, Y int32
}

var (
	user32                       = windows.NewLazySystemDLL("user32.dll")
	procEnumWindows              = user32.NewProc("EnumWindows")
	procGetWindowThreadProcessId = user32.NewProc("GetWindowThreadProcessId")
	procIsWindowVisible          = user32.NewProc("IsWindowVisible")
	procGetParent                = user32.NewProc("GetParent")
	procGetClientRect            = user32.NewProc("GetClientRect")
	procClientToScreen           = user32.NewProc("ClientToScreen")
	procSetForegroundWindow      = user32.NewProc("SetForegroundWindow")
	procShowWindow               = user32.NewProc("ShowWindow")
	procSetCursorPos             = user32.NewProc("SetCursorPos")
	procMouseEvent               = user32.NewProc("mouse_event")
	procKeybdEvent               = user32.NewProc("keybd_event")
)

const (
	swRestore           = 9
	mouseeventfLeftDown = 0x0002
	mouseeventfLeftUp   = 0x0004
	keyeventfKeyUp      = 0x0002
)

// FindTopLevelVisibleHWND returns the first top-level visible window for pid, or 0.
func FindTopLevelVisibleHWND(pid uint32) HWND {
	var found HWND
	cb := syscall.NewCallback(func(hwnd uintptr, _ uintptr) uintptr {
		var winPID uint32
		procGetWindowThreadProcessId.Call(hwnd, uintptr(unsafe.Pointer(&winPID)), 0)
		if winPID != pid {
			return 1
		}
		r, _, _ := procGetParent.Call(hwnd)
		if r != 0 {
			return 1
		}
		v, _, _ := procIsWindowVisible.Call(hwnd)
		if v == 0 {
			return 1
		}
		found = HWND(hwnd)
		return 0 // stop enumeration
	})
	procEnumWindows.Call(cb, 0)
	return found
}

// ClientAreaScreenBounds returns the client area in screen pixels: origin (left, top) and size (width, height).
func ClientAreaScreenBounds(hwnd HWND) (left, top, width, height int32, err error) {
	if hwnd == 0 {
		return 0, 0, 0, 0, fmt.Errorf("null hwnd")
	}
	var r RECT
	ok, _, e := procGetClientRect.Call(hwnd, uintptr(unsafe.Pointer(&r)))
	if ok == 0 {
		return 0, 0, 0, 0, fmt.Errorf("GetClientRect: %w", e)
	}
	width = r.Right - r.Left
	height = r.Bottom - r.Top
	var pt POINT
	ok, _, e = procClientToScreen.Call(hwnd, uintptr(unsafe.Pointer(&pt)))
	if ok == 0 {
		return 0, 0, 0, 0, fmt.Errorf("ClientToScreen: %w", e)
	}
	return pt.X, pt.Y, width, height, nil
}

// FocusWindow tries to restore and foreground the window.
func FocusWindow(hwnd HWND) error {
	if hwnd == 0 {
		return fmt.Errorf("null hwnd")
	}
	procShowWindow.Call(hwnd, swRestore)
	r, _, err := procSetForegroundWindow.Call(hwnd)
	if r == 0 {
		return fmt.Errorf("SetForegroundWindow: %w", err)
	}
	return nil
}

// Click performs a left click at screen coordinates.
func Click(x, y int32) error {
	r, _, err := procSetCursorPos.Call(uintptr(x), uintptr(y))
	if r == 0 {
		return fmt.Errorf("SetCursorPos: %w", err)
	}
	procMouseEvent.Call(mouseeventfLeftDown, 0, 0, 0, 0)
	procMouseEvent.Call(mouseeventfLeftUp, 0, 0, 0, 0)
	return nil
}

// KeyTap presses and releases a virtual key (see input.VK).
func KeyTap(vk uint16) error {
	procKeybdEvent.Call(uintptr(vk), 0, 0, 0)
	procKeybdEvent.Call(uintptr(vk), 0, keyeventfKeyUp, 0)
	return nil
}

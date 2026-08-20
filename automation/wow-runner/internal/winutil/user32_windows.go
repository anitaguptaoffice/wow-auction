//go:build windows

package winutil

import (
	"fmt"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"

	lxnwin "github.com/lxn/win"
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
	procGetForegroundWindow      = user32.NewProc("GetForegroundWindow")
	procShowWindow               = user32.NewProc("ShowWindow")
	procSetCursorPos             = user32.NewProc("SetCursorPos")
	procMouseEvent               = user32.NewProc("mouse_event")
	procSetProcessDPIAware       = user32.NewProc("SetProcessDPIAware")
	procSetProcessDPIContext     = user32.NewProc("SetProcessDpiAwarenessContext")
)

const (
	swRestore           = 9
	mouseeventfLeftDown = 0x0002
	mouseeventfLeftUp   = 0x0004
	mouseeventfWheel    = 0x0800
)

// DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is the pseudo-handle -4.
const dpiAwarenessContextPerMonitorV2 = ^uintptr(3)

func init() {
	// The machine may use 200% scaling. Without per-monitor DPI awareness,
	// GetClientRect/ClientToScreen return virtualized coordinates while the
	// screenshot backend uses physical pixels, cropping only half the window.
	if err := procSetProcessDPIContext.Find(); err == nil {
		if ok, _, _ := procSetProcessDPIContext.Call(dpiAwarenessContextPerMonitorV2); ok != 0 {
			return
		}
	}
	procSetProcessDPIAware.Call()
}

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

// FindLargestTopLevelVisibleHWND returns the largest visible top-level client window
// owned by one of pids. Battle.net uses several helper processes, so taking pids[0]
// is not sufficient to identify the actual launcher window.
func FindLargestTopLevelVisibleHWND(pids []int32) (int32, HWND) {
	wanted := make(map[uint32]int32, len(pids))
	for _, pid := range pids {
		if pid > 0 {
			wanted[uint32(pid)] = pid
		}
	}
	var bestPID int32
	var best HWND
	var bestArea int64
	cb := syscall.NewCallback(func(hwnd uintptr, _ uintptr) uintptr {
		var winPID uint32
		procGetWindowThreadProcessId.Call(hwnd, uintptr(unsafe.Pointer(&winPID)), 0)
		pid, ok := wanted[winPID]
		if !ok {
			return 1
		}
		parent, _, _ := procGetParent.Call(hwnd)
		visible, _, _ := procIsWindowVisible.Call(hwnd)
		if parent != 0 || visible == 0 {
			return 1
		}
		var rect RECT
		okRect, _, _ := procGetClientRect.Call(hwnd, uintptr(unsafe.Pointer(&rect)))
		if okRect == 0 {
			return 1
		}
		area := int64(rect.Right-rect.Left) * int64(rect.Bottom-rect.Top)
		if area > bestArea {
			bestArea = area
			bestPID = pid
			best = HWND(hwnd)
		}
		return 1
	})
	procEnumWindows.Call(cb, 0)
	return bestPID, best
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

// FocusAndVerify restores hwnd, brings it to foreground and verifies input will
// be delivered to the intended window.
func FocusAndVerify(hwnd HWND) error {
	if err := FocusWindow(hwnd); err != nil {
		return err
	}
	time.Sleep(60 * time.Millisecond)
	foreground, _, _ := procGetForegroundWindow.Call()
	if HWND(foreground) != hwnd {
		return fmt.Errorf("foreground window mismatch: got %d, want %d", foreground, hwnd)
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

// MouseWheel injects one or more wheel notches into the foreground window.
// Positive delta scrolls up; negative delta scrolls down. One notch is 120.
func MouseWheel(delta int32) error {
	if delta == 0 {
		return fmt.Errorf("zero mouse wheel delta")
	}
	procMouseEvent.Call(mouseeventfWheel, 0, 0, uintptr(uint32(delta)), 0)
	return nil
}

// KeyTap presses and releases a virtual key (see input.VK).
func KeyTap(vk uint16) error {
	inputs := []lxnwin.KEYBD_INPUT{
		{Type: lxnwin.INPUT_KEYBOARD, Ki: lxnwin.KEYBDINPUT{WVk: vk}},
		{Type: lxnwin.INPUT_KEYBOARD, Ki: lxnwin.KEYBDINPUT{WVk: vk, DwFlags: lxnwin.KEYEVENTF_KEYUP}},
	}
	return sendKeyboardInputs(inputs)
}

// KeyChord presses modifiers in order, taps key, then releases modifiers in
// reverse order so WoW receives combinations such as ALT-CTRL-H atomically.
func KeyChord(modifiers []uint16, key uint16) error {
	if key == 0 {
		return fmt.Errorf("zero primary key")
	}
	inputs := make([]lxnwin.KEYBD_INPUT, 0, len(modifiers)*2+2)
	for _, modifier := range modifiers {
		inputs = append(inputs, lxnwin.KEYBD_INPUT{
			Type: lxnwin.INPUT_KEYBOARD, Ki: lxnwin.KEYBDINPUT{WVk: modifier},
		})
	}
	inputs = append(inputs,
		lxnwin.KEYBD_INPUT{Type: lxnwin.INPUT_KEYBOARD, Ki: lxnwin.KEYBDINPUT{WVk: key}},
		lxnwin.KEYBD_INPUT{Type: lxnwin.INPUT_KEYBOARD, Ki: lxnwin.KEYBDINPUT{WVk: key, DwFlags: lxnwin.KEYEVENTF_KEYUP}},
	)
	for index := len(modifiers) - 1; index >= 0; index-- {
		inputs = append(inputs, lxnwin.KEYBD_INPUT{
			Type: lxnwin.INPUT_KEYBOARD,
			Ki:   lxnwin.KEYBDINPUT{WVk: modifiers[index], DwFlags: lxnwin.KEYEVENTF_KEYUP},
		})
	}
	return sendKeyboardInputs(inputs)
}

// SendText injects UTF-16 text using KEYEVENTF_UNICODE. It is independent of
// the active keyboard layout and is therefore reliable for WoW slash commands.
func SendText(value string) error {
	units := utf16.Encode([]rune(value))
	if len(units) == 0 {
		return nil
	}
	inputs := make([]lxnwin.KEYBD_INPUT, 0, len(units)*2)
	for _, unit := range units {
		inputs = append(inputs,
			lxnwin.KEYBD_INPUT{Type: lxnwin.INPUT_KEYBOARD, Ki: lxnwin.KEYBDINPUT{WScan: unit, DwFlags: lxnwin.KEYEVENTF_UNICODE}},
			lxnwin.KEYBD_INPUT{Type: lxnwin.INPUT_KEYBOARD, Ki: lxnwin.KEYBDINPUT{WScan: unit, DwFlags: lxnwin.KEYEVENTF_UNICODE | lxnwin.KEYEVENTF_KEYUP}},
		)
	}
	return sendKeyboardInputs(inputs)
}

func sendKeyboardInputs(inputs []lxnwin.KEYBD_INPUT) error {
	if len(inputs) == 0 {
		return nil
	}
	sent := lxnwin.SendInput(uint32(len(inputs)), unsafe.Pointer(&inputs[0]), int32(unsafe.Sizeof(inputs[0])))
	if sent != uint32(len(inputs)) {
		return fmt.Errorf("SendInput sent %d of %d events", sent, len(inputs))
	}
	return nil
}

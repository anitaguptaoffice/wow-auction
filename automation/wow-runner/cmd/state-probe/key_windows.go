//go:build windows

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"golang.org/x/sys/windows"
	"wow-auction/automation/wow-runner/internal/winutil"
)

// Explicit test only: requires a fresh native-state match, a background window,
// and released modifiers. Never focuses the game, enters the world, or retries.
func selectionKeyStep(h windows.Handle, pid uint32, path, name, realm, out, key, expected string) error {
	keys := map[string]uintptr{"Up": 0x26, "Down": 0x28}
	vk, ok := keys[key]
	if !ok || expected == "" {
		return fmt.Errorf("key must be Up/Down and expect-guid is required")
	}
	if _, e := os.Stat(out); !os.IsNotExist(e) {
		return fmt.Errorf("output already exists or is inaccessible")
	}
	if e := snapshotNative(h, pid, path, name, realm, out+".before.json"); e != nil {
		return e
	}
	b, e := os.ReadFile(out + ".before.json")
	if e != nil {
		return e
	}
	var before struct {
		Snapshot selectionSnapshot `json:"snapshot"`
		Selected *characterRow     `json:"selectedCharacter"`
	}
	if e = json.Unmarshal(b, &before); e != nil {
		return e
	}
	if !before.Snapshot.InCharacterSelect || before.Selected == nil || before.Selected.GUID != expected || before.Selected.Name != name || before.Selected.RealmName != realm {
		return fmt.Errorf("selected character no longer matches; no key sent")
	}
	u := windows.NewLazySystemDLL("user32.dll")
	fg := u.NewProc("GetForegroundWindow")
	post := u.NewProc("PostMessageW")
	mapping := u.NewProc("MapVirtualKeyW")
	async := u.NewProc("GetAsyncKeyState")
	hwnd := winutil.FindTopLevelVisibleHWND(pid)
	if hwnd == 0 {
		return fmt.Errorf("no visible game window")
	}
	foregroundBefore, _, _ := fg.Call()
	if foregroundBefore == hwnd {
		return fmt.Errorf("game is foreground; no background test key sent")
	}
	for _, v := range []uintptr{0x10, 0x11, 0x12, 0x5b, 0x5c} {
		state, _, _ := async.Call(v)
		if state&0x8000 != 0 {
			return fmt.Errorf("modifier is held; no key sent")
		}
	}
	scan, _, _ := mapping.Call(vk, 0)
	flags := uintptr(1) | (scan << 16) | (1 << 24)
	start := time.Now()
	down, _, downErr := post.Call(hwnd, 0x100, vk, flags)
	time.Sleep(50 * time.Millisecond)
	up, _, upErr := post.Call(hwnd, 0x101, vk, flags|0xc0000000)
	foregroundAfter, _, _ := fg.Call()
	result := map[string]any{"time": start.Format(time.RFC3339Nano), "pid": pid, "hwnd": hwnd, "key": key, "expectedGUID": expected, "foregroundBefore": foregroundBefore, "foregroundAfter": foregroundAfter, "focusUnchanged": foregroundBefore == foregroundAfter, "downQueued": down != 0, "upQueued": up != 0, "received": "not_yet_confirmed"}
	if down == 0 {
		result["downError"] = downErr.Error()
	}
	if up == 0 {
		result["upError"] = upErr.Error()
	}
	// Retain send evidence even when the subsequent read fails.
	f, e := os.OpenFile(out, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	e = json.NewEncoder(f).Encode(result)
	f.Close()
	if e != nil {
		return e
	}
	if down == 0 || up == 0 {
		return fmt.Errorf("message queue failure; no retry")
	}
	time.Sleep(1200 * time.Millisecond)
	return snapshotNative(h, pid, path, name, realm, out+".after.json")
}

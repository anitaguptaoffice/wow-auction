//go:build windows

package main

import (
	"context"
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/sys/windows"
)

func forceKillGame(ctx context.Context, exe string) error {
	pid, e := findGame(exe)
	if e != nil {
		return e
	}
	if pid == 0 {
		return nil
	}
	h, e := windows.OpenProcess(windows.PROCESS_TERMINATE|windows.PROCESS_QUERY_LIMITED_INFORMATION|windows.SYNCHRONIZE, false, pid)
	if e != nil {
		return e
	}
	defer windows.CloseHandle(h)
	path := make([]uint16, 32768)
	n := uint32(len(path))
	if e = windows.QueryFullProcessImageName(h, 0, &path[0], &n); e != nil {
		return e
	}
	if !strings.EqualFold(filepath.Clean(windows.UTF16ToString(path[:n])), filepath.Clean(exe)) {
		return fmt.Errorf("process identity changed; no termination")
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if e = windows.TerminateProcess(h, 1); e != nil {
		return e
	}
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		v, e := windows.WaitForSingleObject(h, 1000)
		if e != nil {
			return e
		}
		if v == windows.WAIT_OBJECT_0 {
			return nil
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
	}
	return fmt.Errorf("client termination timed out")
}

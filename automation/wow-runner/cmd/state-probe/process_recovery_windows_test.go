//go:build windows

package main

import (
	"context"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

func TestRecoveryDummyProcess(t *testing.T) {
	if os.Getenv("WOW_RECOVERY_DUMMY_CHILD") != "1" {
		t.Skip("child process only")
	}
	time.Sleep(30 * time.Second)
}

func TestForceKillTargetsOnlyMatchingOwnedDummyProcess(t *testing.T) {
	source, e := os.Executable()
	if e != nil {
		t.Fatal(e)
	}
	in, e := os.Open(source)
	if e != nil {
		t.Fatal(e)
	}
	target := filepath.Join(t.TempDir(), "recovery-dummy.exe")
	out, e := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0700)
	if e != nil {
		in.Close()
		t.Fatal(e)
	}
	_, copyErr := io.Copy(out, in)
	in.Close()
	closeErr := out.Close()
	if copyErr != nil || closeErr != nil {
		t.Fatalf("copy: %v %v", copyErr, closeErr)
	}
	cmd := exec.Command(target, "-test.run=^TestRecoveryDummyProcess$")
	cmd.Env = append(os.Environ(), "WOW_RECOVERY_DUMMY_CHILD=1")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	if e = cmd.Start(); e != nil {
		t.Fatal(e)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	defer func() { _ = cmd.Process.Kill() }()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if e = forceKillGame(ctx, target); e != nil {
		t.Fatal(e)
	}
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("owned dummy process still running")
	}
}

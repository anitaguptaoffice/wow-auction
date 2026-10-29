//go:build windows

package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func readTestSchedule(t *testing.T, root string) scheduleState {
	t.Helper()
	b, e := os.ReadFile(filepath.Join(root, "schedule.json"))
	if e != nil {
		t.Fatal(e)
	}
	var s scheduleState
	if e = json.Unmarshal(b, &s); e != nil {
		t.Fatal(e)
	}
	return s
}

func TestScheduledCompletionKeepsDeadlineAndDoesNotRestartEarly(t *testing.T) {
	root := filepath.Join(t.TempDir(), "service")
	cfg := collectConfig{Output: root, CycleIntervalSeconds: 4 * 60 * 60}
	ctx, cancel := context.WithCancel(context.Background())
	calls := 0
	e := runScheduled(ctx, cfg, func(_ context.Context, c collectConfig, progress func(int) error) error {
		calls++
		if c.Output == root {
			t.Fatal("cycle must use a distinct output directory")
		}
		if e := progress(1); e != nil {
			return e
		}
		cancel()
		return nil
	})
	if !errors.Is(e, context.Canceled) || calls != 1 {
		t.Fatalf("run: %v, calls %d", e, calls)
	}
	s := readTestSchedule(t, root)
	if s.Phase != "waiting" || s.CompletedCharacters != 1 || !s.NextCycleAt.Equal(s.CycleStartedAt.Add(4*time.Hour)) {
		t.Fatalf("bad schedule: %+v", s)
	}
	ctx2, cancel2 := context.WithTimeout(context.Background(), 80*time.Millisecond)
	defer cancel2()
	e = runScheduled(ctx2, cfg, func(context.Context, collectConfig, func(int) error) error {
		t.Fatal("started before saved deadline")
		return nil
	})
	if !errors.Is(e, context.DeadlineExceeded) {
		t.Fatalf("wait: %v", e)
	}
}

func TestFailedOrInterruptedCycleRestartsWithCooldown(t *testing.T) {
	root := filepath.Join(t.TempDir(), "service")
	cfg := collectConfig{Output: root, CycleIntervalSeconds: 14400}
	ctx, cancel := context.WithCancel(context.Background())
	e := runScheduled(ctx, cfg, func(_ context.Context, _ collectConfig, progress func(int) error) error {
		if e := progress(2); e != nil {
			return e
		}
		cancel()
		return errors.New("test failure")
	})
	if !errors.Is(e, context.Canceled) {
		t.Fatalf("pause: %v", e)
	}
	s := readTestSchedule(t, root)
	if s.Phase != "paused" || s.CompletedCharacters != 2 || s.LastError != "test failure" {
		t.Fatalf("lost progress: %+v", s)
	}
	digest, _ := scheduleFingerprint(cfg)
	if _, e := loadSchedule(root, digest, time.Now()); e != nil {
		t.Fatal(e)
	}
	s.Phase = "running"
	if e := saveSchedule(filepath.Join(root, "schedule.json"), s); e != nil {
		t.Fatal(e)
	}
	ctx2, cancel2 := context.WithTimeout(context.Background(), 120*time.Millisecond)
	defer cancel2()
	resets := 0
	e = runScheduledWithReset(ctx2, cfg, func(context.Context, collectConfig, func(int) error) error {
		t.Fatal("resumed before cooldown")
		return nil
	}, func(context.Context, string) error { resets++; return nil })
	if !errors.Is(e, context.DeadlineExceeded) || resets != 1 {
		t.Fatalf("recovery reset: %v, calls=%d", e, resets)
	}
	s = readTestSchedule(t, root)
	if s.Phase != "recovering" || !s.ClientReset || s.CompletedCharacters != 0 || !s.ArchivesReconciled || time.Until(s.RecoverAt) < 14*time.Minute {
		t.Fatalf("bad recovery state: %+v", s)
	}
	if _, e := loadSchedule(root, "different", time.Now()); e == nil {
		t.Fatal("different config accepted")
	}
}

func TestRecoveryResumesOriginalCycleWithoutResettingCadence(t *testing.T) {
	root := filepath.Join(t.TempDir(), "service")
	cfg := collectConfig{Output: root, CycleIntervalSeconds: 14400}
	if e := os.Mkdir(root, 0700); e != nil {
		t.Fatal(e)
	}
	digest, _ := scheduleFingerprint(cfg)
	started := time.Now().Add(-time.Hour).Truncate(time.Second)
	cycle := filepath.Join(root, "cycle-existing")
	initial := scheduleState{Version: 1, ConfigSHA256: digest, Phase: "recovering", CycleStartedAt: started, NextCycleAt: started, CycleDirectory: cycle, CompletedCharacters: 2, RecoverAt: time.Now().Add(-time.Second), ClientReset: true}
	if e := saveSchedule(filepath.Join(root, "schedule.json"), initial); e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithCancel(context.Background())
	resets := 0
	calls := 0
	e := runScheduledWithReset(ctx, cfg, func(_ context.Context, c collectConfig, progress func(int) error) error {
		calls++
		if c.Output != cycle || !c.CycleStart.Equal(started) {
			t.Fatal("did not reuse original cycle")
		}
		if e := progress(3); e != nil {
			return e
		}
		cancel()
		return nil
	}, func(context.Context, string) error { resets++; return nil })
	if !errors.Is(e, context.Canceled) || calls != 1 || resets != 0 {
		t.Fatalf("resume=%v calls=%d resets=%d", e, calls, resets)
	}
	s := readTestSchedule(t, root)
	if s.Phase != "waiting" || s.CompletedCharacters != 3 || !s.NextCycleAt.Equal(started.Add(4*time.Hour)) {
		t.Fatalf("wrong resumed schedule: %+v", s)
	}
}

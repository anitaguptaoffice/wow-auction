//go:build windows

package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"wow-auction/automation/wow-runner/internal/syncqueue"
)

type scheduleState struct {
	Version             int       `json:"version"`
	ConfigSHA256        string    `json:"configSha256"`
	Phase               string    `json:"phase"`
	CycleStartedAt      time.Time `json:"cycleStartedAt,omitempty"`
	NextCycleAt         time.Time `json:"nextCycleAt"`
	CycleDirectory      string    `json:"cycleDirectory,omitempty"`
	CompletedCharacters int       `json:"completedCharacters"`
	LastError           string    `json:"lastError,omitempty"`
	RecoverAt           time.Time `json:"recoverAt,omitempty"`
	ClientReset         bool      `json:"clientReset,omitempty"`
	RecoveryCount       int       `json:"recoveryCount,omitempty"`
	ArchivesReconciled  bool      `json:"archivesReconciled,omitempty"`
}

func scheduleFingerprint(cfg collectConfig) (string, error) {
	b, e := json.Marshal(cfg)
	if e != nil {
		return "", e
	}
	d := sha256.Sum256(b)
	return hex.EncodeToString(d[:]), nil
}

func saveSchedule(path string, state scheduleState) error {
	b, e := json.MarshalIndent(state, "", "  ")
	if e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".schedule-*.tmp")
	if e != nil {
		return e
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if e = f.Chmod(0600); e == nil {
		_, e = f.Write(append(b, '\n'))
	}
	if e == nil {
		e = f.Sync()
	}
	closeErr := f.Close()
	if e != nil {
		return e
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(tmp, path)
}

func loadSchedule(root, digest string, now time.Time) (scheduleState, error) {
	state := scheduleState{Version: 1, ConfigSHA256: digest, Phase: "waiting", NextCycleAt: now}
	created := false
	if e := os.Mkdir(root, 0700); e == nil {
		created = true
	} else if !os.IsExist(e) {
		return state, e
	}
	path := filepath.Join(root, "schedule.json")
	if created {
		return state, saveSchedule(path, state)
	}
	b, e := os.ReadFile(path)
	if e != nil {
		return state, fmt.Errorf("existing output lacks valid schedule: %w", e)
	}
	if e = json.Unmarshal(b, &state); e != nil {
		return state, e
	}
	if state.Version != 1 || state.ConfigSHA256 != digest || state.NextCycleAt.IsZero() {
		return state, fmt.Errorf("schedule/config mismatch")
	}
	if state.Phase != "waiting" && state.Phase != "running" && state.Phase != "paused" && state.Phase != "recovering" {
		return state, fmt.Errorf("unknown schedule state")
	}
	if state.Phase != "waiting" && (state.CycleStartedAt.IsZero() || state.CycleDirectory == "" || filepath.Clean(filepath.Dir(state.CycleDirectory)) != filepath.Clean(root)) {
		return state, fmt.Errorf("invalid recovery cycle directory")
	}
	return state, nil
}

func runScheduled(ctx context.Context, cfg collectConfig, run func(context.Context, collectConfig, func(int) error) error) error {
	return runScheduledWithReset(ctx, cfg, run, forceKillGame)
}

func runScheduledWithReset(ctx context.Context, cfg collectConfig, run func(context.Context, collectConfig, func(int) error) error, reset func(context.Context, string) error) error {
	digest, e := scheduleFingerprint(cfg)
	if e != nil {
		return e
	}
	state, e := loadSchedule(cfg.Output, digest, time.Now())
	if e != nil {
		return e
	}
	if cfg.Sync != nil && cfg.Sync.Enabled {
		workerCtx, cancel := context.WithCancel(ctx)
		defer cancel()
		workerConfig := *cfg.Sync
		workerConfig.ScanRoot = cfg.Output
		worker := syncqueue.Worker{Config: workerConfig}
		go worker.Run(workerCtx)
	}
	stateFile := filepath.Join(cfg.Output, "schedule.json")
	if state.Phase == "running" || state.Phase == "paused" {
		state.Phase = "recovering"
		state.ClientReset = false
		state.ArchivesReconciled = false
		state.RecoverAt = time.Now().Add(time.Duration(max(900, cfg.CooldownSeconds)) * time.Second)
		if e = saveSchedule(stateFile, state); e != nil {
			return e
		}
	}
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if state.Phase == "recovering" {
			if !state.ArchivesReconciled {
				count, err := countVerifiedArchives(state.CycleDirectory, cfg.Characters, state.CycleStartedAt)
				if err != nil {
					state.LastError = "archive reconciliation: " + err.Error()
					_ = saveSchedule(stateFile, state)
					if err = pause(ctx, time.Minute); err != nil {
						return err
					}
					continue
				}
				state.CompletedCharacters = count
				state.ArchivesReconciled = true
				if e = saveSchedule(stateFile, state); e != nil {
					return e
				}
			}
			if !state.ClientReset {
				if e = reset(ctx, cfg.Exe); e != nil {
					state.LastError = "client reset: " + e.Error()
					_ = saveSchedule(stateFile, state)
					if e = pause(ctx, time.Minute); e != nil {
						return e
					}
					continue
				}
				state.ClientReset = true
				state.RecoveryCount++
				if e = saveSchedule(stateFile, state); e != nil {
					return e
				}
			}
			if remaining := time.Until(state.RecoverAt); remaining > 0 {
				if e = pause(ctx, min(remaining, time.Minute)); e != nil {
					return e
				}
				continue
			}
		}
		remaining := time.Until(state.NextCycleAt)
		if state.Phase == "recovering" {
			remaining = 0
		}
		if remaining > 0 {
			fmt.Printf("等待下一轮：%s\n", state.NextCycleAt.Local().Format(time.RFC3339))
			if e = pause(ctx, min(remaining, time.Minute)); e != nil {
				return e
			}
			continue
		}
		started := time.Now()
		cycleDir := filepath.Join(cfg.Output, "cycle-"+started.Format("20060102-150405.000000000"))
		if state.Phase == "recovering" {
			started = state.CycleStartedAt
			cycleDir = state.CycleDirectory
		} else {
			state.CompletedCharacters = 0
		}
		state.Phase = "running"
		state.CycleStartedAt = started
		state.CycleDirectory = cycleDir
		state.LastError = ""
		if e = saveSchedule(stateFile, state); e != nil {
			return e
		}
		cycleCfg := cfg
		cycleCfg.Output = cycleDir
		cycleCfg.CycleStart = started
		e = run(ctx, cycleCfg, func(n int) error { state.CompletedCharacters = n; return saveSchedule(stateFile, state) })
		if e != nil {
			state.Phase = "recovering"
			state.LastError = e.Error()
			state.ClientReset = false
			state.ArchivesReconciled = false
			state.RecoverAt = time.Now().Add(time.Duration(max(900, cfg.CooldownSeconds)) * time.Second)
			if ctx.Err() != nil {
				state.Phase = "paused"
			}
			if err := saveSchedule(stateFile, state); err != nil {
				return fmt.Errorf("cycle failed (%v), recording pause failed: %w", e, err)
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			fmt.Printf("采集异常，将重启客户端并在 %s 后恢复：%v\n", state.RecoverAt.Local().Format(time.RFC3339), e)
			continue
		}
		state.Phase = "waiting"
		state.NextCycleAt = started.Add(time.Duration(cfg.CycleIntervalSeconds) * time.Second)
		if e = saveSchedule(stateFile, state); e != nil {
			return e
		}
	}
}

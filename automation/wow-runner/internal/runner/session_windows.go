//go:build windows

package runner

import (
	"fmt"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/proc"
	"wow-auction/automation/wow-runner/internal/winutil"
)

func bnetStartTimeout(cfg *config.Root) time.Duration {
	s := cfg.Timeouts.BnetStart
	if s <= 0 {
		s = 120
	}
	return time.Duration(s) * time.Second
}

func maxRetriesPerCharacter(cfg *config.Root) int {
	n := cfg.Retry.MaxRetriesPerCharacter
	if n <= 0 {
		return 3
	}
	return n
}

func maxRestartTotal(cfg *config.Root) int {
	n := cfg.Retry.MaxRestartTotal
	if n <= 0 {
		return 10
	}
	return n
}

func waitLargestVisibleWindow(exe string, deadline time.Time) (int32, winutil.HWND, error) {
	for time.Now().Before(deadline) {
		pids, err := proc.PIDsByExe(exe)
		if err != nil {
			return 0, 0, err
		}
		if pid, hwnd := winutil.FindLargestTopLevelVisibleHWND(pids); hwnd != 0 {
			return pid, hwnd, nil
		}
		time.Sleep(300 * time.Millisecond)
	}
	return 0, 0, fmt.Errorf("timeout waiting for visible window of %s", exe)
}

// effectiveIndices：single/current 只取第一个索引；all 为完整列表。
func effectiveIndices(cfg *config.Root) []int {
	if cfg.Characters.Mode == "single" || cfg.Characters.Mode == "current" {
		return []int{cfg.Characters.Indices[0]}
	}
	out := make([]int, len(cfg.Characters.Indices))
	copy(out, cfg.Characters.Indices)
	return out
}

// bootstrapWow 确保 Wow 进程存在并前台：可选启动战网、点击「进入游戏」、等待 Wow、聚焦窗口。
func bootstrapWow(log *logx.Logger, cfg *config.Root) (pid int32, hwnd winutil.HWND, err error) {
	deadline := time.Now().Add(bnetStartTimeout(cfg))
	launch := strings.TrimSpace(cfg.Process.BattleNetLaunchExe)

	bnetPids, err := proc.PIDsByExe(cfg.Process.BattleNetExe)
	if err != nil {
		return 0, 0, err
	}
	wowPids, err := proc.PIDsByExe(cfg.Process.WowExe)
	if err != nil {
		return 0, 0, err
	}

	if len(bnetPids) == 0 && launch != "" {
		log.Emit("INFO", "process_start", "launch Battle.net", map[string]any{
			"exe": launch,
		})
		if err := proc.StartExeDetached(launch); err != nil {
			return 0, 0, fmt.Errorf("launch battle.net: %w", err)
		}
		if _, err := proc.WaitForExe(cfg.Process.BattleNetExe, deadline); err != nil {
			return 0, 0, err
		}
		bnetPids, err = proc.PIDsByExe(cfg.Process.BattleNetExe)
		if err != nil {
			return 0, 0, err
		}
	}

	wowPids, err = proc.PIDsByExe(cfg.Process.WowExe)
	if err != nil {
		return 0, 0, err
	}
	if len(wowPids) == 0 && len(bnetPids) == 0 {
		return 0, 0, fmt.Errorf("Battle.net not running and Wow not running; set process.battle_net_launch_exe or start Battle.net manually")
	}

	if len(wowPids) == 0 && len(bnetPids) > 0 {
		bnetPID, bnetHWND, findErr := waitLargestVisibleWindow(cfg.Process.BattleNetExe, deadline)
		if findErr != nil {
			return 0, 0, findErr
		}
		if err := prepareBattleNetUI(log, cfg, bnetPID, bnetHWND); err != nil {
			return 0, 0, err
		}
		if cfg.OCR.Enabled {
			if err := clickBattleNetOCRLabel(log, cfg, bnetHWND, cfg.Bnet.PlayLabels, "click enter game", deadline); err != nil {
				return 0, 0, err
			}
			time.Sleep(2 * time.Second)
		} else {
			x, y := cfg.Bnet.EnterGameClick.X, cfg.Bnet.EnterGameClick.Y
			if x == 0 && y == 0 {
				return 0, 0, fmt.Errorf("bnet.enter_game_click is not calibrated")
			}
			originL, originT, _, _, boundsErr := winutil.ClientAreaScreenBounds(bnetHWND)
			if boundsErr != nil {
				return 0, 0, boundsErr
			}
			screenX, screenY := originL+int32(x), originT+int32(y)
			log.Emit("INFO", "input_mouse", "click enter game", map[string]any{
				"x": screenX, "y": screenY, "button": "left", "pid": bnetPID,
			})
			if err := winutil.Click(screenX, screenY); err != nil {
				return 0, 0, fmt.Errorf("click enter game: %w", err)
			}
			time.Sleep(2 * time.Second)
		}
	}

	if len(wowPids) == 0 {
		if _, err := proc.WaitForExe(cfg.Process.WowExe, deadline); err != nil {
			return 0, 0, fmt.Errorf("Wow.exe not running: %w (start Battle.net manually or set process.battle_net_launch_exe and bnet.enter_game_click)", err)
		}
	}

	pid, hwnd, err = waitLargestVisibleWindow(cfg.Process.WowExe, deadline)
	if err != nil {
		return 0, 0, err
	}
	log.Emit("INFO", "window_activate", "focus Wow", map[string]any{
		"pid":        pid,
		"hwnd":       uint64(hwnd),
		"ok":         true,
		"title_hint": "Wow",
	})
	if err := winutil.FocusAndVerify(hwnd); err != nil {
		return 0, 0, err
	}
	return pid, hwnd, nil
}

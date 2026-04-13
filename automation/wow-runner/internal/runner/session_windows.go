//go:build windows

package runner

import (
	"fmt"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/fsm"
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

func maxKillRestartTotal(cfg *config.Root) int {
	n := cfg.Retry.MaxKillRestartTotal
	if n <= 0 {
		return 10
	}
	return n
}

// effectiveIndices：single 只取第一个索引；all 为完整列表。
func effectiveIndices(cfg *config.Root) []int {
	if cfg.Characters.Mode == "single" {
		return []int{cfg.Characters.Indices[0]}
	}
	out := make([]int, len(cfg.Characters.Indices))
	copy(out, cfg.Characters.Indices)
	return out
}

// killAllWowProcesses 终止列表中的 Wow 并等待进程消失。
func killAllWowProcesses(log *logx.Logger, cfg *config.Root, wowPids []int32) error {
	if len(wowPids) == 0 {
		return nil
	}
	log.Emit("WARN", "process_kill", "terminating Wow.exe process(es)", map[string]any{
		"wow_pids": wowPids,
		"state":    fsm.BNETStart,
		"reason":   "require Battle.net chain",
	})
	goneDeadline := time.Now().Add(60 * time.Second)
	for _, p := range wowPids {
		if err := proc.KillPID(p); err != nil {
			log.Emit("WARN", "process_kill", "KillPID Wow", map[string]any{"pid": p, "error": err.Error()})
		}
	}
	for _, p := range wowPids {
		if err := proc.WaitProcessGone(p, goneDeadline); err != nil {
			return fmt.Errorf("wait Wow pid %d exit after kill: %w", p, err)
		}
	}
	left, err := proc.PIDsByExe(cfg.Process.WowExe)
	if err != nil {
		return err
	}
	if len(left) > 0 {
		return fmt.Errorf("Wow.exe still present after kill: %v", left)
	}
	return nil
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

	// 规范：必须从战网「进入游戏」进入 WoW。在拉起战网之前，若仅有 Wow、无战网进程，杀光 Wow 再重走战网链路。
	if len(wowPids) > 0 && len(bnetPids) == 0 {
		log.Emit("WARN", "process_kill", "Wow without Battle.net: kill all Wow.exe then require Battle.net", map[string]any{
			"wow_pids": wowPids,
			"state":    fsm.BNETStart,
		})
		if err := killAllWowProcesses(log, cfg, wowPids); err != nil {
			return 0, 0, err
		}
		wowPids, err = proc.PIDsByExe(cfg.Process.WowExe)
		if err != nil {
			return 0, 0, err
		}
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
	// 拉起战网后若仍无战网进程但仍有 Wow（异常），再清一次。
	if len(wowPids) > 0 && len(bnetPids) == 0 {
		log.Emit("WARN", "process_kill", "Wow still up without Battle.net after launch attempt; killing Wow", map[string]any{
			"wow_pids": wowPids,
		})
		if err := killAllWowProcesses(log, cfg, wowPids); err != nil {
			return 0, 0, err
		}
		wowPids, err = proc.PIDsByExe(cfg.Process.WowExe)
		if err != nil {
			return 0, 0, err
		}
	}

	if len(wowPids) == 0 && len(bnetPids) == 0 {
		return 0, 0, fmt.Errorf("Battle.net not running and Wow not running; set process.battle_net_launch_exe or start Battle.net manually")
	}

	if len(wowPids) == 0 && len(bnetPids) > 0 {
		if err := prepareBattleNetUI(log, cfg, bnetPids[0]); err != nil {
			return 0, 0, err
		}
		x, y := cfg.Bnet.EnterGameClick.X, cfg.Bnet.EnterGameClick.Y
		if x == 0 && y == 0 {
			log.Emit("WARN", "transition", "enter_game_click not calibrated (0,0), cannot start Wow from Battle.net", map[string]any{
				"from_state": fsm.BNETStart,
				"to_state":   fsm.BNETStart,
			})
		} else {
			log.Emit("INFO", "input_mouse", "click enter game", map[string]any{
				"x": x, "y": y, "button": "left",
			})
			if err := winutil.Click(int32(x), int32(y)); err != nil {
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

	wowPids, err = proc.PIDsByExe(cfg.Process.WowExe)
	if err != nil {
		return 0, 0, err
	}
	if len(wowPids) == 0 {
		return 0, 0, fmt.Errorf("Wow.exe still not found after wait")
	}

	pid = wowPids[0]
	hwnd = winutil.FindTopLevelVisibleHWND(uint32(pid))
	if hwnd == 0 {
		return 0, 0, fmt.Errorf("no visible top-level window for Wow pid %d", pid)
	}
	log.Emit("INFO", "window_activate", "focus Wow", map[string]any{
		"pid":        pid,
		"hwnd":       uint64(hwnd),
		"ok":         true,
		"title_hint": "Wow",
	})
	if err := winutil.FocusWindow(hwnd); err != nil {
		return 0, 0, err
	}
	return pid, hwnd, nil
}

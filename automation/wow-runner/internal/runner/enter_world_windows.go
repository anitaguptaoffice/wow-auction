//go:build windows

package runner

import (
	"fmt"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/fsm"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winutil"
)

func enterWorldTimeout(cfg *config.Root) time.Duration {
	s := cfg.Timeouts.EnterWorld
	if s <= 0 {
		s = 120
	}
	return time.Duration(s) * time.Second
}

// waitEnterWorld 在按下进世界键后轮询 enter_world_actionbar 模板；成功后可选再睡 enter_world_load 秒。
func waitEnterWorld(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, charPos int) error {
	p := cfg.ResolvePath(cfg.Templates.EnterWorldActionbar)
	roi := searchROI(cfg)
	th := visionThreshold(cfg)
	poll := visionPoll(cfg)
	deadline := time.Now().Add(enterWorldTimeout(cfg))
	log.Emit("INFO", "wait_start", "enter_world_actionbar", map[string]any{
		"wait_id":    "enter_world_actionbar",
		"timeout_ms": int(enterWorldTimeout(cfg).Milliseconds()),
		"char_index": charPos,
		"state":      fsm.EnterWorld,
	})
	ok, score, _, err := vision.WaitForMatch(hwnd, p, roi, th, poll, deadline, visionOpts(cfg))
	if err != nil {
		tryFailureCapture(log, cfg, hwnd, "enter_world_actionbar")
		return err
	}
	if !ok {
		tryFailureCapture(log, cfg, hwnd, "enter_world_actionbar")
		return fmt.Errorf("enter_world_actionbar: template not matched within timeout (last_score=%.4f)", score)
	}
	log.Emit("INFO", "wait_satisfied", "enter_world_actionbar matched", map[string]any{
		"wait_id":    "enter_world_actionbar",
		"similarity": score,
		"char_index": charPos,
		"state":      fsm.EnterWorld,
	})
	loadSec := cfg.Timeouts.EnterWorldLoad
	if loadSec <= 0 {
		loadSec = 5
	}
	log.Emit("INFO", "wait_start", "enter_world_load grace sleep", map[string]any{
		"wait_id":    "enter_world_load",
		"timeout_ms": loadSec * 1000,
		"char_index": charPos,
		"state":      fsm.EnterWorld,
	})
	time.Sleep(time.Duration(loadSec) * time.Second)
	log.Emit("INFO", "wait_satisfied", "enter_world_load done", map[string]any{
		"wait_id":    "enter_world_load",
		"elapsed_ms": loadSec * 1000,
		"char_index": charPos,
	})
	return nil
}

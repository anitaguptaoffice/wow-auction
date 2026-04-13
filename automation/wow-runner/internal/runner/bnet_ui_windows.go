//go:build windows

package runner

import (
	"fmt"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/fsm"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winutil"
)

// prepareBattleNetUI 在点击「进入游戏」前：可选等待战网就绪模板（常驻战网可直接进魔兽，一般无弹窗问题）。
func prepareBattleNetUI(log *logx.Logger, cfg *config.Root, bnetPID int32) error {
	hwnd := winutil.FindTopLevelVisibleHWND(uint32(bnetPID))
	if hwnd == 0 {
		return fmt.Errorf("Battle.net: no visible top-level window for pid %d", bnetPID)
	}
	if err := winutil.FocusWindow(hwnd); err != nil {
		return err
	}
	roi := bnetSearchROI(cfg)
	th := visionThreshold(cfg)
	poll := visionPoll(cfg)
	opts := visionOpts(cfg)
	deadline := time.Now().Add(bnetUIReadyTimeout(cfg))

	ready := strings.TrimSpace(cfg.ResolvePath(cfg.Bnet.ReadyTemplate))
	if ready != "" {
		log.Emit("INFO", "wait_start", "bnet ready template", map[string]any{
			"wait_id":    "bnet_ready",
			"timeout_ms": int(bnetUIReadyTimeout(cfg).Milliseconds()),
			"state":      fsm.BNETStart,
		})
		ok, score, _, err := vision.WaitForMatch(hwnd, ready, roi, th, poll, deadline, opts)
		if err != nil {
			tryFailureCapture(log, cfg, hwnd, "bnet_ready")
			return fmt.Errorf("bnet ready: %w", err)
		}
		if !ok {
			tryFailureCapture(log, cfg, hwnd, "bnet_ready")
			return fmt.Errorf("bnet ready: template not matched (last_score=%.4f)", score)
		}
		log.Emit("INFO", "wait_satisfied", "bnet ready template ok", map[string]any{
			"wait_id":    "bnet_ready",
			"similarity": score,
		})
	}

	return nil
}

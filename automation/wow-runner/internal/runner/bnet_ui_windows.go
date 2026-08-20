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

// prepareBattleNetUI 在点击「进入游戏」前选择魔兽页，并可选等待已校准的就绪模板。
func prepareBattleNetUI(log *logx.Logger, cfg *config.Root, bnetPID int32, hwnd winutil.HWND) error {
	if hwnd == 0 {
		return fmt.Errorf("Battle.net: no visible top-level window for pid %d", bnetPID)
	}
	if err := winutil.FocusAndVerify(hwnd); err != nil {
		return err
	}
	originL, originT, _, _, err := winutil.ClientAreaScreenBounds(hwnd)
	if err != nil {
		return err
	}
	deadline := time.Now().Add(bnetUIReadyTimeout(cfg))
	game := cfg.Bnet.GameSelectClick
	if game.X != 0 || game.Y != 0 {
		x, y := originL+int32(game.X), originT+int32(game.Y)
		log.Emit("INFO", "input_mouse", "select World of Warcraft in Battle.net", map[string]any{
			"pid": bnetPID, "x": x, "y": y, "button": "left",
		})
		if err := winutil.Click(x, y); err != nil {
			return fmt.Errorf("select World of Warcraft: %w", err)
		}
		time.Sleep(700 * time.Millisecond)
	} else if cfg.OCR.Enabled {
		if err := clickBattleNetOCRLabel(log, cfg, hwnd, cfg.Bnet.GameLabels, "select World of Warcraft", deadline); err != nil {
			return err
		}
		time.Sleep(700 * time.Millisecond)
	}
	roi := bnetSearchROI(cfg)
	th := visionThreshold(cfg)
	poll := visionPoll(cfg)
	opts := visionOpts(cfg)

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

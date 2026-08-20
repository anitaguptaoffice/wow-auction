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

// waitCharSelectScreenBeforeNavigate 在发送 Home / ↓ 之前，强校验当前已在选角界面（char_select_screen 模板）。
func waitCharSelectScreenBeforeNavigate(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, charPos int) error {
	if cfg.OCR.Enabled {
		return waitForOCRTokens(
			log, cfg, hwnd, cfg.OCR.CharSelectTokens, "char_select_ocr",
			time.Now().Add(charSelectGateTimeout(cfg)), false,
		)
	}
	p := cfg.ResolvePath(cfg.Templates.CharSelectScreen)
	roi := searchROI(cfg)
	th := visionThreshold(cfg)
	poll := visionPoll(cfg)
	deadline := time.Now().Add(charSelectGateTimeout(cfg))
	opts := visionOpts(cfg)
	log.Emit("INFO", "wait_start", "char_select_screen (before Home/Down)", map[string]any{
		"wait_id":    "char_select_gate",
		"timeout_ms": int(charSelectGateTimeout(cfg).Milliseconds()),
		"char_index": charPos,
		"state":      fsm.CharSelect,
	})
	ok, score, _, err := vision.WaitForMatch(hwnd, p, roi, th, poll, deadline, opts)
	if err != nil {
		tryFailureCapture(log, cfg, hwnd, "char_select_gate")
		return err
	}
	if !ok {
		tryFailureCapture(log, cfg, hwnd, "char_select_gate")
		return fmt.Errorf("char_select_screen: not matched before navigation (last_score=%.4f)", score)
	}
	log.Emit("INFO", "wait_satisfied", "char_select_screen gate ok", map[string]any{
		"wait_id":    "char_select_gate",
		"similarity": score,
		"char_index": charPos,
	})
	return nil
}

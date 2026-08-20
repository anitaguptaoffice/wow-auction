//go:build windows

package runner

import (
	"fmt"
	"image"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/fsm"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winutil"
)

func searchROI(cfg *config.Root) image.Rectangle {
	r := cfg.Vision.SearchROI
	if r == nil || r.W <= 0 || r.H <= 0 {
		return image.Rectangle{}
	}
	return image.Rect(r.X, r.Y, r.X+r.W, r.Y+r.H)
}

func visionThreshold(cfg *config.Root) float64 {
	t := cfg.Vision.MatchThreshold
	if t > 0 && t <= 1 {
		return t
	}
	return 0.85
}

func visionPoll(cfg *config.Root) time.Duration {
	if cfg.Vision.PollIntervalMS > 0 {
		return time.Duration(cfg.Vision.PollIntervalMS) * time.Millisecond
	}
	return 400 * time.Millisecond
}

func ahOpenTimeout(cfg *config.Root) time.Duration {
	s := cfg.Timeouts.AHOpen
	if s <= 0 {
		s = 30
	}
	return time.Duration(s) * time.Second
}

func maxSinceScanTrigger(cfg *config.Root) time.Duration {
	s := cfg.Timeouts.MaxSinceScanTrigger
	if s <= 0 {
		s = 600
	}
	return time.Duration(s) * time.Second
}

// waitAuctionHouseOpen waits for ah_open_ok template after tar+interact（配置空则 Load 时已填 DefaultPlaceholderTemplate）。
func waitAuctionHouseOpen(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, charIdx int) error {
	p := cfg.ResolvePath(cfg.Templates.AHOpenOK)
	roi := searchROI(cfg)
	th := visionThreshold(cfg)
	poll := visionPoll(cfg)
	deadline := time.Now().Add(ahOpenTimeout(cfg))
	log.Emit("INFO", "wait_start", "poll ah_open_ok", map[string]any{
		"wait_id":    "ah_open_ok",
		"timeout_ms": int(ahOpenTimeout(cfg).Milliseconds()),
		"char_index": charIdx,
		"state":      fsm.AHOpen,
	})
	opts := visionOpts(cfg)
	ok, score, _, err := vision.WaitForMatch(hwnd, p, roi, th, poll, deadline, opts)
	if err != nil {
		tryFailureCapture(log, cfg, hwnd, "ah_open_ok")
		return err
	}
	if !ok {
		tryFailureCapture(log, cfg, hwnd, "ah_open_ok")
		return fmt.Errorf("ah_open_ok: template not matched within timeout (last_score=%.4f)", score)
	}
	log.Emit("INFO", "wait_satisfied", "ah_open_ok matched", map[string]any{
		"wait_id":    "ah_open_ok",
		"similarity": score,
		"char_index": charIdx,
		"state":      fsm.AHOpen,
	})
	return nil
}

// waitPluginScan requires a monotonic “started → complete” observation. It
// never logs out or terminates WoW; callers perform a normal /logout or /quit
// only after this gate succeeds.
func waitPluginScan(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, pid int32, charIdx int, scanTrigger time.Time) error {
	if cfg.OCR.Enabled {
		return waitPluginOCR(log, cfg, hwnd, charIdx, scanTrigger)
	}
	charPath := cfg.ResolvePath(cfg.Templates.CharSelectScreen)
	if charPath == "" {
		return fmt.Errorf("templates.char_select_screen resolved empty (expected default placeholder from config.Load)")
	}

	deadline := scanTrigger.Add(maxSinceScanTrigger(cfg))
	roi := searchROI(cfg)
	th := visionThreshold(cfg)
	poll := visionPoll(cfg)
	scanDonePath := cfg.ResolvePath(cfg.Templates.PluginScanComplete)
	scanStartPath := cfg.ResolvePath(cfg.Templates.PluginScanStarted)
	opts := visionOpts(cfg)

	log.Emit("INFO", "wait_start", "WAIT_PLUGIN_SCAN until completion or deadline", map[string]any{
		"wait_id":         "wait_plugin_scan",
		"timeout_ms":      int(time.Until(deadline).Milliseconds()),
		"char_index":      charIdx,
		"state":           fsm.WaitPluginScan,
		"scan_trigger_ts": scanTrigger.Format(time.RFC3339Nano),
	})

	var loggedScanStart bool
	pollCount := 0
	if scanDonePath == "" || scanStartPath == "" {
		return fmt.Errorf("template fallback requires plugin_scan_started and plugin_scan_complete")
	}

	for time.Now().Before(deadline) {
		pollCount++

		sChar, _, err := vision.MatchOnce(hwnd, charPath, roi, opts)
		if err != nil {
			return err
		}
		if sChar >= th {
			return fmt.Errorf("returned to character select before plugin completion (score=%.4f)", sChar)
		}

		if !loggedScanStart {
			s, _, err := vision.MatchOnce(hwnd, scanStartPath, roi, opts)
			if err != nil {
				return err
			}
			if s >= th {
				log.Emit("INFO", "metric", "plugin_scan_started observed", map[string]any{
					"similarity": s,
					"char_index": charIdx,
				})
				loggedScanStart = true
			}
		} else {
			s, _, err := vision.MatchOnce(hwnd, scanDonePath, roi, opts)
			if err != nil {
				return err
			}
			if s >= th {
				log.Emit("INFO", "visual_decision", "plugin_scan_complete matched", map[string]any{
					"similarity": s,
					"char_index": charIdx,
				})
				return nil
			}
		}

		if pollCount == 1 || pollCount%20 == 0 {
			log.Emit("INFO", "visual_decision", "WAIT_PLUGIN_SCAN poll", map[string]any{
				"poll_count": pollCount,
				"char_index": charIdx,
				"state":      fsm.WaitPluginScan,
			})
		}

		time.Sleep(poll)
	}

	log.Emit("ERROR", "exception", "WAIT_PLUGIN_SCAN timeout (PLUGIN_STUCK)", map[string]any{
		"poll_count":               pollCount,
		"max_since_scan_trigger_s": int(maxSinceScanTrigger(cfg).Seconds()),
		"char_index":               charIdx,
		"pid":                      pid,
	})
	tryFailureCapture(log, cfg, hwnd, "wait_plugin_scan")
	return fmt.Errorf("%w: max_since_scan_trigger exceeded", ErrPluginTimeout)
}

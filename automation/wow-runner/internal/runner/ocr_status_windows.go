//go:build windows

package runner

import (
	"context"
	"fmt"
	"image"
	"strings"
	"time"
	"unicode"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/fsm"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winocr"
	"wow-auction/automation/wow-runner/internal/winutil"
)

type ocrScanPhase string

const (
	ocrUnknown  ocrScanPhase = "unknown"
	ocrWaiting  ocrScanPhase = "waiting"
	ocrScanning ocrScanPhase = "scanning"
	ocrComplete ocrScanPhase = "complete"
	ocrWarning  ocrScanPhase = "warning"
	ocrError    ocrScanPhase = "error"
)

// machineText removes punctuation and whitespace so AS_SCANNING remains
// matchable when OCR returns "AS SCANNING" or drops the underscore.
func machineText(value string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			return unicode.ToLower(r)
		}
		return -1
	}, winocr.NormalizeText(value))
}

func containsMachineToken(haystack string, tokens []string) bool {
	for _, token := range tokens {
		needle := machineText(token)
		if needle != "" && strings.Contains(haystack, needle) {
			return true
		}
	}
	return false
}

func classifyOCRStatus(text string, cfg config.OCR) ocrScanPhase {
	compact := machineText(text)
	// Specific failure/warning labels must precede the generic Chinese phrase
	// “扫描完成”, otherwise a warning could be accepted as a clean completion.
	if containsMachineToken(compact, cfg.ErrorTokens) || strings.Contains(compact, machineText("扫描未完成")) {
		return ocrError
	}
	if containsMachineToken(compact, cfg.WarningTokens) || strings.Contains(compact, machineText("扫描完成但存在缺失")) {
		return ocrWarning
	}
	if containsMachineToken(compact, cfg.CompleteTokens) || strings.Contains(compact, machineText("扫描完成")) {
		return ocrComplete
	}
	if containsMachineToken(compact, cfg.ScanningTokens) || strings.Contains(compact, machineText("正在采集拍卖数据")) {
		return ocrScanning
	}
	if containsMachineToken(compact, cfg.WaitingTokens) || strings.Contains(compact, machineText("正在请求拍卖快照")) {
		return ocrWaiting
	}
	return ocrUnknown
}

type scanOCRState struct {
	seenWaiting  bool
	seenScanning bool
	last         ocrScanPhase
	stable       int
}

func (s *scanOCRState) observe(phase ocrScanPhase, requiredStable int) (done bool, warning bool, err error) {
	if requiredStable < 1 {
		requiredStable = 1
	}
	if phase == s.last {
		s.stable++
	} else {
		s.last = phase
		s.stable = 1
	}
	switch phase {
	case ocrWaiting:
		s.seenWaiting = true
	case ocrScanning:
		s.seenScanning = true
	case ocrError:
		return false, false, fmt.Errorf("plugin OCR reported AS_ERROR")
	case ocrComplete, ocrWarning:
		// A completion visible before scanning is stale UI from a previous run.
		if !s.seenScanning || s.stable < requiredStable {
			return false, false, nil
		}
		return true, phase == ocrWarning, nil
	}
	return false, false, nil
}

func configuredOCRROI(cfg *config.Root, bounds image.Rectangle) image.Rectangle {
	if r := cfg.OCR.SearchROI; r != nil && r.W > 0 && r.H > 0 {
		return image.Rect(r.X, r.Y, r.X+r.W, r.Y+r.H).Intersect(bounds)
	}
	// The addon panel is anchored at TOP center. Restricting to the upper fifth
	// avoids names/chat text while still covering high-DPI UI scaling.
	height := bounds.Dy() / 5
	if height < 220 {
		height = 220
	}
	if height > bounds.Dy() {
		height = bounds.Dy()
	}
	return image.Rect(bounds.Min.X, bounds.Min.Y, bounds.Max.X, bounds.Min.Y+height)
}

func waitForOCRTokens(
	log *logx.Logger,
	cfg *config.Root,
	hwnd winutil.HWND,
	labels []string,
	waitID string,
	deadline time.Time,
	topPanelOnly bool,
) error {
	engine, err := winocr.New(cfg.OCR.Language)
	if err != nil {
		return fmt.Errorf("initialize Windows.Media.Ocr: %w", err)
	}
	defer engine.Close()
	if err := winutil.FocusAndVerify(hwnd); err != nil {
		return err
	}
	poll := time.Duration(cfg.OCR.PollIntervalMS) * time.Millisecond
	if poll <= 0 {
		poll = 750 * time.Millisecond
	}
	required := cfg.OCR.StableReads
	if required < 1 {
		required = 1
	}
	stable := 0
	for time.Now().Before(deadline) {
		img, err := vision.CaptureClient(hwnd)
		if err != nil {
			return err
		}
		roi := image.Rectangle{}
		if topPanelOnly {
			roi = configuredOCRROI(cfg, img.Bounds())
		}
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		result, err := engine.Recognize(ctx, img, roi)
		cancel()
		if err != nil {
			stable = 0
			time.Sleep(poll)
			continue
		}
		text := machineText(result.Text)
		if containsMachineToken(text, labels) {
			stable++
			if stable >= required {
				log.Emit("INFO", "wait_satisfied", "Windows OCR gate satisfied", map[string]any{
					"wait_id": waitID, "stable_reads": stable, "word_count": len(result.Words),
				})
				return nil
			}
		} else {
			stable = 0
		}
		time.Sleep(poll)
	}
	tryFailureCapture(log, cfg, hwnd, waitID)
	return fmt.Errorf("%s: Windows OCR did not find %v before timeout", waitID, labels)
}

func waitPluginOCR(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, charIdx int, scanTrigger time.Time) error {
	engine, err := winocr.New(cfg.OCR.Language)
	if err != nil {
		return fmt.Errorf("initialize Windows.Media.Ocr: %w", err)
	}
	defer engine.Close()

	deadline := scanTrigger.Add(maxSinceScanTrigger(cfg))
	poll := time.Duration(cfg.OCR.PollIntervalMS) * time.Millisecond
	if poll <= 0 {
		poll = 750 * time.Millisecond
	}
	state := scanOCRState{}
	consecutiveErrors := 0
	pollCount := 0
	log.Emit("INFO", "wait_start", "Windows OCR plugin state", map[string]any{
		"wait_id": "plugin_ocr", "char_index": charIdx,
		"timeout_ms": int(time.Until(deadline).Milliseconds()), "state": fsm.WaitPluginScan,
	})

	for time.Now().Before(deadline) {
		pollCount++
		img, captureErr := vision.CaptureClient(hwnd)
		if captureErr != nil {
			return fmt.Errorf("capture WoW for OCR: %w", captureErr)
		}
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		result, recognizeErr := engine.Recognize(ctx, img, configuredOCRROI(cfg, img.Bounds()))
		cancel()
		if recognizeErr != nil {
			consecutiveErrors++
			if consecutiveErrors >= 5 {
				return fmt.Errorf("Windows OCR failed %d consecutive times: %w", consecutiveErrors, recognizeErr)
			}
			time.Sleep(poll)
			continue
		}
		consecutiveErrors = 0
		phase := classifyOCRStatus(result.Text, cfg.OCR)
		previousPhase := state.last
		done, warning, observeErr := state.observe(phase, cfg.OCR.StableReads)
		if observeErr != nil {
			tryFailureCapture(log, cfg, hwnd, "plugin_ocr_error")
			return observeErr
		}
		if phase != ocrUnknown && (phase != previousPhase || pollCount == 1 || pollCount%20 == 0) {
			log.Emit("INFO", "ocr_state", "plugin OCR state", map[string]any{
				"phase": phase, "stable_reads": state.stable, "seen_waiting": state.seenWaiting,
				"seen_scanning": state.seenScanning, "word_count": len(result.Words), "char_index": charIdx,
			})
		}
		if done {
			level := "INFO"
			if warning {
				level = "WARN"
			}
			log.Emit(level, "wait_satisfied", "plugin scan saved in memory; graceful exit required", map[string]any{
				"wait_id": "plugin_ocr", "phase": phase, "warning": warning,
				"poll_count": pollCount, "char_index": charIdx,
			})
			return nil
		}
		time.Sleep(poll)
	}
	tryFailureCapture(log, cfg, hwnd, "plugin_ocr_timeout")
	return fmt.Errorf("%w: OCR did not observe scanning then completion", ErrPluginTimeout)
}

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
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winutil"
)

func gracefulExitTimeout(cfg *config.Root) time.Duration {
	seconds := cfg.Timeouts.GracefulExit
	if seconds <= 0 {
		seconds = 75
	}
	return time.Duration(seconds) * time.Second
}

func sendWowSlashCommand(log *logx.Logger, hwnd winutil.HWND, command string, charIdx int) error {
	if err := winutil.FocusAndVerify(hwnd); err != nil {
		return fmt.Errorf("focus WoW before %s: %w", command, err)
	}
	log.Emit("INFO", "input_command", "send WoW slash command", map[string]any{
		"command": command, "char_index": charIdx, "state": fsm.GracefulExit,
	})
	if err := winutil.KeyTap(0x0D); err != nil { // open chat
		return err
	}
	time.Sleep(120 * time.Millisecond)
	if err := winutil.SendText(command); err != nil {
		return err
	}
	time.Sleep(80 * time.Millisecond)
	if err := winutil.KeyTap(0x0D); err != nil { // submit chat command
		return err
	}
	return nil
}

// gracefulLogout returns to character select and therefore gives WoW a normal
// SavedVariables flush point. It never calls TerminateProcess.
func gracefulLogout(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, charIdx int) error {
	if macroKey := strings.TrimSpace(cfg.Keys.LogoutMacro); macroKey != "" {
		if err := winutil.FocusAndVerify(hwnd); err != nil {
			return fmt.Errorf("focus WoW before logout macro: %w", err)
		}
		if err := keyTapByName(log, macroKey, "logout_macro"); err != nil {
			return err
		}
	} else {
		if err := sendWowSlashCommand(log, hwnd, "/logout", charIdx); err != nil {
			return err
		}
	}
	if cfg.OCR.Enabled {
		return waitForOCRTokens(
			log, cfg, hwnd, cfg.OCR.CharSelectTokens, "graceful_logout_ocr",
			time.Now().Add(gracefulExitTimeout(cfg)), false,
		)
	}
	path := cfg.ResolvePath(cfg.Templates.CharSelectScreen)
	if path == "" {
		return fmt.Errorf("templates.char_select_screen is required to verify /logout")
	}
	deadline := time.Now().Add(gracefulExitTimeout(cfg))
	ok, score, _, err := vision.WaitForMatch(
		hwnd, path, searchROI(cfg), visionThreshold(cfg), visionPoll(cfg), deadline, visionOpts(cfg),
	)
	if err != nil {
		return err
	}
	if !ok {
		tryFailureCapture(log, cfg, hwnd, "graceful_logout")
		return fmt.Errorf("/logout did not reach character select (last_score=%.4f)", score)
	}
	log.Emit("INFO", "wait_satisfied", "normal /logout reached character select", map[string]any{
		"char_index": charIdx, "similarity": score,
	})
	return nil
}

// gracefulQuit waits for Wow.exe to disappear naturally. A timeout is returned
// without force-killing the process so a pending SavedVariables write is not lost.
func gracefulQuit(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, pid int32, charIdx int) error {
	if err := sendWowSlashCommand(log, hwnd, "/quit", charIdx); err != nil {
		return err
	}
	deadline := time.Now().Add(gracefulExitTimeout(cfg))
	if err := proc.WaitProcessGone(pid, deadline); err != nil {
		tryFailureCapture(log, cfg, hwnd, "graceful_quit")
		return fmt.Errorf("/quit did not terminate Wow normally; process was left running: %w", err)
	}
	log.Emit("INFO", "wait_satisfied", "Wow exited normally after /quit", map[string]any{
		"pid": pid, "char_index": charIdx,
	})
	return nil
}

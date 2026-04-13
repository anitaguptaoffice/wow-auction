//go:build windows

package runner

import (
	"errors"
	"fmt"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/fsm"
	"wow-auction/automation/wow-runner/internal/input"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/proc"
	"wow-auction/automation/wow-runner/internal/winutil"
)

func runPlatform(log *logx.Logger, cfg *config.Root) error {
	emitTrans(log, fsm.INIT, fsm.BNETStart, "run_fsm", nil)

	indices := effectiveIndices(cfg)
	killRestarts := 0
	charPos := 0

	for charPos < len(indices) {
		slot := indices[charPos]
		perCharRetries := 0

		for {
			emitTrans(log, fsm.BNETStart, fsm.WOWForeground, "bootstrap", map[string]any{
				"char_index": charPos,
				"slot":       slot,
				"retry":      perCharRetries,
			})

			pid, hwnd, err := bootstrapWow(log, cfg)
			if err != nil {
				return err
			}

			if err := selectCharacterForRound(log, cfg, indices, charPos, slot, perCharRetries, hwnd); err != nil {
				return err
			}

			emitTrans(log, fsm.CharSelect, fsm.EnterWorld, "enter", map[string]any{
				"char_index": charPos,
				"slot":       slot,
				"state":      fsm.EnterWorld,
			})
			if err := keyTapByName(log, cfg.Keys.EnterWorld, "enter_world"); err != nil {
				return err
			}
			if err := waitEnterWorld(log, cfg, hwnd, charPos); err != nil {
				return err
			}

			emitTrans(log, fsm.EnterWorld, fsm.AHPrep, "prep", map[string]any{"char_index": charPos, "slot": slot, "state": fsm.AHPrep})
			if cfg.Timeouts.AHPrep > 0 {
				time.Sleep(time.Duration(cfg.Timeouts.AHPrep) * time.Second)
			}

			emitTrans(log, fsm.AHPrep, fsm.AHOpen, "open_ah", map[string]any{"char_index": charPos, "slot": slot, "state": fsm.AHOpen})
			if err := keyTapByName(log, cfg.Keys.AuctionTarMacro, "auction_tar_macro"); err != nil {
				return err
			}
			time.Sleep(150 * time.Millisecond)
			if err := keyTapByName(log, cfg.Keys.InteractTarget, "interact_target"); err != nil {
				return err
			}

			if err := waitAuctionHouseOpen(log, cfg, hwnd, charPos); err != nil {
				return err
			}

			ts := time.Now()
			scanTS := ts.Format(time.RFC3339Nano)
			log.Emit("INFO", "scan_trigger_recorded", "AH_OPEN success, scan timer started", map[string]any{
				"scan_trigger_ts": scanTS,
				"char_index":      charPos,
				"slot":            slot,
				"state":           fsm.WaitPluginLogout,
			})

			emitTrans(log, fsm.AHOpen, fsm.WaitPluginLogout, "wait_templates", map[string]any{
				"char_index": charPos,
				"slot":       slot,
				"state":      fsm.WaitPluginLogout,
			})
			err = waitPluginLogout(log, cfg, hwnd, pid, charPos, ts)
			if err == nil {
				if charPos == len(indices)-1 {
					return roundDoneKillWow(log, pid, charPos, slot)
				}
				charPos++
				break
			}

			if errors.Is(err, ErrPluginTimeoutKill) {
				killRestarts++
				perCharRetries++
				log.Emit("WARN", "transition", "PLUGIN_STUCK: will full restart same character", map[string]any{
					"char_index":        charPos,
					"slot":              slot,
					"kill_restart_seq":  killRestarts,
					"per_char_retry":    perCharRetries,
					"max_kill_total":    maxKillRestartTotal(cfg),
					"max_per_character": maxRetriesPerCharacter(cfg),
				})
				if killRestarts > maxKillRestartTotal(cfg) {
					emitTrans(log, fsm.WaitPluginLogout, fsm.Failed, "kill_restart_budget", map[string]any{"char_index": charPos})
					return fmt.Errorf("exceeded retry.max_kill_restart_total (%d)", maxKillRestartTotal(cfg))
				}
				if perCharRetries > maxRetriesPerCharacter(cfg) {
					emitTrans(log, fsm.WaitPluginLogout, fsm.Failed, "per_char_retry_budget", map[string]any{"char_index": charPos})
					return fmt.Errorf("exceeded retry.max_retries_per_character (%d) for slot %d", maxRetriesPerCharacter(cfg), slot)
				}
				deadline := time.Now().Add(45 * time.Second)
				if werr := proc.WaitProcessGone(pid, deadline); werr != nil {
					log.Emit("WARN", "process_poll", "WaitProcessGone", map[string]any{"pid": pid, "error": werr.Error()})
				}
				time.Sleep(2 * time.Second)
				continue
			}
			return err
		}
	}

	return nil
}

func selectCharacterForRound(log *logx.Logger, cfg *config.Root, indices []int, charPos, slot, perCharRetries int, hwnd winutil.HWND) error {
	if err := waitCharSelectScreenBeforeNavigate(log, cfg, hwnd, charPos); err != nil {
		return err
	}

	if charPos == 0 || perCharRetries > 0 {
		emitTrans(log, fsm.WOWForeground, fsm.CharSelect, "start_char_select", map[string]any{
			"char_index": charPos,
			"slot":       slot,
			"state":      fsm.CharSelect,
		})
		return doCharSelect(log, cfg, slot)
	}

	from := fsm.WaitPluginLogout
	emitTrans(log, from, fsm.CharSelectAgain, "next_character", map[string]any{
		"char_index": charPos,
		"slot":       slot,
		"state":      fsm.CharSelectAgain,
	})
	prev := indices[charPos-1]
	if slot < prev {
		emitTrans(log, fsm.CharSelectAgain, fsm.CharSelect, "slot_non_monotonic_use_home", map[string]any{
			"char_index": charPos,
			"prev_slot":  prev,
			"slot":       slot,
		})
		return doCharSelect(log, cfg, slot)
	}
	return doCharSelectAgain(log, cfg, slot-prev)
}

func roundDoneKillWow(log *logx.Logger, pid int32, charPos, slot int) error {
	emitTrans(log, fsm.WaitPluginLogout, fsm.RoundDone, "last_char_on_char_select", map[string]any{
		"char_index": charPos,
		"slot":       slot,
		"state":      fsm.RoundDone,
	})
	log.Emit("INFO", "process_kill", "taskkill Wow.exe after last character scan (ROUND_DONE)", map[string]any{
		"pid":        pid,
		"char_index": charPos,
		"slot":       slot,
	})
	if err := proc.KillPID(pid); err != nil {
		emitTrans(log, fsm.RoundDone, fsm.Failed, "kill_failed", map[string]any{"pid": pid})
		return fmt.Errorf("ROUND_DONE KillPID: %w", err)
	}
	log.Emit("INFO", "transition", "ROUND_DONE → success", map[string]any{
		"from_state": fsm.RoundDone,
		"to_state":   "EXIT",
		"trigger":    "wow_terminated",
	})
	return nil
}

func emitTrans(log *logx.Logger, from, to, trigger string, extra map[string]any) {
	m := map[string]any{
		"from_state": from,
		"to_state":   to,
		"trigger":    trigger,
	}
	for k, v := range extra {
		m[k] = v
	}
	log.Emit("INFO", "transition", from+" → "+to, m)
}

func doCharSelect(log *logx.Logger, cfg *config.Root, slotFromHome int) error {
	if err := keyTapByName(log, cfg.Keys.CharHome, "char_home"); err != nil {
		return err
	}
	time.Sleep(120 * time.Millisecond)
	downKey := cfg.Keys.CharSelectDown
	if downKey == "" {
		downKey = "Down"
	}
	for i := 0; i < slotFromHome; i++ {
		if err := keyTapByName(log, downKey, "char_select_down"); err != nil {
			return err
		}
		time.Sleep(80 * time.Millisecond)
	}
	return nil
}

func doCharSelectAgain(log *logx.Logger, cfg *config.Root, downCount int) error {
	if downCount <= 0 {
		return nil
	}
	downKey := cfg.Keys.CharSelectDown
	if downKey == "" {
		downKey = "Down"
	}
	for i := 0; i < downCount; i++ {
		if err := keyTapByName(log, downKey, "char_select_down"); err != nil {
			return err
		}
		time.Sleep(80 * time.Millisecond)
	}
	return nil
}

func keyTapByName(log *logx.Logger, keyName, field string) error {
	vk, err := input.VK(keyName)
	if err != nil {
		return fmt.Errorf("key %s (%s): %w", field, keyName, err)
	}
	log.Emit("INFO", "input_key", "key tap", map[string]any{
		"keys":  []string{keyName},
		"field": field,
	})
	return winutil.KeyTap(vk)
}

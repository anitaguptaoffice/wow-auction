//go:build windows

package runner

import (
	"errors"
	"fmt"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/fsm"
	"wow-auction/automation/wow-runner/internal/input"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/winutil"
)

func runPlatform(log *logx.Logger, cfg *config.Root) error {
	emitTrans(log, fsm.INIT, fsm.BNETStart, "run_fsm", nil)

	indices := effectiveIndices(cfg)
	restartCount := 0
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
			if target := strings.TrimSpace(cfg.Keys.AuctioneerTarget); target != "" {
				if err := sendWowSlashCommand(log, hwnd, "/targetexact "+target, charPos); err != nil {
					return err
				}
			} else {
				if err := keyTapByName(log, cfg.Keys.AuctionTarMacro, "auction_tar_macro"); err != nil {
					return err
				}
			}
			time.Sleep(150 * time.Millisecond)
			if err := keyTapByName(log, cfg.Keys.InteractTarget, "interact_target"); err != nil {
				return err
			}

			ts := time.Now()
			if !cfg.OCR.Enabled {
				if err := waitAuctionHouseOpen(log, cfg, hwnd, charPos); err != nil {
					return err
				}
			}
			scanTS := ts.Format(time.RFC3339Nano)
			log.Emit("INFO", "scan_trigger_recorded", "AH_OPEN success, scan timer started", map[string]any{
				"scan_trigger_ts": scanTS,
				"char_index":      charPos,
				"slot":            slot,
				"state":           fsm.WaitPluginScan,
			})

			emitTrans(log, fsm.AHOpen, fsm.WaitPluginScan, "wait_plugin_state", map[string]any{
				"char_index": charPos,
				"slot":       slot,
				"state":      fsm.WaitPluginScan,
			})
			err = waitPluginScan(log, cfg, hwnd, pid, charPos, ts)
			if err == nil {
				emitTrans(log, fsm.WaitPluginScan, fsm.GracefulExit, "scan_complete", map[string]any{
					"char_index": charPos, "slot": slot,
				})
				if charPos == len(indices)-1 {
					if err := gracefulQuit(log, cfg, hwnd, pid, charPos); err != nil {
						return err
					}
					emitTrans(log, fsm.GracefulExit, fsm.SnapshotValidate, "process_exited_normally", map[string]any{
						"char_index": charPos, "slot": slot,
					})
					if err := syncSnapshotAfterExit(log, cfg, ts); err != nil {
						return err
					}
					emitTrans(log, fsm.SnapshotValidate, fsm.Done, "snapshot_validated", map[string]any{
						"char_index": charPos, "slot": slot, "state": fsm.Done,
					})
					return nil
				}
				if err := gracefulLogout(log, cfg, hwnd, charPos); err != nil {
					return err
				}
				emitTrans(log, fsm.GracefulExit, fsm.BNETStart, "next_character", map[string]any{
					"char_index": charPos + 1,
				})
				charPos++
				break
			}

			if errors.Is(err, ErrPluginTimeout) {
				restartCount++
				perCharRetries++
				var budgetErr error
				budgetTrigger := ""
				if restartCount > maxRestartTotal(cfg) {
					budgetTrigger = "restart_budget"
					budgetErr = fmt.Errorf("exceeded retry.max_restart_total (%d)", maxRestartTotal(cfg))
				} else if perCharRetries > maxRetriesPerCharacter(cfg) {
					budgetTrigger = "per_char_retry_budget"
					budgetErr = fmt.Errorf("exceeded retry.max_retries_per_character (%d) for slot %d", maxRetriesPerCharacter(cfg), slot)
				}
				log.Emit("WARN", "transition", "PLUGIN_STUCK: normal quit before retry decision", map[string]any{
					"char_index":        charPos,
					"slot":              slot,
					"restart_seq":       restartCount,
					"per_char_retry":    perCharRetries,
					"max_restart_total": maxRestartTotal(cfg),
					"max_per_character": maxRetriesPerCharacter(cfg),
					"will_retry":        budgetErr == nil,
				})
				// A stuck scan is also shut down through WoW itself. Never force-kill a
				// successful or potentially flushing client.
				emitTrans(log, fsm.WaitPluginScan, fsm.GracefulExit, "scan_timeout", map[string]any{
					"char_index": charPos, "slot": slot,
				})
				if qerr := gracefulQuit(log, cfg, hwnd, pid, charPos); qerr != nil {
					return fmt.Errorf("plugin timeout and normal retry shutdown failed: %w", qerr)
				}
				if budgetErr != nil {
					emitTrans(log, fsm.GracefulExit, fsm.Failed, budgetTrigger, map[string]any{"char_index": charPos})
					return budgetErr
				}
				emitTrans(log, fsm.GracefulExit, fsm.BNETStart, "retry_after_normal_exit", map[string]any{
					"char_index": charPos, "slot": slot,
				})
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
	if cfg.Characters.Mode == "current" {
		emitTrans(log, fsm.WOWForeground, fsm.CharSelect, "keep_current_character", map[string]any{
			"char_index": charPos, "state": fsm.CharSelect,
		})
		return nil
	}

	if charPos == 0 || perCharRetries > 0 {
		emitTrans(log, fsm.WOWForeground, fsm.CharSelect, "start_char_select", map[string]any{
			"char_index": charPos,
			"slot":       slot,
			"state":      fsm.CharSelect,
		})
		return doCharSelect(log, cfg, slot)
	}

	from := fsm.WOWForeground
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
	chord, err := input.ParseChord(keyName)
	if err != nil {
		return fmt.Errorf("key %s (%s): %w", field, keyName, err)
	}
	log.Emit("INFO", "input_key", "key tap", map[string]any{
		"keys":  []string{keyName},
		"field": field,
	})
	return winutil.KeyChord(chord.Modifiers, chord.Key)
}

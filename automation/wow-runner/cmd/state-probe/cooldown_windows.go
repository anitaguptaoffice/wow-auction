//go:build windows

package main

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"time"
)

func hasSnapshot(f addonFrame) bool {
	n, e := strconv.ParseInt(f.Values["total"], 10, 64)
	return e == nil && n > 0
}

// A missing response is only a suspected throttle, never proof of the server's reason.
func cooldownDecision(f addonFrame, now time.Time, wait, cooldown, attempts, max int) (string, time.Time, error) {
	if validCompleted(f) {
		return "complete", time.Time{}, nil
	}
	if hasSnapshot(f) {
		return "processing", time.Time{}, nil
	}
	stamp, e := strconv.ParseInt(f.Values["scanStartedAt"], 10, 64)
	if e != nil || stamp <= 0 || stamp > now.Unix()+5 || f.Values["run"] == "" || f.Values["run"] == "none" {
		return "", time.Time{}, fmt.Errorf("request identity/time unavailable")
	}
	start := time.Unix(stamp, 0).Add(time.Duration(wait) * time.Second)
	until := start.Add(time.Duration(cooldown) * time.Second)
	if now.Before(start) {
		return "waiting", until, nil
	}
	if attempts >= max {
		return "", until, fmt.Errorf("no snapshot after %d cooldown retries; stopped", attempts)
	}
	if now.Before(until) {
		return "cooldown", until, nil
	}
	return "retry", until, nil
}

func (c *collector) watchScan(f addonFrame) (addonFrame, error) {
	attempts := 0
	var processingSince, lastProgress time.Time
	lastState := ""
	for {
		if f.Values["phase"] == "warning" || f.Values["phase"] == "error" {
			return f, fmt.Errorf("addon reported incomplete/error; no logout")
		}
		now := time.Now()
		state, until, e := cooldownDecision(f, now, c.config.NoResponseSeconds, c.config.CooldownSeconds, attempts, *c.config.MaxCooldownRetries)
		if e != nil {
			return f, e
		}
		if state == "complete" {
			return f, nil
		}
		if state == "processing" {
			if processingSince.IsZero() {
				processingSince = now
			}
			if now.Sub(processingSince) > time.Duration(c.config.ScanTimeoutSeconds)*time.Second {
				return f, fmt.Errorf("snapshot processing timed out; no automatic rescan")
			}
		}
		if state != lastState || now.Sub(lastProgress) >= 15*time.Second {
			fields := map[string]any{"session": f.Session, "run": f.Values["run"], "phase": f.Values["phase"], "processed": f.Values["processed"], "total": f.Values["total"], "retryCount": attempts}
			if state == "cooldown" || state == "retry" {
				fields["retryAt"] = until.Format(time.RFC3339)
				fields["remainingSeconds"] = max(0, int(until.Sub(now).Seconds()))
				fields["reason"] = "no_response_suspected_throttle"
			}
			if e = c.event("SCAN_"+state, fields); e != nil {
				return f, e
			}
			lastState, lastProgress = state, now
		}
		if state == "retry" {
			previous := f
			f, e = c.retryScan(previous)
			if e != nil {
				return f, e
			}
			if f.Values["run"] != previous.Values["run"] {
				attempts++
				processingSince = time.Time{}
				lastState = ""
			}
			continue
		}
		ctx, cancel := context.WithTimeout(c.ctx, 6*time.Second)
		f, e = c.fresh(ctx)
		cancel()
		if e != nil {
			return f, fmt.Errorf("scan observation stopped: %w", e)
		}
	}
}

func (c *collector) retryScan(previous addonFrame) (addonFrame, error) {
	f, e := c.wait("before cooldown retry", 5, func(f addonFrame) bool { return true })
	if e != nil {
		return f, e
	}
	if hasSnapshot(f) || validCompleted(f) {
		return f, nil
	}
	if f.Session != previous.Session || f.Values["run"] != previous.Values["run"] {
		return f, fmt.Errorf("scan changed outside controller during cooldown")
	}
	if f.Values["chatOpen"] != "0" || f.Values["keyboardFocus"] != "0" {
		return f, fmt.Errorf("input box open at cooldown expiry; no Escape sent")
	}
	if f.Values["auctionOpen"] == "1" {
		if e = c.event("COOLDOWN_CLOSE_AUCTION", nil); e != nil {
			return f, e
		}
		if e = c.tap(0x1b); e != nil {
			return f, e
		}
		f, e = c.wait("auction closed", 8, func(f addonFrame) bool { return f.Values["auctionOpen"] == "0" || hasSnapshot(f) })
		if e != nil {
			return f, e
		}
		if hasSnapshot(f) {
			return f, nil
		}
	}
	if e = c.prepare(); e != nil {
		return f, e
	}
	f, e = c.wait("auction reopened", 5, func(f addonFrame) bool { return true })
	if e != nil {
		return f, e
	}
	if hasSnapshot(f) {
		return f, nil
	}
	if f.Values["run"] != previous.Values["run"] {
		return f, fmt.Errorf("unexpected request while reopening auction")
	}
	// Reopening does not reset the addon's one-automatic-request-per-login flag.
	// Explicitly request once, then require a new request ID.
	if e = c.command("scan"); e != nil {
		if errors.Is(e, errLateSnapshot) {
			if c.last.Values["chatOpen"] == "1" && c.last.Values["chatCommand"] == "scan" {
				if e = c.tap(0x1b); e != nil {
					return f, e
				}
			}
			return c.wait("late snapshot", 5, func(f addonFrame) bool { return hasSnapshot(f) && f.Values["chatOpen"] == "0" })
		}
		return f, e
	}
	return c.wait("new scan request", 8, func(f addonFrame) bool { return f.Values["run"] != "none" && f.Values["run"] != previous.Values["run"] })
}

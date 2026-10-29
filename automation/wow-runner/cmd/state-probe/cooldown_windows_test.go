//go:build windows

package main

import (
	"testing"
	"time"
)

func TestNoResponseCooldownAndLateArrival(t *testing.T) {
	start := time.Unix(1700000000, 0)
	f := addonFrame{Session: "s", Values: map[string]string{"scanStartedAt": "1700000000", "run": "r", "total": "0", "phase": "started"}}
	for _, test := range []struct {
		seconds int
		want    string
	}{{0, "waiting"}, {59, "waiting"}, {60, "cooldown"}, {959, "cooldown"}, {960, "retry"}} {
		state, until, e := cooldownDecision(f, start.Add(time.Duration(test.seconds)*time.Second), 60, 900, 0, 2)
		if e != nil || state != test.want || until.Unix() != start.Unix()+960 {
			t.Fatalf("at %d: %s %v %v", test.seconds, state, until, e)
		}
	}
	// Closed auction UI does not reset the request time or cause an immediate retry.
	f.Values["phase"] = "ready"
	f.Values["auctionOpen"] = "0"
	state, _, e := cooldownDecision(f, start.Add(300*time.Second), 60, 900, 0, 2)
	if e != nil || state != "cooldown" {
		t.Fatal("closing auction skipped cooldown")
	}
	// A late snapshot cancels the retry even when cooldown has expired.
	f.Values["total"] = "396495"
	f.Values["phase"] = "scanning"
	state, _, e = cooldownDecision(f, start.Add(1000*time.Second), 60, 900, 0, 2)
	if e != nil || state != "processing" {
		t.Fatal("late response would be retried")
	}
	// A slow real scan must never be labelled as a throttle.
	state, _, e = cooldownDecision(f, start.Add(time.Hour), 60, 900, 2, 2)
	if e != nil || state != "processing" {
		t.Fatal("processing entered cooldown")
	}
}

func TestCooldownRetryBudgetAndNewRequestClock(t *testing.T) {
	f := addonFrame{Values: map[string]string{"scanStartedAt": "1700001000", "run": "new-request", "total": "0"}}
	if state, _, e := cooldownDecision(f, time.Unix(1700001001, 0), 60, 900, 2, 2); e != nil || state != "waiting" {
		t.Fatal("last allowed retry was not given response window")
	}
	if _, _, e := cooldownDecision(f, time.Unix(1700001060, 0), 60, 900, 2, 2); e == nil {
		t.Fatal("retry budget ignored")
	}
	f.Values["scanStartedAt"] = "bad"
	if _, _, e := cooldownDecision(f, time.Now(), 60, 900, 0, 2); e == nil {
		t.Fatal("missing timestamp accepted")
	}
}

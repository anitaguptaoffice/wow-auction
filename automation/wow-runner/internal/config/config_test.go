package config

import "testing"

func TestValidateAllowsNearbyInteractionWithoutTargetMacro(t *testing.T) {
	cfg := Root{
		Process: Process{BattleNetExe: "Battle.net.exe", WowExe: "Wow.exe"},
		Keys: Keys{
			InteractTarget: "MOUSEWHEELDOWN",
			LogoutMacro:    "1",
		},
		Characters: Characters{Mode: "current", Indices: []int{0}},
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("Validate() rejected nearby-interaction-only setup: %v", err)
	}
}

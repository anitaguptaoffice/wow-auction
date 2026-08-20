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

func TestValidateSnapshotPipelineRequiresImporterAndGuardsClear(t *testing.T) {
	base := Root{
		Process:    Process{BattleNetExe: "Battle.net.exe", WowExe: "Wow.exe"},
		Keys:       Keys{InteractTarget: "MOUSEWHEELDOWN"},
		Characters: Characters{Mode: "current", Indices: []int{0}},
	}

	cfg := base
	cfg.Snapshot.ClearSourceAfterImport = true
	if err := cfg.Validate(); err == nil {
		t.Fatal("clear without import unexpectedly accepted")
	}

	cfg = base
	cfg.Snapshot.ImportEnabled = true
	if err := cfg.Validate(); err == nil {
		t.Fatal("import without runtime paths unexpectedly accepted")
	}

	cfg.Snapshot.PythonExe = "python.exe"
	cfg.Snapshot.ImporterScript = "import_auction.py"
	cfg.Snapshot.ClearSourceAfterImport = true
	if err := cfg.Validate(); err != nil {
		t.Fatalf("complete snapshot pipeline rejected: %v", err)
	}
}

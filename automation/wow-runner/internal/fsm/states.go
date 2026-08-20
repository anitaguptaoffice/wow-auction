// Package fsm holds FSM state IDs aligned with DEVELOPMENT_PLAN.md §4.
package fsm

const (
	INIT             = "INIT"
	BNETStart        = "BNET_START"
	WOWForeground    = "WOW_FOREGROUND"
	CharSelect       = "CHAR_SELECT"
	EnterWorld       = "ENTER_WORLD"
	AHPrep           = "AH_PREP"
	AHOpen           = "AH_OPEN"
	WaitPluginScan   = "WAIT_PLUGIN_SCAN"
	GracefulExit     = "GRACEFUL_EXIT"
	SnapshotValidate = "SNAPSHOT_VALIDATE"
	CharSelectAgain  = "CHAR_SELECT_AGAIN"
	Done             = "DONE"
	Failed           = "FAILED"
)

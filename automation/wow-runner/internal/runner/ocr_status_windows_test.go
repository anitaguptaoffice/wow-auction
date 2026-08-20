//go:build windows

package runner

import (
	"testing"

	"wow-auction/automation/wow-runner/internal/config"
)

func testOCRConfig() config.OCR {
	return config.OCR{
		WaitingTokens: []string{"AS_WAITING"}, ScanningTokens: []string{"AS_SCANNING"},
		CompleteTokens: []string{"AS_COMPLETE"}, WarningTokens: []string{"AS_WARNING"},
		ErrorTokens: []string{"AS_ERROR"}, StableReads: 2,
	}
}

func TestClassifyOCRStatus(t *testing.T) {
	cfg := testOCRConfig()
	cases := map[string]ocrScanPhase{
		"A S _ W A I T I N G": ocrWaiting,
		"AS_SCANNING 42%":     ocrScanning,
		"AS_COMPLETE":         ocrComplete,
		"AS_WARNING 扫描完成，但存在缺失": ocrWarning,
		"AS_ERROR 扫描未完成":        ocrError,
	}
	for text, want := range cases {
		if got := classifyOCRStatus(text, cfg); got != want {
			t.Errorf("classifyOCRStatus(%q)=%s, want %s", text, got, want)
		}
	}
}

func TestOCRStateRejectsStaleCompletion(t *testing.T) {
	var state scanOCRState
	if done, _, _ := state.observe(ocrComplete, 2); done {
		t.Fatal("stale completion accepted before scanning")
	}
	state.observe(ocrScanning, 2)
	state.observe(ocrComplete, 2)
	done, warning, err := state.observe(ocrComplete, 2)
	if err != nil || !done || warning {
		t.Fatalf("completion not accepted: done=%v warning=%v err=%v", done, warning, err)
	}
}

func TestOCRStateWarningFlushesButDefersValidation(t *testing.T) {
	var state scanOCRState
	state.observe(ocrScanning, 2)
	state.observe(ocrWarning, 2)
	done, warning, err := state.observe(ocrWarning, 2)
	if err != nil || !done || !warning {
		t.Fatalf("warning state: done=%v warning=%v err=%v", done, warning, err)
	}
}

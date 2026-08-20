//go:build windows

// Command wow-ocr is a read-only calibration helper for the runner. It prints
// Windows.Media.Ocr text and word rectangles for the largest visible window of
// an executable; it never clicks or types.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"image"
	"os"
	"time"

	"wow-auction/automation/wow-runner/internal/proc"
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winocr"
	"wow-auction/automation/wow-runner/internal/winutil"
)

func main() {
	exe := flag.String("exe", "Battle.net.exe", "executable name whose largest visible window is read")
	language := flag.String("language", "zh-Hans-CN", "installed Windows OCR language")
	flag.Parse()
	pids, err := proc.PIDsByExe(*exe)
	if err != nil {
		fatal(err)
	}
	pid, hwnd := winutil.FindLargestTopLevelVisibleHWND(pids)
	if hwnd == 0 {
		fatal(fmt.Errorf("no visible window for %s (pids=%v)", *exe, pids))
	}
	if err := winutil.FocusAndVerify(hwnd); err != nil {
		fatal(err)
	}
	img, err := vision.CaptureClient(hwnd)
	if err != nil {
		fatal(err)
	}
	engine, err := winocr.New(*language)
	if err != nil {
		fatal(err)
	}
	defer engine.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	result, err := engine.Recognize(ctx, img, image.Rectangle{})
	if err != nil {
		fatal(err)
	}
	out := struct {
		PID      int32         `json:"pid"`
		HWND     uintptr       `json:"hwnd"`
		Width    int           `json:"width"`
		Height   int           `json:"height"`
		Language string        `json:"language"`
		Text     string        `json:"text"`
		Words    []winocr.Word `json:"words"`
	}{pid, hwnd, img.Bounds().Dx(), img.Bounds().Dy(), result.Language, result.Text, result.Words}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		fatal(err)
	}
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}

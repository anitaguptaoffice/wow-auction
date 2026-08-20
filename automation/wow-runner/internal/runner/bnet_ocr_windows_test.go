//go:build windows

package runner

import (
	"context"
	"image"
	"os"
	"testing"
	"time"

	"wow-auction/automation/wow-runner/internal/proc"
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winocr"
	"wow-auction/automation/wow-runner/internal/winutil"
)

func TestFindOCRLabelBounds(t *testing.T) {
	words := []winocr.Word{
		{Text: "World", Bounds: image.Rect(10, 10, 50, 30)},
		{Text: "of", Bounds: image.Rect(55, 10, 70, 30)},
		{Text: "Warcraft", Bounds: image.Rect(75, 10, 140, 30)},
		{Text: "进", Bounds: image.Rect(200, 100, 220, 130)},
		{Text: "入", Bounds: image.Rect(222, 100, 242, 130)},
		{Text: "游", Bounds: image.Rect(244, 100, 264, 130)},
		{Text: "戏", Bounds: image.Rect(266, 100, 286, 130)},
	}
	if _, label, ok := findOCRLabelBounds(words, []string{"World of Warcraft"}); !ok || label == "" {
		t.Fatal("split English label not found")
	}
	bounds, _, ok := findOCRLabelBounds(words, []string{"进入游戏"})
	if !ok || bounds.Min.X != 200 || bounds.Max.X != 286 {
		t.Fatalf("Chinese label: ok=%v bounds=%v", ok, bounds)
	}
}

func TestAnnouncedMaintenanceWindow(t *testing.T) {
	now := time.Date(2026, 8, 20, 5, 6, 0, 0, time.Local)
	start, end, ok := announcedMaintenanceWindow(
		"我们将于 8 月 20 日凌晨 05：00 开始对《魔兽世界》进行维护，停机维护时间预计为 3 小时左右。",
		now,
	)
	if !ok || start.Hour() != 5 || end.Hour() != 8 || now.Before(start) || !now.Before(end) {
		t.Fatalf("maintenance window: start=%v end=%v ok=%v", start, end, ok)
	}
}

func TestBattleNetOCRLive(t *testing.T) {
	if os.Getenv("WOW_RUNNER_BNET_OCR_TEST") == "" {
		t.Skip("set WOW_RUNNER_BNET_OCR_TEST=1 for the live Battle.net window")
	}
	pids, err := proc.PIDsByExe("Battle.net.exe")
	if err != nil {
		t.Fatal(err)
	}
	_, hwnd := winutil.FindLargestTopLevelVisibleHWND(pids)
	if hwnd == 0 {
		t.Fatal("Battle.net has no visible window")
	}
	if err := winutil.FocusAndVerify(hwnd); err != nil {
		t.Fatal(err)
	}
	img, err := vision.CaptureClient(hwnd)
	if err != nil {
		t.Fatal(err)
	}
	engine, err := winocr.New("zh-Hans-CN")
	if err != nil {
		t.Fatal(err)
	}
	defer engine.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	result, err := engine.Recognize(ctx, img, image.Rectangle{})
	if err != nil {
		t.Fatal(err)
	}
	for _, labels := range [][]string{{"魔兽世界", "World of Warcraft"}, {"进入游戏", "Play"}} {
		bounds, label, ok := findOCRLabelBounds(result.Words, labels)
		if !ok {
			t.Fatalf("did not locate labels %v in OCR text %q", labels, result.Text)
		}
		t.Logf("found %q at %v", label, bounds)
	}
}

//go:build windows

package runner

import (
	"fmt"
	"image/png"
	"os"
	"path/filepath"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winutil"
)

// tryFailureCapture 将 hwnd 客户区截图落盘（debug.failure_capture_dir 非空时）。
func tryFailureCapture(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, tag string) {
	if log == nil || cfg == nil || hwnd == 0 {
		return
	}
	dir := strings.TrimSpace(cfg.Debug.FailureCaptureDir)
	if dir == "" {
		return
	}
	abs := cfg.ResolvePath(dir)
	if err := os.MkdirAll(abs, 0755); err != nil {
		log.Emit("WARN", "debug_capture", "mkdir failure_capture_dir", map[string]any{"path": abs, "error": err.Error()})
		return
	}
	img, err := vision.CaptureClient(hwnd)
	if err != nil {
		log.Emit("WARN", "debug_capture", "capture for failure screenshot", map[string]any{"error": err.Error(), "tag": tag})
		return
	}
	fn := filepath.Join(abs, fmt.Sprintf("fail-%s-%d.png", tag, time.Now().UnixNano()))
	f, err := os.Create(fn)
	if err != nil {
		log.Emit("WARN", "debug_capture", "create failure screenshot", map[string]any{"error": err.Error()})
		return
	}
	defer f.Close()
	if err := png.Encode(f, img); err != nil {
		log.Emit("WARN", "debug_capture", "encode png", map[string]any{"error": err.Error()})
		return
	}
	log.Emit("INFO", "debug_capture", "failure screenshot written", map[string]any{"path": fn, "tag": tag})
}

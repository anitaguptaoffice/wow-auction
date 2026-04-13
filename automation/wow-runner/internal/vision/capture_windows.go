//go:build windows

package vision

import (
	"fmt"
	"image"

	"github.com/kbinani/screenshot"

	"wow-auction/automation/wow-runner/internal/winutil"
)

// CaptureClient captures the entire client area of hwnd as RGBA (screen coordinates).
func CaptureClient(hwnd winutil.HWND) (*image.RGBA, error) {
	l, t, w, h, err := winutil.ClientAreaScreenBounds(hwnd)
	if err != nil {
		return nil, err
	}
	if w <= 0 || h <= 0 {
		return nil, fmt.Errorf("empty client area")
	}
	r := image.Rect(int(l), int(t), int(l+w), int(t+h))
	img, err := screenshot.CaptureRect(r)
	if err != nil {
		return nil, fmt.Errorf("screenshot: %w", err)
	}
	return img, nil
}

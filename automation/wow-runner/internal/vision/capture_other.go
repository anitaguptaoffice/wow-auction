//go:build !windows

package vision

import (
	"fmt"
	"image"

	"wow-auction/automation/wow-runner/internal/winutil"
)

// CaptureClient is only implemented on Windows for WoW automation.
func CaptureClient(hwnd winutil.HWND) (*image.RGBA, error) {
	_ = hwnd
	return nil, fmt.Errorf("vision: CaptureClient not supported on this platform")
}

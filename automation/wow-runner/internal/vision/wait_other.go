//go:build !windows

package vision

import (
	"fmt"
	"image"
	"time"

	"wow-auction/automation/wow-runner/internal/winutil"
)

// MatchOnce is only available on Windows.
func MatchOnce(hwnd winutil.HWND, tmplPath string, roi image.Rectangle, opts *MatchOptions) (score float64, at image.Point, err error) {
	_, _, _, _ = hwnd, tmplPath, roi, opts
	return 0, image.Point{}, fmt.Errorf("vision: MatchOnce not supported on this platform")
}

// WaitForMatch is only available on Windows.
func WaitForMatch(hwnd winutil.HWND, tmplPath string, roi image.Rectangle, threshold float64, poll time.Duration, deadline time.Time, opts *MatchOptions) (ok bool, lastScore float64, at image.Point, err error) {
	_, _, _, _, _, _, _ = hwnd, tmplPath, roi, threshold, poll, deadline, opts
	return false, 0, image.Point{}, fmt.Errorf("vision: WaitForMatch not supported on this platform")
}

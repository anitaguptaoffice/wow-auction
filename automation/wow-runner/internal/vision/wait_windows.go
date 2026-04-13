//go:build windows

package vision

import (
	"fmt"
	"image"
	"time"

	"wow-auction/automation/wow-runner/internal/winutil"
)

// MatchOnce captures the client area and returns best template similarity in [0,1] and match origin in screen capture coords (same as client image coords).
func MatchOnce(hwnd winutil.HWND, tmplPath string, roi image.Rectangle, opts *MatchOptions) (score float64, at image.Point, err error) {
	if tmplPath == "" {
		return 0, image.Point{}, fmt.Errorf("empty template path")
	}
	if opts == nil {
		opts = DefaultMatchOptions()
	}
	scr, err := CaptureClient(hwnd)
	if err != nil {
		return 0, image.Point{}, err
	}
	tmpl, err := LoadPNG(tmplPath)
	if err != nil {
		return 0, image.Point{}, err
	}
	score, at, ok := BestMatch(scr, tmpl, roi, opts)
	if !ok {
		return 0, image.Point{}, fmt.Errorf("template larger than ROI or invalid geometry")
	}
	return score, at, nil
}

// WaitForMatch polls until score >= threshold or deadline.
func WaitForMatch(hwnd winutil.HWND, tmplPath string, roi image.Rectangle, threshold float64, poll time.Duration, deadline time.Time, opts *MatchOptions) (ok bool, lastScore float64, at image.Point, err error) {
	if opts == nil {
		opts = DefaultMatchOptions()
	}
	var last image.Point
	for time.Now().Before(deadline) {
		s, p, e := MatchOnce(hwnd, tmplPath, roi, opts)
		if e != nil {
			return false, 0, image.Point{}, e
		}
		last = p
		lastScore = s
		if s >= threshold {
			return true, s, p, nil
		}
		if poll > 0 {
			time.Sleep(poll)
		}
	}
	return false, lastScore, last, nil
}

//go:build windows

package runner

import (
	"image"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/vision"
)

func visionOpts(cfg *config.Root) *vision.MatchOptions {
	o := vision.DefaultMatchOptions()
	switch strings.ToLower(strings.TrimSpace(cfg.Vision.MatchMethod)) {
	case "rgb_mean", "rgb", "legacy":
		o.Method = vision.MatchMethodRGBMean
	case "ncc", "":
		o.Method = vision.MatchMethodNCC
	default:
		o.Method = vision.MatchMethodNCC
	}
	if cfg.Vision.ColorGateMaxAvgChannelDiff > 0 {
		o.ColorGateMaxAvgChannelDiff = cfg.Vision.ColorGateMaxAvgChannelDiff
	}
	return o
}

func bnetSearchROI(cfg *config.Root) image.Rectangle {
	r := cfg.Bnet.SearchROI
	if r == nil || r.W <= 0 || r.H <= 0 {
		return image.Rectangle{}
	}
	return image.Rect(r.X, r.Y, r.X+r.W, r.Y+r.H)
}

func bnetUIReadyTimeout(cfg *config.Root) time.Duration {
	s := cfg.Timeouts.BnetUIReady
	if s <= 0 {
		s = 45
	}
	return time.Duration(s) * time.Second
}

func charSelectGateTimeout(cfg *config.Root) time.Duration {
	s := cfg.Timeouts.CharSelect
	if s <= 0 {
		s = 60
	}
	return time.Duration(s) * time.Second
}

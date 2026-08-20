//go:build windows

package runner

import (
	"context"
	"fmt"
	"image"
	"regexp"
	"sort"
	"strconv"
	"time"
	"unicode/utf8"

	"wow-auction/automation/wow-runner/internal/config"
	"wow-auction/automation/wow-runner/internal/logx"
	"wow-auction/automation/wow-runner/internal/vision"
	"wow-auction/automation/wow-runner/internal/winocr"
	"wow-auction/automation/wow-runner/internal/winutil"
)

var maintenancePattern = regexp.MustCompile(`(?s)(\d{1,2})月(\d{1,2})日.*?(\d{1,2})[:：](\d{2})开始.*?维护时间.*?(\d{1,2})小时`)

func announcedMaintenanceWindow(text string, now time.Time) (time.Time, time.Time, bool) {
	compact := winocr.CompactText(text)
	match := maintenancePattern.FindStringSubmatch(compact)
	if len(match) != 6 {
		return time.Time{}, time.Time{}, false
	}
	values := make([]int, 5)
	for index := range values {
		value, err := strconv.Atoi(match[index+1])
		if err != nil {
			return time.Time{}, time.Time{}, false
		}
		values[index] = value
	}
	month, day, hour, minute, durationHours := values[0], values[1], values[2], values[3], values[4]
	if month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || durationHours < 1 {
		return time.Time{}, time.Time{}, false
	}
	start := time.Date(now.Year(), time.Month(month), day, hour, minute, 0, 0, now.Location())
	// Handle an announcement around New Year without requiring a year in the UI.
	if start.Sub(now) > 180*24*time.Hour {
		start = start.AddDate(-1, 0, 0)
	} else if now.Sub(start) > 180*24*time.Hour {
		start = start.AddDate(1, 0, 0)
	}
	return start, start.Add(time.Duration(durationHours) * time.Hour), true
}

func findOCRLabelBounds(words []winocr.Word, labels []string) (image.Rectangle, string, bool) {
	ordered := append([]winocr.Word(nil), words...)
	sort.SliceStable(ordered, func(i, j int) bool {
		ic, jc := ordered[i].Bounds.Min.Y+ordered[i].Bounds.Max.Y, ordered[j].Bounds.Min.Y+ordered[j].Bounds.Max.Y
		if delta := ic - jc; delta < -12 || delta > 12 {
			return ic < jc
		}
		return ordered[i].Bounds.Min.X < ordered[j].Bounds.Min.X
	})
	for _, label := range labels {
		target := machineText(label)
		if target == "" {
			continue
		}
		for i := range ordered {
			combined := ""
			bounds := image.Rectangle{}
			baseCenter := (ordered[i].Bounds.Min.Y + ordered[i].Bounds.Max.Y) / 2
			for j := i; j < len(ordered) && j < i+5; j++ {
				center := (ordered[j].Bounds.Min.Y + ordered[j].Bounds.Max.Y) / 2
				if center-baseCenter > 18 || baseCenter-center > 18 {
					break
				}
				combined += machineText(ordered[j].Text)
				if bounds.Empty() {
					bounds = ordered[j].Bounds
				} else {
					bounds = bounds.Union(ordered[j].Bounds)
				}
				matches := combined == target
				if !matches && utf8.RuneCountInString(target) >= 4 {
					matches = len(combined) >= len(target) && containsCanonical(combined, target)
				}
				if matches {
					return bounds, label, true
				}
			}
		}
	}
	return image.Rectangle{}, "", false
}

func containsCanonical(value, target string) bool {
	for i := 0; i+len(target) <= len(value); i++ {
		if value[i:i+len(target)] == target {
			return true
		}
	}
	return false
}

func clickBattleNetOCRLabel(log *logx.Logger, cfg *config.Root, hwnd winutil.HWND, labels []string, action string, deadline time.Time) error {
	engine, err := winocr.New(cfg.OCR.Language)
	if err != nil {
		return err
	}
	defer engine.Close()
	originL, originT, _, _, err := winutil.ClientAreaScreenBounds(hwnd)
	if err != nil {
		return err
	}
	for time.Now().Before(deadline) {
		img, err := vision.CaptureClient(hwnd)
		if err != nil {
			return err
		}
		ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		result, err := engine.Recognize(ctx, img, image.Rectangle{})
		cancel()
		if err != nil {
			return fmt.Errorf("Battle.net OCR: %w", err)
		}
		if start, end, announced := announcedMaintenanceWindow(result.Text, time.Now()); announced && !time.Now().Before(start) && time.Now().Before(end) {
			return fmt.Errorf("%w: announced window %s–%s", ErrServerMaintenance, start.Format(time.RFC3339), end.Format(time.RFC3339))
		}
		bounds, label, ok := findOCRLabelBounds(result.Words, labels)
		if ok {
			x := originL + int32((bounds.Min.X+bounds.Max.X)/2)
			y := originT + int32((bounds.Min.Y+bounds.Max.Y)/2)
			if err := winutil.FocusAndVerify(hwnd); err != nil {
				return err
			}
			log.Emit("INFO", "ocr_click", action, map[string]any{
				"label": label, "x": x, "y": y, "word_count": len(result.Words),
			})
			if err := winutil.Click(x, y); err != nil {
				return err
			}
			return nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	return fmt.Errorf("Battle.net OCR could not find %s labels %v", action, labels)
}

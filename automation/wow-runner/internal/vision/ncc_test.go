package vision

import (
	"image"
	"image/color"
	"testing"
)

func TestBestMatch_NCC_identicalPatch(t *testing.T) {
	const n = 32
	c := color.RGBA{R: 80, G: 120, B: 200, A: 255}
	screen := image.NewRGBA(image.Rect(0, 0, n, n))
	tmpl := image.NewRGBA(image.Rect(0, 0, 8, 8))
	for y := 0; y < n; y++ {
		for x := 0; x < n; x++ {
			screen.SetRGBA(x, y, c)
		}
	}
	for y := 0; y < 8; y++ {
		for x := 0; x < 8; x++ {
			tmpl.SetRGBA(x, y, c)
		}
	}
	score, at, ok := BestMatch(screen, tmpl, image.Rect(0, 0, n, n), &MatchOptions{Method: MatchMethodNCC})
	if !ok || score < 0.999 {
		t.Fatalf("ncc want ~1, got ok=%v score=%v at=%v", ok, score, at)
	}
	if at.X != 0 || at.Y != 0 {
		t.Fatalf("at %+v", at)
	}
}

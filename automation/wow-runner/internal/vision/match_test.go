package vision

import (
	"image"
	"image/color"
	"testing"
)

func TestBestSimilarity_identicalPatch(t *testing.T) {
	// 8x8 gray screen; template = top-left 4x4 same color
	const n = 8
	c := color.RGBA{R: 100, G: 50, B: 200, A: 255}
	screen := image.NewRGBA(image.Rect(0, 0, n, n))
	tmpl := image.NewRGBA(image.Rect(0, 0, 4, 4))
	for y := 0; y < n; y++ {
		for x := 0; x < n; x++ {
			screen.SetRGBA(x, y, c)
		}
	}
	for y := 0; y < 4; y++ {
		for x := 0; x < 4; x++ {
			tmpl.SetRGBA(x, y, c)
		}
	}
	score, at, ok := BestSimilarity(screen, tmpl, image.Rect(0, 0, n, n))
	if !ok || score < 0.999 {
		t.Fatalf("want ~1.0 score, got ok=%v score=%v at=%v", ok, score, at)
	}
	if at.X != 0 || at.Y != 0 {
		t.Fatalf("at %v", at)
	}
}

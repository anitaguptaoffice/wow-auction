//go:build windows

package winocr

import (
	"context"
	"image"
	"image/color"
	_ "image/jpeg"
	_ "image/png"
	"os"
	"strings"
	"testing"
	"time"
)

func TestPackBGRAClipsROIAndRemovesStride(t *testing.T) {
	img := image.NewRGBA(image.Rect(10, 20, 14, 23))
	img.SetRGBA(11, 21, colorRGBA(1, 2, 3, 4))
	pixels, width, height, err := packBGRA(img, image.Rect(11, 21, 13, 23))
	if err != nil {
		t.Fatal(err)
	}
	if width != 2 || height != 2 || len(pixels) != 16 {
		t.Fatalf("got %dx%d, %d bytes", width, height, len(pixels))
	}
	if got := pixels[:4]; got[0] != 3 || got[1] != 2 || got[2] != 1 || got[3] != 255 {
		t.Fatalf("first BGRA pixel = %v", got)
	}
}

func colorRGBA(r, g, b, a byte) color.RGBA {
	return color.RGBA{R: r, G: g, B: b, A: a}
}

// Set WOW_OCR_TEST_IMAGE to run this against a real screenshot. Optional
// WOW_OCR_EXPECT is a comma-separated list of normalized substrings.
func TestWindowsOCRIntegration(t *testing.T) {
	path := os.Getenv("WOW_OCR_TEST_IMAGE")
	if path == "" {
		t.Skip("WOW_OCR_TEST_IMAGE is not set")
	}
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	img, _, err := image.Decode(f)
	if err != nil {
		t.Fatal(err)
	}
	engine, err := New(defaultLanguage)
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
	t.Logf("language=%s words=%d text=%q", result.Language, len(result.Words), result.Text)
	normalized := CompactText(result.Text)
	for _, expected := range strings.Split(os.Getenv("WOW_OCR_EXPECT"), ",") {
		expected = CompactText(expected)
		if expected != "" && !strings.Contains(normalized, expected) {
			t.Errorf("OCR text %q does not contain %q", normalized, expected)
		}
	}
}

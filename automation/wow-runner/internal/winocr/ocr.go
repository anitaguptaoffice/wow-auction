// Package winocr exposes the Windows built-in OCR engine behind a small,
// testable Go API. The Windows implementation consumes image pixels directly;
// it does not write screenshots to disk or start a helper process.
package winocr

import (
	"context"
	"errors"
	"image"
	"strings"
	"unicode"
)

var (
	// ErrClosed is returned after an Engine has been closed.
	ErrClosed = errors.New("winocr: engine is closed")
	// ErrUnsupported is returned on platforms without Windows.Media.Ocr.
	ErrUnsupported = errors.New("winocr: Windows.Media.Ocr is unavailable on this platform")
)

// Result is one OCR pass over an image ROI. Word bounds are relative to the
// cropped ROI, not to the original image.
type Result struct {
	Text     string
	Words    []Word
	Language string
}

// Word is one recognized word and its pixel bounds within the OCR ROI.
type Word struct {
	Text   string
	Bounds image.Rectangle
}

// Recognizer is the interface consumed by runner code and easily replaced by
// a deterministic fake in tests.
type Recognizer interface {
	Recognize(context.Context, image.Image, image.Rectangle) (Result, error)
	Close() error
}

// NormalizeText performs the stable subset of Unicode normalization needed by
// the status parser: full-width ASCII becomes ASCII, letters are lower-cased,
// and every whitespace run becomes one ordinary space.
//
// It deliberately keeps punctuation. Call CompactText when matching Chinese
// phrases that Windows OCR may split with spaces.
func NormalizeText(value string) string {
	var b strings.Builder
	b.Grow(len(value))
	spacePending := false
	for _, r := range value {
		switch {
		case r == '\u3000':
			r = ' '
		case r >= '\uff01' && r <= '\uff5e':
			r -= 0xfee0
		}
		if unicode.IsSpace(r) {
			spacePending = b.Len() > 0
			continue
		}
		if spacePending {
			b.WriteByte(' ')
			spacePending = false
		}
		b.WriteRune(unicode.ToLower(r))
	}
	return b.String()
}

// CompactText is NormalizeText with all remaining Unicode whitespace removed.
// It is useful for phrases such as "扫描完成", which OCR may return as
// "扫 描 完 成".
func CompactText(value string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsSpace(r) {
			return -1
		}
		return r
	}, NormalizeText(value))
}

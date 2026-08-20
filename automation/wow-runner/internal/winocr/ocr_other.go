//go:build !windows

package winocr

import (
	"context"
	"image"
)

// Engine is a non-Windows placeholder so packages can compile cross-platform.
type Engine struct{}

// New returns ErrUnsupported outside Windows.
func New(language string) (*Engine, error) {
	_ = language
	return nil, ErrUnsupported
}

// Recognize returns ErrUnsupported outside Windows.
func (*Engine) Recognize(ctx context.Context, src image.Image, roi image.Rectangle) (Result, error) {
	_, _, _ = ctx, src, roi
	return Result{}, ErrUnsupported
}

// Close is a no-op for the placeholder.
func (*Engine) Close() error { return nil }

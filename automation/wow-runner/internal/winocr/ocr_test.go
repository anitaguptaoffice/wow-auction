package winocr

import (
	"errors"
	"testing"
)

func TestNormalizeText(t *testing.T) {
	got := NormalizeText("  ＡＳ：ＳＣＡＮ\t４１％\r\n 扫 描 完 成  ")
	want := "as:scan 41% 扫 描 完 成"
	if got != want {
		t.Fatalf("NormalizeText() = %q, want %q", got, want)
	}
	if got := CompactText(" 扫 描\n完 成 "); got != "扫描完成" {
		t.Fatalf("CompactText() = %q", got)
	}
}

func TestErrorsAreDistinct(t *testing.T) {
	if errors.Is(ErrClosed, ErrUnsupported) {
		t.Fatal("ErrClosed unexpectedly matches ErrUnsupported")
	}
}

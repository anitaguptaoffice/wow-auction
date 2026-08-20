package input

import "testing"

func TestVK(t *testing.T) {
	tests := []struct {
		in   string
		want uint16
	}{
		{"1", 0x31},
		{"0", 0x30},
		{"Enter", 0x0D},
		{"Home", 0x24},
		{"Down", 0x28},
		{"f1", 0x70},
	}
	for _, tc := range tests {
		got, err := VK(tc.in)
		if err != nil || got != tc.want {
			t.Fatalf("%q: got %x err %v want %x", tc.in, got, err, tc.want)
		}
	}
}

func TestParseChord(t *testing.T) {
	chord, err := ParseChord("ALT-CTRL-H")
	if err != nil {
		t.Fatal(err)
	}
	if chord.Key != 0x48 || len(chord.Modifiers) != 2 || chord.Modifiers[0] != 0x12 || chord.Modifiers[1] != 0x11 {
		t.Fatalf("unexpected chord: %+v", chord)
	}
	if _, err := ParseChord("Ctrl+NoSuchKey"); err == nil {
		t.Fatal("expected invalid key error")
	}
}

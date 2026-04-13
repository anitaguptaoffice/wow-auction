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

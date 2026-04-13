package proc

import "testing"

func TestExeMatch(t *testing.T) {
	cases := []struct {
		want, got string
		ok        bool
	}{
		{"Wow.exe", "Wow.exe", true},
		{"Wow.exe", "wow.exe", true},
		{"Wow", "Wow.exe", true},
		{"Wow.exe", "Wow", true},
		{"Battle.net.exe", "Battle.net.exe", true},
		{"Wow.exe", "Notepad.exe", false},
	}
	for _, c := range cases {
		if exeMatch(c.want, c.got) != c.ok {
			t.Fatalf("exeMatch(%q,%q) want %v", c.want, c.got, c.ok)
		}
	}
}

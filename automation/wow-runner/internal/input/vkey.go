package input

import (
	"fmt"
	"strconv"
	"strings"
)

// Chord is an ordered set of modifier virtual keys and one primary key.
type Chord struct {
	Modifiers []uint16
	Key       uint16
}

// VK maps config key names (WoW bind style) to Windows virtual-key codes.
// Extend as needed for your binds.
func VK(name string) (uint16, error) {
	s := strings.TrimSpace(name)
	if s == "" {
		return 0, fmt.Errorf("empty key name")
	}
	// single character digit
	if len(s) == 1 && s[0] >= '0' && s[0] <= '9' {
		if s[0] == '0' {
			return 0x30, nil
		}
		return uint16(0x30 + (s[0] - '0')), nil
	}
	if len(s) == 1 && ((s[0] >= 'a' && s[0] <= 'z') || (s[0] >= 'A' && s[0] <= 'Z')) {
		return uint16(0x41 + (strings.ToUpper(s)[0] - 'A')), nil
	}
	switch strings.ToLower(s) {
	case "enter", "return":
		return 0x0D, nil
	case "esc", "escape":
		return 0x1B, nil
	case "space":
		return 0x20, nil
	case "home":
		return 0x24, nil
	case "left":
		return 0x25, nil
	case "up":
		return 0x26, nil
	case "right":
		return 0x27, nil
	case "down":
		return 0x28, nil
	case "tab":
		return 0x09, nil
	}
	if len(s) > 1 && (s[0] == 'f' || s[0] == 'F') {
		n, err := strconv.Atoi(s[1:])
		if err == nil && n >= 1 && n <= 24 {
			return uint16(0x70 + n - 1), nil // F1=0x70
		}
	}
	return 0, fmt.Errorf("unknown key name: %q", name)
}

// ParseChord accepts WoW-style ALT-CTRL-H and common Ctrl+Alt+H forms.
func ParseChord(name string) (Chord, error) {
	parts := strings.FieldsFunc(strings.TrimSpace(name), func(r rune) bool { return r == '+' || r == '-' })
	if len(parts) == 0 {
		return Chord{}, fmt.Errorf("empty key chord")
	}
	seen := map[uint16]bool{}
	chord := Chord{}
	for _, raw := range parts[:len(parts)-1] {
		var modifier uint16
		switch strings.ToLower(strings.TrimSpace(raw)) {
		case "ctrl", "control":
			modifier = 0x11
		case "alt":
			modifier = 0x12
		case "shift":
			modifier = 0x10
		case "win", "windows", "meta":
			modifier = 0x5B
		default:
			return Chord{}, fmt.Errorf("unknown modifier %q in %q", raw, name)
		}
		if !seen[modifier] {
			chord.Modifiers = append(chord.Modifiers, modifier)
			seen[modifier] = true
		}
	}
	key, err := VK(parts[len(parts)-1])
	if err != nil {
		return Chord{}, err
	}
	if seen[key] {
		return Chord{}, fmt.Errorf("primary key duplicates a modifier in %q", name)
	}
	chord.Key = key
	return chord, nil
}

package input

import (
	"fmt"
	"strconv"
	"strings"
)

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

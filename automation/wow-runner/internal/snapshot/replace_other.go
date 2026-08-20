//go:build !windows

package snapshot

import "os"

func replaceFile(source, destination string) error {
	return os.Rename(source, destination)
}

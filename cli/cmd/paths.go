package cmd

import (
	"os"
	"path/filepath"
)

// absPath resolves p to an absolute path relative to the current working directory.
// It returns p unchanged if os.Getwd() fails or p is already absolute.
func absPath(p string) string {
	if filepath.IsAbs(p) {
		return p
	}
	cwd, err := os.Getwd()
	if err != nil {
		return p
	}
	return filepath.Clean(filepath.Join(cwd, p))
}

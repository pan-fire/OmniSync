package main

import (
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// The release build (Makefile, .github/workflows/release.yml) sets the
// version and commit with -X main.version / -X main.commit; this proves those
// names still reach `--version`.
func TestLdflagsSetVersionAndCommit(t *testing.T) {
	if testing.Short() {
		t.Skip("builds the binary")
	}
	bin := filepath.Join(t.TempDir(), "osync")
	if runtime.GOOS == "windows" {
		bin += ".exe"
	}
	build := exec.Command("go", "build",
		"-ldflags", "-X main.version=1.2.3-test -X main.commit=deadbee",
		"-o", bin, ".")
	if out, err := build.CombinedOutput(); err != nil {
		t.Fatalf("go build: %v\n%s", err, out)
	}
	out, err := exec.Command(bin, "--version").CombinedOutput()
	if err != nil {
		t.Fatalf("--version: %v\n%s", err, out)
	}
	if got, want := strings.TrimSpace(string(out)), "osync version 1.2.3-test (commit deadbee)"; got != want {
		t.Errorf("--version = %q, want %q", got, want)
	}
}

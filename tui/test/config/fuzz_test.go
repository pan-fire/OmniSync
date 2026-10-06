package config_test

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/config"
	"github.com/spf13/cobra"
)

// Whatever tui.toml holds (hand-edited, truncated, another program's
// file), Load does not panic, and the flags still win: a --url given on the
// command line is the URL used.
func FuzzLoad(f *testing.F) {
	base := f.TempDir()
	switch runtime.GOOS {
	case "darwin", "ios":
		f.Setenv("HOME", base)
	case "windows":
		f.Setenv("AppData", base)
	default:
		f.Setenv("XDG_CONFIG_HOME", base)
	}
	for _, k := range []string{"OMNISYNC_URL", "OMNISYNC_API_KEY", "OMNISYNC_THEME", "OMNISYNC_ASCII_MODE", "OMNISYNC_LOG_FILE", "OMNISYNC_NO_MOUSE"} {
		f.Setenv(k, "")
		_ = os.Unsetenv(k)
	}
	dir, err := config.Dir()
	if err != nil {
		f.Fatal(err)
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		f.Fatal(err)
	}
	path := filepath.Join(dir, "tui.toml")
	const flagURL = "https://flag.example.org"
	f.Fuzz(func(t *testing.T, content []byte) {
		if err := os.WriteFile(path, content, 0o600); err != nil {
			t.Fatal(err)
		}
		if _, err := config.Load(nil); err != nil {
			return // a value of the wrong type: Load says so, the caller warns
		}
		cmd := &cobra.Command{}
		cmd.Flags().String("url", "", "")
		if err := cmd.Flags().Set("url", flagURL); err != nil {
			t.Fatal(err)
		}
		cfg, err := config.Load(cmd)
		if err != nil {
			t.Fatalf("Load with --url failed where Load without did not: %v", err)
		}
		if cfg.URL != flagURL {
			t.Errorf("URL = %q, want the flag's %q", cfg.URL, flagURL)
		}
	})
}

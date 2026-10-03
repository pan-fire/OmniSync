package config_test

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/config"
	"github.com/spf13/cobra"
)

// isolate points os.UserConfigDir() at a temp dir and clears the env vars
// Load reads, so the developer's own config cannot leak into a test. It
// returns the osync config directory (not created).
func isolate(t *testing.T) string {
	t.Helper()
	base := t.TempDir()
	switch runtime.GOOS {
	case "darwin", "ios":
		t.Setenv("HOME", base)
		base = filepath.Join(base, "Library", "Application Support")
	case "windows":
		t.Setenv("AppData", base)
	default:
		t.Setenv("XDG_CONFIG_HOME", base)
	}
	for _, k := range []string{"OMNISYNC_URL", "OMNISYNC_API_KEY", "OMNISYNC_THEME", "OMNISYNC_ASCII_MODE", "OMNISYNC_LOG_FILE", "OMNISYNC_NO_MOUSE"} {
		t.Setenv(k, "")
		_ = os.Unsetenv(k)
	}
	return filepath.Join(base, "osync")
}

func writeConfig(t *testing.T, dir, content string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "tui.toml"), []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestDir_UsesUserConfigDir(t *testing.T) {
	want := isolate(t)
	got, err := config.Dir()
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Errorf("Dir() = %q, want %q", got, want)
	}
}

func TestLoad_Defaults(t *testing.T) {
	isolate(t)
	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatalf("Load() error: %v", err)
	}
	if cfg.URL != "http://127.0.0.1:8000" {
		t.Errorf("URL = %q, want %q", cfg.URL, "http://127.0.0.1:8000")
	}
	if cfg.APIKey != "" {
		t.Errorf("APIKey = %q, want empty", cfg.APIKey)
	}
	if cfg.Theme != "dark" {
		t.Errorf("Theme = %q, want %q", cfg.Theme, "dark")
	}
	if cfg.ASCIIMode {
		t.Error("ASCIIMode = true, want false")
	}
	if cfg.LogFile != "" {
		t.Errorf("LogFile = %q, want empty", cfg.LogFile)
	}
	if cfg.NoMouse {
		t.Error("NoMouse = true, want false (mouse on by default)")
	}
}

// The mouse can be left to the terminal by env var or flag.
func TestLoad_NoMouse(t *testing.T) {
	isolate(t)
	t.Setenv("OMNISYNC_NO_MOUSE", "true")
	cfg, err := config.Load(nil)
	if err != nil || !cfg.NoMouse {
		t.Fatalf("env: NoMouse = %v, err %v", cfg != nil && cfg.NoMouse, err)
	}
	isolate(t)
	cmd := newCmd()
	if err = cmd.Flags().Set("no-mouse", "true"); err != nil {
		t.Fatal(err)
	}
	if cfg, err = config.Load(cmd); err != nil || !cfg.NoMouse {
		t.Errorf("flag: NoMouse = %v, err %v", cfg != nil && cfg.NoMouse, err)
	}
}

func TestLoad_EnvVarOverride(t *testing.T) {
	isolate(t)
	t.Setenv("OMNISYNC_URL", "http://envhost:9000")
	t.Setenv("OMNISYNC_API_KEY", "env-secret")

	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatalf("Load() error: %v", err)
	}
	if cfg.URL != "http://envhost:9000" {
		t.Errorf("URL = %q, want %q", cfg.URL, "http://envhost:9000")
	}
	if cfg.APIKey != "env-secret" {
		t.Errorf("APIKey = %q, want %q", cfg.APIKey, "env-secret")
	}
}

func newCmd() *cobra.Command {
	cmd := &cobra.Command{}
	cmd.Flags().String("url", "", "")
	cmd.Flags().String("api-key", "", "")
	cmd.Flags().Bool("ascii", false, "")
	cmd.Flags().String("theme", "", "")
	cmd.Flags().Bool("no-mouse", false, "")
	return cmd
}

func TestLoad_FlagOverridesEnv(t *testing.T) {
	isolate(t)
	t.Setenv("OMNISYNC_URL", "http://envhost:9000")
	t.Setenv("OMNISYNC_API_KEY", "env-secret")

	cmd := newCmd()
	for flag, value := range map[string]string{"url": "http://flaghost:7000", "api-key": "flag-secret", "ascii": "true", "theme": "light"} {
		if err := cmd.Flags().Set(flag, value); err != nil {
			t.Fatal(err)
		}
	}

	cfg, err := config.Load(cmd)
	if err != nil {
		t.Fatalf("Load() error: %v", err)
	}
	if cfg.URL != "http://flaghost:7000" || cfg.APIKey != "flag-secret" || !cfg.ASCIIMode || cfg.Theme != "light" {
		t.Errorf("flags not applied: %+v", cfg)
	}
}

func TestLoad_TOMLFile(t *testing.T) {
	dir := isolate(t)
	writeConfig(t, dir, `url = "http://tomlhost:5000"
api_key = "file-secret"
theme = "light"
ascii_mode = true
`)

	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatalf("Load() error: %v", err)
	}
	if cfg.URL != "http://tomlhost:5000" {
		t.Errorf("URL = %q, want %q", cfg.URL, "http://tomlhost:5000")
	}
	if cfg.APIKey != "file-secret" {
		t.Errorf("APIKey = %q, want file-secret", cfg.APIKey)
	}
	if cfg.Theme != "light" {
		t.Errorf("Theme = %q, want %q", cfg.Theme, "light")
	}
	if !cfg.ASCIIMode {
		t.Error("ASCIIMode = false, want true")
	}
}

func TestLoad_EnvOverridesFile(t *testing.T) {
	dir := isolate(t)
	writeConfig(t, dir, `api_key = "file-secret"`)
	t.Setenv("OMNISYNC_API_KEY", "env-secret")

	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.APIKey != "env-secret" {
		t.Errorf("APIKey = %q, want env-secret", cfg.APIKey)
	}
}

// A tui.toml in the working directory must be ignored: it could redirect the
// TUI (and its API key) to another backend.
func TestLoad_IgnoresWorkingDirectory(t *testing.T) {
	isolate(t)
	cwd := t.TempDir()
	writeConfig(t, cwd, `url = "http://evil.example:1"`+"\n"+`api_key = "stolen"`)
	orig, _ := os.Getwd()
	if err := os.Chdir(cwd); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(orig) })

	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.URL != config.DefaultURL || cfg.APIKey != "" {
		t.Errorf("working-directory tui.toml was read: %+v", cfg)
	}
}

func TestSaveTheme_RoundTrip(t *testing.T) {
	dir := isolate(t)
	writeConfig(t, dir, `url = "http://keep:1"`)

	if err := config.SaveTheme("light"); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Theme != "light" {
		t.Errorf("Theme = %q, want light", cfg.Theme)
	}
	if cfg.URL != "http://keep:1" {
		t.Errorf("SaveTheme dropped other settings: URL = %q", cfg.URL)
	}
}

func TestSaveTheme_CreatesOwnerOnlyFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("no Unix permission bits on Windows")
	}
	dir := isolate(t)
	if err := config.SaveTheme("light"); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(filepath.Join(dir, "tui.toml"))
	if err != nil {
		t.Fatal(err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Errorf("new tui.toml mode = %04o, want 0600", perm)
	}
}

func TestSaveTheme_TightensExistingFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("no Unix permission bits on Windows")
	}
	dir := isolate(t)
	writeConfig(t, dir, `api_key = "secret"`)
	path := filepath.Join(dir, "tui.toml")
	if err := os.Chmod(path, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := config.SaveTheme("light"); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 {
		t.Errorf("tui.toml mode after SaveTheme = %04o, want 0600", perm)
	}
	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.APIKey != "secret" || cfg.Theme != "light" {
		t.Errorf("SaveTheme lost settings: %+v", cfg)
	}
	// No temp files are left next to the config.
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 {
		t.Errorf("config dir has %d entries, want only tui.toml", len(entries))
	}
}

func hasWarning(cfg *config.Config, substr string) bool {
	for _, w := range cfg.Warnings {
		if strings.Contains(w, substr) {
			return true
		}
	}
	return false
}

func TestLoad_WarnsAboutReadableFileWithKey(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("no Unix permission bits on Windows")
	}
	dir := isolate(t)
	writeConfig(t, dir, `api_key = "secret"`)
	path := filepath.Join(dir, "tui.toml")
	if err := os.Chmod(path, 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatal(err)
	}
	if !hasWarning(cfg, "chmod 600") {
		t.Errorf("no warning for a group/world-readable file holding api_key: %q", cfg.Warnings)
	}

	if err := os.Chmod(path, 0o600); err != nil {
		t.Fatal(err)
	}
	if cfg, _ = config.Load(nil); len(cfg.Warnings) != 0 {
		t.Errorf("owner-only file still warned: %q", cfg.Warnings)
	}
}

func TestLoad_NoPermissionWarningWithoutKeyInFile(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("no Unix permission bits on Windows")
	}
	dir := isolate(t)
	writeConfig(t, dir, `theme = "light"`)
	if err := os.Chmod(filepath.Join(dir, "tui.toml"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("OMNISYNC_API_KEY", "from-env")
	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.Warnings) != 0 {
		t.Errorf("warned although the file holds no key: %q", cfg.Warnings)
	}
}

func TestLoad_WarnsAboutKeyOverPlainHTTP(t *testing.T) {
	isolate(t)
	t.Setenv("OMNISYNC_API_KEY", "secret")
	t.Setenv("OMNISYNC_URL", "http://nas.example:8000")
	cfg, err := config.Load(nil)
	if err != nil {
		t.Fatal(err)
	}
	if !hasWarning(cfg, "unencrypted") {
		t.Errorf("no warning for an API key sent over plain http to another host: %q", cfg.Warnings)
	}
}

func TestInsecureURL(t *testing.T) {
	cases := map[string]bool{
		"http://127.0.0.1:8000":   false,
		"http://localhost:8000":   false,
		"http://[::1]:8000":       false,
		"http://127.1.2.3":        false,
		"https://nas.example":     false,
		"http://nas.example:8000": true,
		"http://192.168.1.5:8000": true,
		"HTTP://10.0.0.1":         true,
	}
	for u, want := range cases {
		if got := config.InsecureURL(u); got != want {
			t.Errorf("InsecureURL(%q) = %v, want %v", u, got, want)
		}
	}
}

package config

import (
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/spf13/cobra"
	"github.com/spf13/viper"
)

// DefaultURL is the backend URL used when nothing else is configured.
const DefaultURL = "http://127.0.0.1:8000"

// Dir returns the TUI's config directory: os.UserConfigDir()/osync, e.g.
// ~/.config/osync on Linux. The config file is tui.toml in it.
func Dir() (string, error) {
	base, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("cannot determine the user config directory: %w", err)
	}
	return filepath.Join(base, "osync"), nil
}

// Config holds all TUI configuration values.
type Config struct {
	URL       string `mapstructure:"url"`
	APIKey    string `mapstructure:"api_key"`
	Theme     string `mapstructure:"theme"`
	ASCIIMode bool   `mapstructure:"ascii_mode"`
	LogFile   string `mapstructure:"log_file"`
	// NoMouse leaves the mouse to the terminal (no clickable tabs).
	NoMouse bool `mapstructure:"no_mouse"`

	// Warnings about how the API key is kept or sent, or a config file that
	// could not be read, for the user to see once at startup (not read from
	// the config file).
	Warnings []string `mapstructure:"-"`
}

// configFileMode is the mode of tui.toml: it may hold the API key.
const configFileMode os.FileMode = 0o600

// Load reads configuration with three-layer precedence:
// CLI flags (highest) -> environment variables (OMNISYNC_URL,
// OMNISYNC_API_KEY, OMNISYNC_THEME, OMNISYNC_ASCII_MODE, OMNISYNC_LOG_FILE,
// OMNISYNC_NO_MOUSE)
// -> <os.UserConfigDir()>/osync/tui.toml (lowest). The working directory is
// never searched, so a tui.toml lying around in some project cannot change
// where the TUI connects or which API key it sends.
//
// A layer that cannot be used does not stop the others: a tui.toml that is
// not valid TOML is skipped, and a setting with a value of the wrong type
// keeps its default; both come back as Warnings. The error is for the
// caller's sake only; Load currently always returns a usable Config.
func Load(cmd *cobra.Command) (*Config, error) {
	v := viper.New()

	v.SetDefault("url", DefaultURL)
	v.SetDefault("api_key", "")
	v.SetDefault("theme", "dark")
	v.SetDefault("ascii_mode", false)
	v.SetDefault("log_file", "")
	v.SetDefault("no_mouse", false)

	configPath := ""
	var warnings []string
	if dir, err := Dir(); err == nil {
		configPath = filepath.Join(dir, "tui.toml")
		v.SetConfigFile(configPath)
		v.SetConfigType("toml")
		if err := v.ReadInConfig(); err != nil && !errors.Is(err, os.ErrNotExist) {
			var notFound viper.ConfigFileNotFoundError
			if !errors.As(err, &notFound) {
				// The file is the lowest layer: a broken one must not
				// discard the flags and environment variables above it.
				warnings = append(warnings, fmt.Sprintf("%s is not used: %v", configPath, err))
			}
		}
	}
	keyInFile := v.InConfig("api_key") && v.GetString("api_key") != ""

	v.SetEnvPrefix("OMNISYNC")
	v.AutomaticEnv()

	if cmd != nil {
		if f := cmd.Flags().Lookup("url"); f != nil && f.Changed {
			v.Set("url", f.Value.String())
		}
		if f := cmd.Flags().Lookup("api-key"); f != nil && f.Changed {
			v.Set("api_key", f.Value.String())
		}
		if f := cmd.Flags().Lookup("ascii"); f != nil && f.Changed {
			v.Set("ascii_mode", f.Value.String() == "true")
		}
		if f := cmd.Flags().Lookup("no-mouse"); f != nil && f.Changed {
			v.Set("no_mouse", f.Value.String() == "true")
		}
		if f := cmd.Flags().Lookup("theme"); f != nil && f.Changed {
			v.Set("theme", f.Value.String())
		}
	}

	// Each setting is decoded on its own: a value of the wrong type (say
	// no_mouse = "yes" in tui.toml, or OMNISYNC_ASCII_MODE=on) is dropped
	// with a warning and its default used, so it cannot take the other
	// settings, the flags or the environment down with it.
	var cfg Config
	decodeKey(v, "url", &cfg.URL, DefaultURL, &warnings)
	decodeKey(v, "api_key", &cfg.APIKey, "", &warnings)
	decodeKey(v, "theme", &cfg.Theme, "dark", &warnings)
	decodeKey(v, "ascii_mode", &cfg.ASCIIMode, false, &warnings)
	decodeKey(v, "log_file", &cfg.LogFile, "", &warnings)
	decodeKey(v, "no_mouse", &cfg.NoMouse, false, &warnings)
	cfg.Warnings = warnings
	if cfg.URL == "" {
		cfg.URL = DefaultURL
	}
	if keyInFile && configPath != "" {
		if w := permissionWarning(configPath); w != "" {
			cfg.Warnings = append(cfg.Warnings, w)
		}
	}
	if cfg.APIKey != "" && InsecureURL(cfg.URL) {
		cfg.Warnings = append(cfg.Warnings, fmt.Sprintf(
			"the API key is sent unencrypted to %s; use https:// or a loopback address (127.0.0.1, localhost)", cfg.URL))
	}
	return &cfg, nil
}

// decodeKey sets *dst to the value of key, or to def with a warning that
// names the key when the value does not decode as a T. The warning gives
// the decoder's reason, which quotes the value, except for api_key: a
// mistyped key is still a secret and must not reach the terminal or a log.
func decodeKey[T any](v *viper.Viper, key string, dst *T, def T, warnings *[]string) {
	var val T
	if err := v.UnmarshalKey(key, &val); err != nil {
		reason := err.Error()
		if key == "api_key" {
			reason = fmt.Sprintf("it is not a %T", val)
		}
		*warnings = append(*warnings, fmt.Sprintf("the %s setting is not used, the default is: %s", key, reason))
		val = def
	}
	*dst = val
}

// permissionWarning describes a config file other users can read, or "".
// Windows has no such mode bits; its ACLs are left alone.
func permissionWarning(path string) string {
	if runtime.GOOS == "windows" {
		return ""
	}
	info, err := os.Stat(path)
	if err != nil || info.Mode().Perm()&0o077 == 0 {
		return ""
	}
	return fmt.Sprintf("%s holds the API key but other users can read it (mode %04o); run: chmod 600 %s",
		path, info.Mode().Perm(), path)
}

// InsecureURL reports whether requests to rawURL travel unencrypted over a
// network: plain http:// to a host that is not a loopback address.
func InsecureURL(rawURL string) bool {
	u, err := url.Parse(rawURL)
	if err != nil || !strings.EqualFold(u.Scheme, "http") {
		return false
	}
	host := strings.ToLower(u.Hostname())
	if host == "localhost" || strings.HasSuffix(host, ".localhost") {
		return false
	}
	ip := net.ParseIP(host)
	return ip == nil || !ip.IsLoopback()
}

// SaveTheme persists the theme preference to the config file, keeping the
// other settings in it. The file is replaced atomically and left readable by
// the owner only (it may hold the API key), also when it was not before.
func SaveTheme(theme string) error {
	dir, err := Dir()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("cannot create config directory: %w", err)
	}

	path := filepath.Join(dir, "tui.toml")
	v := viper.New()
	v.SetConfigFile(path)
	v.SetConfigType("toml")
	_ = v.ReadInConfig()
	v.Set("theme", theme)
	return writeConfigFile(v, path)
}

// writeConfigFile writes v's settings to a new owner-only file next to path
// and renames it over path, so neither a crash nor another user ever sees a
// partly written or world-readable config.
func writeConfigFile(v *viper.Viper, path string) error {
	// viper picks the format from the extension, so the temp name ends in .toml.
	tmp, err := os.CreateTemp(filepath.Dir(path), ".tui-*.toml")
	if err != nil {
		return fmt.Errorf("cannot write config: %w", err)
	}
	tmpPath := tmp.Name()
	_ = tmp.Close()
	defer func() { _ = os.Remove(tmpPath) }() // no-op after the rename
	if err := os.Chmod(tmpPath, configFileMode); err != nil {
		return fmt.Errorf("cannot write config: %w", err)
	}
	v.SetConfigPermissions(configFileMode)
	if err := v.WriteConfigAs(tmpPath); err != nil {
		return err
	}
	if err := os.Rename(tmpPath, path); err != nil {
		return fmt.Errorf("cannot write config: %w", err)
	}
	return nil
}

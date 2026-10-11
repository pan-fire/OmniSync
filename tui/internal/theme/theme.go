package theme

import (
	"image/color"

	"charm.land/lipgloss/v2"
)

// Theme holds all Lip Gloss styles used throughout the TUI.
type Theme struct {
	Name string

	// Core colors
	Primary   color.Color
	Secondary color.Color
	Error     color.Color
	Success   color.Color
	Warning   color.Color
	Info      color.Color
	Muted     color.Color

	// Background / foreground
	Background color.Color
	Foreground color.Color

	// Border style
	Border lipgloss.Border
}

// StatusColor returns the colour of a sync/job status: idle gray, running
// states blue, completed green, error/failed red, paused/skipped yellow.
func (t *Theme) StatusColor(state string) color.Color {
	switch state {
	case "idle":
		return t.Muted
	case "pushing", "pulling", "syncing", "running":
		return t.Info
	case "completed":
		return t.Success
	case "error", "failed":
		return t.Error
	case "paused", "skipped", "resync required", "completed with warnings":
		return t.Warning
	}
	return t.Muted
}

// StatusBadge returns a styled string for a sync/job status.
func (t *Theme) StatusBadge(state string) lipgloss.Style {
	return lipgloss.NewStyle().Foreground(t.StatusColor(state)).Bold(true)
}

// GlyphSet holds the symbols the UI draws. ASCII mode swaps them for plain
// characters that every terminal and font can show.
type GlyphSet struct {
	Check      string // success / selected
	Cross      string // failure
	Connected  string
	Disconnect string
	Warning    string
	Cursor     string // marks the highlighted table row
	DropLeft   string
	DropRight  string
	Ellipsis   string
	Arrow      string
	EmDash     string
	LeftRight  string // "left/right" key hint
}

var unicodeGlyphs = GlyphSet{
	Check: "✓", Cross: "✗", Connected: "●", Disconnect: "○", Warning: "⚠",
	Cursor: ">", DropLeft: "◂", DropRight: "▸", Ellipsis: "…", Arrow: "→",
	EmDash: "—", LeftRight: "←/→",
}

var asciiGlyphs = GlyphSet{
	Check: "x", Cross: "-", Connected: "*", Disconnect: "o", Warning: "!",
	Cursor: ">", DropLeft: "<", DropRight: ">", Ellipsis: "...", Arrow: "->",
	EmDash: "-", LeftRight: "Left/Right",
}

// Current is the active theme.
var Current *Theme

var ascii bool

func init() {
	Current = DarkTheme()
}

// Glyphs returns the symbols for the current mode.
func Glyphs() GlyphSet {
	if ascii {
		return asciiGlyphs
	}
	return unicodeGlyphs
}

// DarkTheme returns the dark color scheme.
func DarkTheme() *Theme {
	return &Theme{
		Name:       "dark",
		Primary:    lipgloss.Color("#7C3AED"),
		Secondary:  lipgloss.Color("#6366F1"),
		Error:      lipgloss.Color("#EF4444"),
		Success:    lipgloss.Color("#22C55E"),
		Warning:    lipgloss.Color("#F59E0B"),
		Info:       lipgloss.Color("#06B6D4"),
		Muted:      lipgloss.Color("#6B7280"),
		Background: lipgloss.Color("#1E1E2E"),
		Foreground: lipgloss.Color("#CDD6F4"),
		Border:     lipgloss.RoundedBorder(),
	}
}

// LightTheme returns the light color scheme.
func LightTheme() *Theme {
	return &Theme{
		Name:       "light",
		Primary:    lipgloss.Color("#6D28D9"),
		Secondary:  lipgloss.Color("#4F46E5"),
		Error:      lipgloss.Color("#DC2626"),
		Success:    lipgloss.Color("#16A34A"),
		Warning:    lipgloss.Color("#D97706"),
		Info:       lipgloss.Color("#0891B2"),
		Muted:      lipgloss.Color("#9CA3AF"),
		Background: lipgloss.Color("#FFFFFF"),
		Foreground: lipgloss.Color("#1F2937"),
		Border:     lipgloss.RoundedBorder(),
	}
}

// Set selects a theme by name ("dark" or "light"); unknown names select
// dark. The ASCII setting is kept.
func Set(name string) {
	if name == "light" {
		Current = LightTheme()
	} else {
		Current = DarkTheme()
	}
	applyBorder()
}

// Toggle switches between dark and light themes and returns the new name.
func Toggle() string {
	if Current.Name == "dark" {
		Set("light")
	} else {
		Set("dark")
	}
	return Current.Name
}

// SetASCII switches ASCII mode: plain borders and plain symbols.
func SetASCII(on bool) {
	ascii = on
	applyBorder()
}

func applyBorder() {
	if ascii {
		Current.Border = lipgloss.ASCIIBorder()
	} else {
		Current.Border = lipgloss.RoundedBorder()
	}
}

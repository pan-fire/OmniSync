package ui

import (
	"errors"
	"fmt"
	"image/color"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// Key hints shared by several views' bottom bars.
const (
	confirmHints = "y:yes  n/Esc:no"
	formHints    = "Enter:submit  Esc:cancel  Tab/Shift+Tab:field  Left/Right:choice"
)

// flash returns a command that shows a status bar message.
func flash(text string, isError bool) tea.Cmd {
	return func() tea.Msg { return FlashMsg{Text: text, IsError: isError} }
}

// errorFlash shows err in the status bar.
func errorFlash(prefix string, err error) tea.Cmd {
	return flash(fmt.Sprintf("%s: %s", prefix, err.Error()), true)
}

// formatBytes renders a byte count with a binary unit.
func formatBytes(b int64) string {
	const unit = 1024
	if b < unit {
		return fmt.Sprintf("%d B", b)
	}
	div, exp := int64(unit), 0
	for n := b / unit; n >= unit; n /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(b)/float64(div), "KMGTPE"[exp])
}

// formatTime renders a backend timestamp (ISO 8601) in local time.
func formatTime(s string) string {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999", "2006-01-02T15:04:05"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.Local().Format("2006-01-02 15:04:05")
		}
	}
	return safeLine(s)
}

// formatTimePtr renders an optional timestamp, or fallback when nil.
func formatTimePtr(s *string, fallback string) string {
	if s == nil || *s == "" {
		return fallback
	}
	return formatTime(*s)
}

func checkMark(ok bool) string {
	g := theme.Glyphs()
	if ok {
		return lipgloss.NewStyle().Foreground(theme.Current.Success).Render(g.Check)
	}
	return lipgloss.NewStyle().Foreground(theme.Current.Error).Render(g.Cross)
}

func formatUptime(seconds float64) string {
	if seconds < 60 {
		return fmt.Sprintf("%.0fs", seconds)
	}
	if seconds < 3600 {
		return fmt.Sprintf("%.0fm", seconds/60)
	}
	return fmt.Sprintf("%.1fh", seconds/3600)
}

// stateLabel renders a profile state for a table cell, with the reason when
// the profile is in the error state. A two-way profile that needs a resync
// says so first: nothing syncs automatically until the user confirms one.
func stateLabel(state api.SyncState, lastError *string, resyncRequired bool) string {
	if resyncRequired && !state.Busy() {
		return "resync required"
	}
	if state == api.SyncStateError && lastError != nil && *lastError != "" {
		return "error: " + safeLine(*lastError)
	}
	return safeLine(string(state))
}

// stateColor is the colour of a profile state cell; the text from
// stateLabel carries the same meaning without colour.
func stateColor(state api.SyncState, resyncRequired bool) color.Color {
	if resyncRequired && !state.Busy() {
		return theme.Current.StatusColor("resync required")
	}
	return theme.Current.StatusColor(string(state))
}

// cellColors returns a Row.Colors slice of n cells with c at index i.
func cellColors(n, i int, c color.Color) []color.Color {
	out := make([]color.Color, n)
	out[i] = c
	return out
}

// syncModeLabel names a profile's sync mode for a list or a field.
func syncModeLabel(mode api.SyncMode) string {
	switch mode {
	case api.SyncModeTwoWay:
		return "two-way"
	case api.SyncModeMirror:
		return "mirror"
	case "":
		return "-"
	}
	return safeLine(string(mode))
}

// syncModeSummary explains a sync mode in one line.
func syncModeSummary(mode api.SyncMode) string {
	switch mode {
	case api.SyncModeTwoWay:
		return "two-way: changes on either side are carried to the other"
	case api.SyncModeMirror:
		return "mirror: one-way push/pull only; the side that syncs last wins"
	}
	return syncModeLabel(mode)
}

// jobDirectionLabel names the kind of a sync job.
func jobDirectionLabel(dir api.JobDirection) string {
	switch dir {
	case api.JobDirectionTwoWay:
		return "two-way"
	case api.JobDirectionResync:
		return "resync"
	case "":
		return "-"
	}
	return safeLine(string(dir))
}

// fileSideLabel names the folder a job's file change happened in.
func fileSideLabel(side *api.FileSide) string {
	if side == nil || *side == "" {
		return "-"
	}
	return safeLine(string(*side))
}

// deleteLimitShort renders a profile's delete limit for a list or a field:
// "25 files per sync" or "none (no delete limit)".
func deleteLimitShort(maxDelete *int) string {
	if maxDelete == nil {
		return "none (no delete limit)"
	}
	return fmt.Sprintf("%d files per sync", *maxDelete)
}

// deleteLimitSentence explains a profile's delete limit in a confirmation.
func deleteLimitSentence(maxDelete *int) string {
	if maxDelete == nil {
		return "This profile has no delete limit: the sync deletes every file it needs to."
	}
	return fmt.Sprintf("Delete limit: %d files. A sync that would delete more stops at the limit\nand deletes no more.", *maxDelete)
}

// apiDetail returns the backend's detail message of an API error, or the
// error text otherwise, as one line to show.
func apiDetail(err error) string {
	var apiErr *api.ApiError
	if errors.As(err, &apiErr) && apiErr.Detail != "" {
		return safeLine(apiErr.Detail)
	}
	return safeLine(err.Error())
}

// errorLine renders a view's error banner.
func errorLine(err error) string {
	style := lipgloss.NewStyle().Foreground(theme.Current.Error)
	return style.Render("  Error: "+safeText(err.Error())) + "\n\n"
}

// safeLine and safeText make server or file text safe to draw: see
// components.SafeLine and components.SafeText. Every string from an API
// answer that a view draws outside a table cell, a flash message or a
// confirmation goes through one of them; servertext_lint_test.go checks.
func safeLine(s string) string { return components.SafeLine(s) }

func safeText(s string) string { return components.SafeText(s) }

func mutedText(s string) string {
	return lipgloss.NewStyle().Foreground(theme.Current.Muted).Render(s)
}

func headerText(s string) string {
	return lipgloss.NewStyle().Bold(true).Foreground(theme.Current.Primary).Render(s)
}

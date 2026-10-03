package ui

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// --- Live progress ---

// formatDuration renders seconds as "1h05m", "3m20s" or "12s".
func formatDuration(seconds int) string {
	if seconds < 0 {
		seconds = 0
	}
	h, m, s := seconds/3600, (seconds%3600)/60, seconds%60
	switch {
	case h > 0:
		return fmt.Sprintf("%dh%02dm", h, m)
	case m > 0:
		return fmt.Sprintf("%dm%02ds", m, s)
	}
	return fmt.Sprintf("%ds", s)
}

// progressBar draws a bar of width cells, or a placeholder without totals.
func progressBar(percent, width int) string {
	if percent < 0 {
		return "[" + strings.Repeat("·", width) + "]"
	}
	filled := percent * width / 100
	return "[" + strings.Repeat("#", filled) + strings.Repeat("-", width-filled) + "]"
}

// progressLine summarises a running sync: bar, percentage, sizes, files,
// speed and time left. Empty without progress.
func progressLine(p *api.SyncProgress) string {
	if p == nil {
		return ""
	}
	pct := p.Percent()
	parts := []string{progressBar(pct, 20)}
	if pct >= 0 {
		parts = append(parts, fmt.Sprintf("%d%%", pct))
	}
	if p.TotalBytes > 0 {
		parts = append(parts, formatBytes(p.Bytes)+" of "+formatBytes(p.TotalBytes))
	}
	if p.FilesTotal > 0 {
		parts = append(parts, fmt.Sprintf("%d/%d files", p.FilesDone, p.FilesTotal))
	}
	if p.Speed > 0 {
		parts = append(parts, formatBytes(int64(p.Speed))+"/s")
	}
	if p.EtaSeconds != nil && *p.EtaSeconds > 0 {
		parts = append(parts, formatDuration(*p.EtaSeconds)+" left")
	}
	if pct < 0 && len(parts) == 1 {
		parts = append(parts, "starting...")
	}
	return strings.Join(parts, "  ")
}

// progressFiles lists the files in flight, one per line, indented.
func progressFiles(p *api.SyncProgress, indent string) string {
	if p == nil || len(p.CurrentFiles) == 0 {
		return ""
	}
	var b strings.Builder
	for _, f := range p.CurrentFiles {
		line := indent + "  " + f.Name
		if f.Percentage != nil {
			line += fmt.Sprintf("  %d%%", *f.Percentage)
		}
		if f.Size != nil {
			line += " of " + formatBytes(*f.Size)
		}
		b.WriteString(mutedText(line))
		b.WriteString("\n")
	}
	return b.String()
}

// --- Sync window ---

var dayNames = []string{"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}

var windowTimesRe = regexp.MustCompile(`^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$`)

func dayIndex(name string) int {
	name = strings.ToLower(name)
	for i, d := range dayNames {
		if strings.HasPrefix(name, strings.ToLower(d)) && len(name) >= 3 {
			return i
		}
	}
	return -1
}

// parseSyncWindow reads "22:00-06:00" (every day) or "Mon-Fri 22:00-06:00"
// / "Sat,Sun 00:00-08:00" (days, then times). Empty means no window (nil).
func parseSyncWindow(text string) (*api.SyncWindow, error) {
	text = strings.TrimSpace(text)
	if text == "" {
		return nil, nil
	}
	fields := strings.Fields(text)
	times := fields[len(fields)-1]
	if !windowTimesRe.MatchString(times) {
		return nil, fmt.Errorf("sync window: times are HH:MM-HH:MM, e.g. 22:00-06:00")
	}
	start, end := times[:5], times[6:]
	if start == end {
		return nil, fmt.Errorf("sync window: start and end must differ")
	}
	days := []int{0, 1, 2, 3, 4, 5, 6}
	if len(fields) > 1 {
		seen := map[int]bool{}
		for _, part := range strings.Split(strings.Join(fields[:len(fields)-1], ","), ",") {
			part = strings.TrimSpace(part)
			if part == "" {
				continue
			}
			from, to, isRange := strings.Cut(part, "-")
			a, b := dayIndex(from), dayIndex(from)
			if isRange {
				b = dayIndex(to)
			}
			if a < 0 || b < 0 {
				return nil, fmt.Errorf("sync window: unknown day %q (use Mon..Sun, e.g. Mon-Fri or Sat,Sun)", part)
			}
			for d := a; ; d = (d + 1) % 7 {
				seen[d] = true
				if d == b {
					break
				}
			}
		}
		days = days[:0]
		for d := range seen {
			days = append(days, d)
		}
		sort.Ints(days)
	}
	return &api.SyncWindow{Days: days, Start: start, End: end}, nil
}

// formatSyncWindow writes a window the way parseSyncWindow reads it.
func formatSyncWindow(w *api.SyncWindow) string {
	if w == nil {
		return ""
	}
	times := w.Start + "-" + w.End
	if len(w.Days) == 7 || len(w.Days) == 0 {
		return times
	}
	names := make([]string, 0, len(w.Days))
	for _, d := range w.Days {
		if d >= 0 && d < 7 {
			names = append(names, dayNames[d])
		}
	}
	return strings.Join(names, ",") + " " + times
}

// windowStatus says what the profile's sync window means right now, or "".
func windowStatus(outside, waiting bool, next *string) string {
	if !outside {
		return ""
	}
	text := "Outside the sync window: automatic syncs wait"
	if waiting {
		text = "Outside the sync window: a sync waits for it to open"
	}
	if next != nil {
		text += " (opens " + formatTime(*next) + ")"
	}
	return text
}

// ptrTo returns a pointer to v.
func ptrTo[T any](v T) *T { return &v }

// --- Pause all / resume all ---

func pauseAllCmd(client *api.Client, view ViewID) tea.Cmd {
	return func() tea.Msg {
		resp, err := client.PauseAllProfiles(context.Background())
		return ActionResultMsg{ViewID: view, Action: "pause_all", Err: err, Data: resp}
	}
}

func resumeAllCmd(client *api.Client, view ViewID) tea.Cmd {
	return func() tea.Msg {
		resp, err := client.ResumeAllProfiles(context.Background())
		return ActionResultMsg{ViewID: view, Action: "resume_all", Err: err, Data: resp}
	}
}

// pauseAllFlash reports a pause-all or resume-all answer; resume-all names
// the profiles that stay paused for another reason.
func pauseAllFlash(action string, resp *api.PauseAllResponse, err error) tea.Cmd {
	verb := "Paused"
	if action == "resume_all" {
		verb = "Resumed"
	}
	if err != nil {
		return errorFlash(verb+" all failed", err)
	}
	if resp == nil {
		return flash(verb+" all", false)
	}
	text := fmt.Sprintf("%s automatic syncing of %d profile(s)", verb, len(resp.Changed))
	if len(resp.StillPaused) > 0 {
		slugs := make([]string, 0, len(resp.StillPaused))
		for slug := range resp.StillPaused {
			slugs = append(slugs, slug)
		}
		sort.Strings(slugs)
		text += "; still paused for review: " + strings.Join(slugs, ", ") + " (open the profile, then i)"
		return flash(text, true)
	}
	return flash(text, false)
}

package ui

import (
	"fmt"
	"strings"

	"charm.land/lipgloss/v2"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// statusWithWarnings is how a completed job with warnings shows its status.
const statusWithWarnings = "completed with warnings"

// warningTexts says what each kind of warning means, for count names.
var warningTexts = map[api.SyncWarningCode]string{
	api.SyncWarningNameCollision: "local name(s) exist in two spellings that differ only in Unicode " +
		"normalisation; rclone syncs one spelling of each (rename one so both sync)",
	api.SyncWarningNameNotUTF8: "local name(s) are not valid UTF-8; whole-folder syncs carry them, " +
		"per-file actions cannot",
	api.SyncWarningSymlinkShadow: "local symbolic link(s) have the name of a remote file or folder; " +
		"a push moves that item to the remote trash, a pull or two-way sync leaves both alone",
	api.SyncWarningSymlinkKept: "remote file(s) or folder(s) not synced: a local symbolic link has " +
		"their name; both were left as they are",
	api.SyncWarningSymlinkTrashed: "remote file(s) or folder(s) moved to the remote trash: a local " +
		"symbolic link has their name (links are not synced)",
}

// jobStatusLabel is a job's status as the views show it.
func jobStatusLabel(j api.SyncJobResponse) string {
	if j.HasWarnings() {
		return statusWithWarnings
	}
	return string(j.Status)
}

// jobListStatus is a job's status in a job list, where the column is
// narrow: "warnings" for a completed job with warnings.
func jobListStatus(j api.SyncJobResponse) string {
	if j.HasWarnings() {
		return "warnings"
	}
	return string(j.Status)
}

// renderWarnings draws warnings (preview, diff, job) as indented lines:
// one per kind, then its paths and how many more there are. "" for none.
func renderWarnings(warnings []api.SyncWarning) string {
	if len(warnings) == 0 {
		return ""
	}
	style := lipgloss.NewStyle().Foreground(theme.Current.Warning)
	var b strings.Builder
	for _, w := range warnings {
		text, ok := warningTexts[w.Code]
		if !ok {
			text = "file(s) need attention (" + safeLine(string(w.Code)) + ")"
		}
		b.WriteString(style.Render(fmt.Sprintf("  %s %d %s", theme.Glyphs().Warning, w.Count, text)))
		b.WriteString("\n")
		for _, p := range w.Paths {
			b.WriteString(mutedText("      " + safeLine(p)))
			b.WriteString("\n")
		}
		if more := w.Count - len(w.Paths); more > 0 {
			b.WriteString(mutedText(fmt.Sprintf("      ... and %d more", more)))
			b.WriteString("\n")
		}
	}
	return b.String()
}

// previewWarnings is the warnings part of a sync confirmation ("" for none).
func previewWarnings(preview *api.SyncPreviewResponse) string {
	if len(preview.Warnings) == 0 {
		return ""
	}
	return "\nNot everything can be synced as it is:\n" + renderWarnings(preview.Warnings)
}

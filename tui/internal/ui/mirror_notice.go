package ui

import (
	"context"
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// The mirror-mode notice: every mirror profile explains what mirror means,
// its risk and the switch to two-way, in Profile Detail and on the
// Dashboard, until the user hides it for that profile. The dismissal is
// stored on the profile (mirror_notice_dismissed), so the web UI and the TUI
// agree.

// mirrorNoticeLines explains mirror mode for one profile (Profile Detail).
func mirrorNoticeLines() []string {
	return []string{
		"Mirror mode: push makes the remote match this folder, pull makes this folder",
		"match the remote. Each is a one-way copy, so whichever runs next overwrites or",
		"deletes changes that exist only on the other side, such as edits made while",
		"syncing was paused or offline (replaced and deleted files go to .omnisync-trash).",
		"Two-way sync keeps changes from both sides. w: switch to two-way (asks first);",
		"its first run is a resync that makes both folders the union and deletes nothing.",
	}
}

// renderMirrorNotice renders the Profile Detail notice with its keys.
func renderMirrorNotice() string {
	info := lipgloss.NewStyle().Foreground(theme.Current.Info)
	var b strings.Builder
	for _, line := range mirrorNoticeLines() {
		b.WriteString(info.Render("  " + line))
		b.WriteString("\n")
	}
	b.WriteString(mutedText("  h: hide this note for this profile"))
	b.WriteString("\n")
	return b.String()
}

// renderMirrorProfilesNotice renders the Dashboard notice for the mirror
// profiles whose notice is not hidden; "" when there are none.
func renderMirrorProfilesNotice(profiles []api.ProfileStatusResponse) string {
	var names []string
	for _, p := range profiles {
		if p.ShowMirrorNotice() {
			names = append(names, p.Name)
		}
	}
	if len(names) == 0 {
		return ""
	}
	info := lipgloss.NewStyle().Foreground(theme.Current.Info)
	var b strings.Builder
	b.WriteString(headerText(fmt.Sprintf("  Mirror mode (%d)", len(names))))
	b.WriteString("\n")
	for _, line := range []string{
		fmt.Sprintf("%s sync(s) as a one-way mirror: push and pull each make one side match", strings.Join(names, ", ")),
		"the other, so changes made on the other side while syncing was paused or offline",
		"can be overwritten or deleted (they go to .omnisync-trash). Two-way sync keeps",
		"changes from both sides; its first run is a resync that deletes nothing.",
	} {
		b.WriteString(info.Render("  " + line))
		b.WriteString("\n")
	}
	b.WriteString(mutedText("  w: switch all mirror profiles to two-way (lists them and asks first)."))
	b.WriteString("\n")
	b.WriteString(mutedText("  Open a profile to switch it alone, or to hide its note (h)."))
	b.WriteString("\n")
	return b.String()
}

// mirrorProfiles returns the profiles that sync in mirror mode.
func mirrorProfiles(profiles []api.ProfileStatusResponse) []api.ProfileStatusResponse {
	var out []api.ProfileStatusResponse
	for _, p := range profiles {
		if !p.TwoWay() {
			out = append(out, p)
		}
	}
	return out
}

// switchAllPrompt lists the mirror profiles "Switch all to two-way" acts on.
func switchAllPrompt(targets []api.ProfileStatusResponse) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Switch all %d mirror profile(s) to two-way sync?\n\n", len(targets))
	for _, p := range targets {
		line := fmt.Sprintf("  %s (%s): %s %s %s", p.Name, p.Slug, p.LocalDir, theme.Glyphs().LeftRight, p.RemoteDir)
		if !p.Enabled {
			line += " (disabled)"
		}
		b.WriteString(line + "\n")
	}
	b.WriteString("\nTwo-way sync carries changes on either side to the other; a file changed on\n")
	b.WriteString("both sides keeps both versions. The first two-way sync of each profile is a\n")
	b.WriteString("resync: both folders become the union of both sides and nothing is deleted\n")
	b.WriteString("(where a file differs, the newer version wins and the older goes to\n")
	b.WriteString(".omnisync-trash).\n\n")
	b.WriteString("You can switch a profile back to mirror in the Profiles view (e: edit).")
	return b.String()
}

// switchAllResult is the outcome of "Switch all to two-way".
type switchAllResult struct {
	switched []string
	failed   []string // "slug: reason", in the order tried
}

// switchAllTwoWayCmd switches each profile to two-way, one PUT
// /profiles/{slug} {"sync_mode": "two_way"} per profile.
func switchAllTwoWayCmd(client *api.Client, slugs []string) tea.Cmd {
	return func() tea.Msg {
		var res switchAllResult
		mode := api.SyncModeTwoWay
		for _, slug := range slugs {
			if _, err := client.UpdateProfile(context.Background(), slug, api.ProfileUpdateRequest{SyncMode: &mode}); err != nil {
				res.failed = append(res.failed, slug+": "+err.Error())
				continue
			}
			res.switched = append(res.switched, slug)
		}
		return ActionResultMsg{ViewID: ViewDashboard, Action: "switch_all_two_way", Data: res}
	}
}

// switchAllFlash reports the outcome of "Switch all to two-way".
func switchAllFlash(res switchAllResult) tea.Cmd {
	if len(res.failed) == 0 {
		return flash(fmt.Sprintf("%d profile(s) now sync two-way; the next sync of each is a resync that deletes nothing", len(res.switched)), false)
	}
	return flash(fmt.Sprintf("Switched %d profile(s) to two-way; %d failed (%s)", len(res.switched), len(res.failed), strings.Join(res.failed, "; ")), true)
}

// setMirrorNoticeCmd hides (dismissed true) or shows the mirror notice of a
// profile.
func setMirrorNoticeCmd(client *api.Client, slug string, dismissed bool) tea.Cmd {
	return func() tea.Msg {
		_, err := client.UpdateProfile(context.Background(), slug, api.ProfileUpdateRequest{MirrorNoticeDismissed: &dismissed})
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "mirror_notice", Err: err, Data: dismissed}
	}
}

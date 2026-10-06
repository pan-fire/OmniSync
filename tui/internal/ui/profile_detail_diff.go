package ui

import (
	"context"
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

type diffFilter int

const (
	diffFilterAll diffFilter = iota
	diffFilterLocalOnly
	diffFilterRemoteOnly
	diffFilterModifiedLocal
	diffFilterModifiedRemote
	diffFilterModifiedBoth
	diffFilterCount
)

var diffFilterLabels = []string{"All", "Local Only", "Remote Only", "Mod Local", "Mod Remote", "Mod Both"}

func diffColumns() []components.Column {
	return []components.Column{
		{Title: "Path", Width: 36},
		{Title: "Category", Width: 15},
		{Title: "Local", Width: 10},
		{Title: "Remote", Width: 10},
		{Title: "Conflict", Width: 8},
		{Title: "Manual", Width: 6},
	}
}

func (m ProfileDetailModel) handleDiffKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "f":
		m.diffFilter = (m.diffFilter + 1) % diffFilterCount
		m.updateDiffTable()
		return m, nil
	case "p":
		return m.askSelective(api.FileActionPush)
	case "l":
		return m.askSelective(api.FileActionPull)
	case "s":
		return m, m.selectiveSync(m.selectedItems(api.FileActionSkip))
	case "m":
		return m, m.selectiveSync(m.selectedItems(api.FileActionManual))
	case "r":
		m.diffLoading = true
		return m, m.fetchDiff()
	default:
		m.diffTable.Update(msg)
	}
	return m, nil
}

// selectedItems returns the selected files, or the highlighted one.
func (m ProfileDetailModel) selectedItems(action api.FileAction) []api.SelectiveSyncItem {
	paths := m.diffTable.SelectedKeys()
	if len(paths) == 0 {
		if row := m.diffTable.SelectedRow(); row != nil {
			paths = []string{row.Key}
		}
	}
	items := make([]api.SelectiveSyncItem, 0, len(paths))
	for _, p := range paths {
		items = append(items, api.SelectiveSyncItem{Path: p, Action: action})
	}
	return items
}

// askSelective asks before pushing or pulling individual files.
func (m ProfileDetailModel) askSelective(action api.FileAction) (tea.Model, tea.Cmd) {
	items := m.selectedItems(action)
	if len(items) == 0 {
		return m, nil
	}
	name := m.slug
	if m.profile != nil {
		name = fmt.Sprintf("%s (%s)", safeLine(m.profile.Name), m.slug)
	}
	where := "the remote copies are replaced by the local ones"
	if action == api.FileActionPull {
		where = "the local copies are replaced by the remote ones"
	}
	m.pendingSel = items
	m.confirm = components.NewConfirm(
		fmt.Sprintf("%s %d selected file(s) of profile %s?\n\nFor these files %s.",
			strings.ToUpper(string(action[:1]))+string(action[1:]), len(items), name, where),
		"selective")
	return m, nil
}

func (m ProfileDetailModel) renderDiffTab() string {
	var b strings.Builder

	fmt.Fprintf(&b, "  Filter: %s\n", mutedText(diffFilterLabels[m.diffFilter]))

	if m.diffErr != nil {
		b.WriteString(errorLine(m.diffErr))
	}
	if m.diffLoading {
		b.WriteString("  Comparing local and remote (this can take a while)...\n")
		if m.diffData == nil {
			return b.String()
		}
	}
	if m.diffData == nil {
		b.WriteString("  No diff data " + theme.Glyphs().EmDash + " press 'r' to load\n")
		return b.String()
	}

	s := m.diffData.Summary
	fmt.Fprintf(&b, "  Total: %d  Local only: %d  Remote only: %d  Mod local: %d  Mod remote: %d  Mod both: %d  Manual: %d\n",
		s.Total, s.LocalOnly, s.RemoteOnly, s.ModifiedLocal, s.ModifiedRemote, s.ModifiedBoth, s.Manual)

	if m.diffData.Error != nil && *m.diffData.Error != "" {
		b.WriteString(errorLine(fmt.Errorf("%s", *m.diffData.Error)))
	}
	b.WriteString("\n")
	b.WriteString(m.diffTable.View())

	b.WriteString("\n")
	if line := m.runningLine(jobKindSelective); line != "" {
		b.WriteString(mutedText(line) + "\n")
	}
	b.WriteString(mutedText("  Space:select  a:all  p:push  l:pull  s:skip  m:manual  f:filter  n/N:page  r:reload"))
	if selected := m.diffTable.SelectedCount(); selected > 0 {
		fmt.Fprintf(&b, "\n  %d selected %s p/l/s/m apply to the selection", selected, theme.Glyphs().EmDash)
	} else if row := m.diffTable.SelectedRow(); row != nil {
		fmt.Fprintf(&b, "\n  Current file: %s %s p/l/s/m apply to this file", mutedText(row.Key), theme.Glyphs().EmDash)
	}

	return b.String()
}

func (m *ProfileDetailModel) updateDiffTable() {
	if m.diffData == nil {
		return
	}
	g := theme.Glyphs()
	var rows []components.Row
	for _, f := range m.diffData.Files {
		if !m.matchesDiffFilter(f) {
			continue
		}
		conflict, manual := "", ""
		if f.IsConflict {
			conflict = g.Warning
		}
		if f.ManualFlag {
			manual = g.Check
		}
		rows = append(rows, components.Row{
			Key:    f.Path,
			Values: []string{f.Path, string(f.Category), sizeOrDash(f.LocalSize), sizeOrDash(f.RemoteSize), conflict, manual},
		})
	}
	m.diffTable.SetRows(rows)
}

func sizeOrDash(n *int64) string {
	if n == nil {
		return "-"
	}
	return formatBytes(*n)
}

func (m ProfileDetailModel) matchesDiffFilter(f api.FileDiff) bool {
	switch m.diffFilter {
	case diffFilterLocalOnly:
		return f.Category == api.ChangeCategoryLocalOnly
	case diffFilterRemoteOnly:
		return f.Category == api.ChangeCategoryRemoteOnly
	case diffFilterModifiedLocal:
		return f.Category == api.ChangeCategoryModifiedLocal
	case diffFilterModifiedRemote:
		return f.Category == api.ChangeCategoryModifiedRemote
	case diffFilterModifiedBoth:
		return f.Category == api.ChangeCategoryModifiedBoth
	}
	return true
}

// fetchDiff loads every differing file (limit 0); the table pages them.
func (m ProfileDetailModel) fetchDiff() tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		data, err := client.ProfileDiff(context.Background(), slug, 0, 0)
		return PollResultMsg{ViewID: ViewProfileDetail, Data: ProfileDetailData{Slug: slug, Value: data}, Err: err}
	}
}

// selectiveSync starts applying per-file actions; the answer (the run
// started, or a refusal) arrives as actionSelectiveStarted and the run is then
// followed.
func (m ProfileDetailModel) selectiveSync(items []api.SelectiveSyncItem) tea.Cmd {
	if len(items) == 0 {
		return nil
	}
	client, slug := m.client, m.slug
	return func() tea.Msg {
		data, err := client.ProfileSelectiveSync(context.Background(), slug, items)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: actionSelectiveStarted, Err: err,
			Data: selectiveStart{Slug: slug, Run: data}}
	}
}

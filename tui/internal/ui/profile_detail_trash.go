package ui

import (
	"context"
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

const tabTrash = "Trash"

// trashState is the Trash tab: one side's .omnisync-trash at a time.
type trashState struct {
	side      api.TrashSide
	data      *api.TrashListResponse
	table     components.Table
	err       error
	loading   bool
	loadedFor string // slug/side the data belongs to
	// The entries a delete or an overwrite prompt acts on, captured when it
	// opened.
	pendingDelete    []string
	pendingOverwrite []string
}

func newTrashState() trashState {
	return trashState{
		side: api.TrashSideLocal,
		table: components.NewTable([]components.Column{
			{Title: "Original place", Width: 40},
			{Title: "Moved to trash", Width: 19},
			{Title: "Size", Width: 10},
		}, 15),
	}
}

// trashListData is a trash listing for one profile and side.
type trashListData struct {
	side api.TrashSide
	resp *api.TrashListResponse
}

// trashActionData is a restore or delete answer.
type trashActionData struct {
	resp *api.TrashActionResponse
}

func (m ProfileDetailModel) trashKeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "v", Desc: "Switch between the local and the remote trash"},
		{Key: "Space", Desc: "Select file"},
		{Key: "a", Desc: "Select all / none"},
		{Key: "u", Desc: "Restore the selected files (or the highlighted one) to their original place"},
		{Key: "d", Desc: "Delete the selected files for good (asks first)"},
		{Key: "n/N", Desc: "Next/previous page"},
		{Key: "r", Desc: "Reload"},
	}
}

func (m ProfileDetailModel) fetchTrash() tea.Cmd {
	client, slug, side := m.client, m.slug, m.trash.side
	return func() tea.Msg {
		resp, err := client.ProfileTrash(context.Background(), slug, side)
		return PollResultMsg{ViewID: ViewProfileDetail, Err: err,
			Data: ProfileDetailData{Slug: slug, Value: trashListData{side: side, resp: resp}}}
	}
}

func (m ProfileDetailModel) handleTrashData(data trashListData, err error) ProfileDetailModel {
	if data.side != m.trash.side {
		return m // the user switched sides meanwhile
	}
	m.trash.loading = false
	m.trash.err = err
	if err != nil {
		return m
	}
	m.trash.data = data.resp
	m.trash.loadedFor = m.slug + "/" + string(data.side)
	m.updateTrashTable()
	return m
}

func (m *ProfileDetailModel) updateTrashTable() {
	var rows []components.Row
	if m.trash.data != nil {
		for _, e := range m.trash.data.Entries {
			size := "-"
			if e.Size != nil {
				size = formatBytes(*e.Size)
			}
			when := e.TrashedAt
			if when == nil {
				when = e.Modified
			}
			rows = append(rows, components.Row{Key: e.ID, Values: []string{e.Path, formatTimePtr(when, "-"), size}})
		}
	}
	m.trash.table.SetRows(rows)
}

// trashTargets is the selection, or the highlighted entry without one.
func (m ProfileDetailModel) trashTargets() []string {
	if keys := m.trash.table.SelectedKeys(); len(keys) > 0 {
		return keys
	}
	if row := m.trash.table.SelectedRow(); row != nil {
		return []string{row.Key}
	}
	return nil
}

func (m ProfileDetailModel) handleTrashKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "v":
		if m.trash.side == api.TrashSideLocal {
			m.trash.side = api.TrashSideRemote
		} else {
			m.trash.side = api.TrashSideLocal
		}
		m.trash.data = nil
		m.trash.table.ClearSelection()
		m.updateTrashTable()
		m.trash.loading = true
		return m, m.fetchTrash()
	case "r":
		m.trash.loading = true
		return m, m.fetchTrash()
	case "u":
		ids := m.trashTargets()
		if len(ids) == 0 {
			return m, flash("Nothing to restore", false)
		}
		if m.syncing {
			return m, flash("A sync of this profile is running; restore when it is done", true)
		}
		return m, m.trashAction("restore", ids, false)
	case "d":
		ids := m.trashTargets()
		if len(ids) == 0 {
			return m, flash("Nothing to delete", false)
		}
		m.trash.pendingDelete = ids
		m.confirm = components.NewConfirm(fmt.Sprintf(
			"Delete %d file(s) from the %s trash for good?\n\n  %s\n\nThey cannot be restored afterwards.",
			len(ids), m.trash.side, strings.Join(firstN(ids, 10), "\n  ")), "trash_delete")
		return m, nil
	}
	m.trash.table.Update(msg)
	return m, nil
}

func firstN(items []string, n int) []string {
	if len(items) <= n {
		return items
	}
	return append(append([]string{}, items[:n]...), fmt.Sprintf("... and %d more", len(items)-n))
}

func (m ProfileDetailModel) trashAction(action string, ids []string, overwrite bool) tea.Cmd {
	client, slug := m.client, m.slug
	req := api.TrashActionRequest{Side: m.trash.side, IDs: ids, Overwrite: overwrite}
	return func() tea.Msg {
		var resp *api.TrashActionResponse
		var err error
		if action == "restore" {
			resp, err = client.RestoreFromTrash(context.Background(), slug, req)
		} else {
			resp, err = client.DeleteFromTrash(context.Background(), slug, req)
		}
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "trash_" + action, Err: err, Data: trashActionData{resp: resp}}
	}
}

// handleTrashResult reports a restore or delete. Files that are newer at
// their original place open a prompt: replace them (the current versions
// go to the trash first) or leave them.
func (m ProfileDetailModel) handleTrashResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	verb := "Restore"
	if msg.Action == "trash_delete" {
		verb = "Delete"
	}
	if msg.Err != nil {
		return m, tea.Batch(m.fetchTrash(), errorFlash(verb+" failed", msg.Err))
	}
	data, _ := msg.Data.(trashActionData)
	if data.resp == nil {
		return m, m.fetchTrash()
	}
	m.trash.table.ClearSelection()
	var newer, other []string
	for _, f := range data.resp.Failed {
		if f.Code == "target_newer" {
			newer = append(newer, f.ID)
		} else {
			other = append(other, f.ID+": "+f.Message)
		}
	}
	done := fmt.Sprintf("%sd %d file(s)", strings.ToLower(verb), len(data.resp.Done))
	if verb == "Restore" {
		done = fmt.Sprintf("Restored %d file(s); the next sync carries them to the other side", len(data.resp.Done))
	}
	cmds := []tea.Cmd{m.fetchTrash()}
	if len(other) > 0 {
		cmds = append(cmds, flash(done+"; failed: "+strings.Join(other, "; "), true))
	} else {
		cmds = append(cmds, flash(done, false))
	}
	if len(newer) > 0 {
		m.trash.pendingOverwrite = newer
		m.confirm = components.NewConfirm(fmt.Sprintf(
			"%d file(s) are newer at their original place:\n\n  %s\n\nReplace them with the trashed versions? The current versions are moved\nto the trash first, so nothing is lost.",
			len(newer), strings.Join(firstN(newer, 10), "\n  ")), "trash_overwrite")
	}
	return m, tea.Batch(cmds...)
}

func (m ProfileDetailModel) handleTrashConfirm(msg components.ConfirmResultMsg) (tea.Model, tea.Cmd) {
	if msg.Tag == "trash_delete" {
		ids := m.trash.pendingDelete
		m.trash.pendingDelete = nil
		if !msg.Confirmed || len(ids) == 0 {
			return m, flash("Nothing was deleted", false)
		}
		return m, m.trashAction("delete", ids, false)
	}
	ids := m.trash.pendingOverwrite
	m.trash.pendingOverwrite = nil
	if !msg.Confirmed || len(ids) == 0 {
		return m, flash("The newer files were left as they are", false)
	}
	return m, m.trashAction("restore", ids, true)
}

func (m ProfileDetailModel) renderTrashTab() string {
	var b strings.Builder
	side := "local folder"
	if m.trash.side == api.TrashSideRemote {
		side = "remote folder"
	}
	b.WriteString(fmt.Sprintf("  Trash of the %s (.omnisync-trash): files syncs replaced or deleted\n", side))
	switch {
	case m.trash.err != nil:
		b.WriteString(errorLine(m.trash.err))
	case m.trash.data == nil:
		b.WriteString("  Loading...\n")
	default:
		d := m.trash.data
		line := fmt.Sprintf("  %d file(s), %s", d.TotalFiles, formatBytes(d.TotalBytes))
		if d.Truncated {
			line += fmt.Sprintf(" (the newest %d are listed)", len(d.Entries))
		}
		b.WriteString(line + "\n\n")
		if len(d.Entries) == 0 {
			b.WriteString(mutedText("  The trash is empty.") + "\n")
		} else {
			b.WriteString(m.trash.table.View())
		}
	}
	b.WriteString("\n")
	b.WriteString(mutedText("  v:local/remote  Space:select  a:all  u:restore  d:delete  r:reload  Esc:back"))
	return b.String()
}

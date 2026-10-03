package ui

import (
	"context"
	"fmt"
	"sort"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// Snapshot browser: one folder of a snapshot at a time (or a search), with
// files and folders to select and restore, to their original place or into
// another local folder.

const (
	formSnapshotSearch = "snapshot_search"
	formRestoreFilesTo = "restore_files_to"
	// snapshotFilesLimit entries of a folder are fetched (the backend's
	// maximum page); a search narrows larger folders.
	snapshotFilesLimit = 1000
)

// snapshotBrowser is the state of the snapshot file browser.
type snapshotBrowser struct {
	targetID   int
	snapshotID string
	path       string // folder shown, "" for the top
	search     string
	page       *api.SnapshotFilesResponse
	table      components.Table
	selected   map[string]bool // paths of files and folders to restore
	loading    bool
	err        error
}

// fileRestore is a restore of selected files, captured when it was asked for.
type fileRestore struct {
	targetID   int
	snapshotID string
	paths      []string
	targetDir  *string
}

// snapshotFilesPage is one answer of the snapshot browser, for the folder it was asked for.
type snapshotFilesPage struct {
	targetID   int
	snapshotID string
	path       string
	search     string
	resp       *api.SnapshotFilesResponse
}

func snapshotFileColumns() []components.Column {
	return []components.Column{
		{Title: "Sel", Width: 3},
		{Title: "Name", Width: 40},
		{Title: "Size", Width: 12},
		{Title: "Files", Width: 7},
		{Title: "Modified", Width: 19},
	}
}

func newSnapshotBrowser(targetID int, snapshotID string) *snapshotBrowser {
	return &snapshotBrowser{
		targetID: targetID, snapshotID: snapshotID, loading: true,
		table: components.NewTable(snapshotFileColumns(), 15), selected: map[string]bool{},
	}
}

// selectedPaths returns the selection in a stable order.
func (b *snapshotBrowser) selectedPaths() []string {
	paths := make([]string, 0, len(b.selected))
	for p := range b.selected {
		paths = append(paths, p)
	}
	sort.Strings(paths)
	return paths
}

func (b *snapshotBrowser) entry(path string) *api.SnapshotFileEntry {
	if b.page == nil {
		return nil
	}
	for i := range b.page.Entries {
		if b.page.Entries[i].Path == path {
			return &b.page.Entries[i]
		}
	}
	return nil
}

func (b *snapshotBrowser) updateTable() {
	var rows []components.Row
	if b.page != nil {
		for _, e := range b.page.Entries {
			mark := "[ ]"
			if b.selected[e.Path] {
				mark = "[x]"
			}
			name, files := e.Name, ""
			if b.search != "" {
				name = e.Path
			}
			if e.IsDir {
				name += "/"
				if e.FileCount != nil {
					files = fmt.Sprint(*e.FileCount)
				}
			}
			rows = append(rows, components.Row{
				Key:    e.Path,
				Values: []string{mark, name, sizeOrDash(e.Size), files, formatTimePtr(e.ModTime, "")},
			})
		}
	}
	b.table.SetRows(rows)
}

func (m ProfileDetailModel) fetchSnapshotFiles(b *snapshotBrowser) tea.Cmd {
	client, slug := m.client, m.slug
	targetID, snapshotID, path, search := b.targetID, b.snapshotID, b.path, b.search
	return func() tea.Msg {
		resp, err := client.ListSnapshotFiles(context.Background(), slug, targetID, snapshotID, path, search, 0, snapshotFilesLimit)
		page := snapshotFilesPage{targetID: targetID, snapshotID: snapshotID, path: path, search: search, resp: resp}
		return PollResultMsg{ViewID: ViewProfileDetail, Data: ProfileDetailData{Slug: slug, Value: page}, Err: err}
	}
}

// openSnapshotBrowser shows the top folder of the highlighted snapshot.
func (m ProfileDetailModel) openSnapshotBrowser(snapshotID string) (tea.Model, tea.Cmd) {
	m.files = newSnapshotBrowser(m.snapshotTarget, snapshotID)
	m.files.table.SetWidth(m.width)
	m.backupsView = backupsSubFiles
	return m, m.fetchSnapshotFiles(m.files)
}

func (m ProfileDetailModel) handleSnapshotFilesPage(page snapshotFilesPage, err error) ProfileDetailModel {
	b := m.files
	if b == nil || b.targetID != page.targetID || b.snapshotID != page.snapshotID ||
		b.path != page.path || b.search != page.search {
		return m // the user moved on
	}
	b.loading = false
	b.err = err
	if err == nil {
		b.page = page.resp
	}
	b.updateTable()
	return m
}

func (m ProfileDetailModel) handleSnapshotFilesKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	b := m.files
	if b == nil {
		m.backupsView = backupsSubSnapshots
		return m, nil
	}
	switch msg.String() {
	case "enter":
		if row := b.table.SelectedRow(); row != nil {
			if e := b.entry(row.Key); e != nil && e.IsDir {
				return m.browseTo(e.Path, "")
			}
		}
		return m, nil
	case "backspace", "u":
		if b.search != "" {
			return m.browseTo(b.path, "")
		}
		if b.path != "" {
			parent := ""
			if i := strings.LastIndex(b.path, "/"); i >= 0 {
				parent = b.path[:i]
			}
			return m.browseTo(parent, "")
		}
		return m, nil
	case "space", " ":
		if row := b.table.SelectedRow(); row != nil {
			if b.selected[row.Key] {
				delete(b.selected, row.Key)
			} else {
				b.selected[row.Key] = true
			}
			b.updateTable()
		}
		return m, nil
	case "c":
		b.selected = map[string]bool{}
		b.updateTable()
		return m, nil
	case "/":
		f := components.NewFormWithID(formSnapshotSearch, "Search the snapshot", []components.Field{
			{Name: "search", Label: "Path contains", Type: components.FieldText, Value: b.search},
		})
		f.Intro = "Every file whose path contains this text (ignoring case). Empty: back to the folder."
		m.form = f
		return m, nil
	case "R":
		if len(b.selected) == 0 {
			return m, flash("Select files or folders with Space first", true)
		}
		m.pendingFiles = &fileRestore{targetID: b.targetID, snapshotID: b.snapshotID, paths: b.selectedPaths()}
		m.confirm = components.NewConfirm(fmt.Sprintf(
			"Restore %d selected item(s) of snapshot %s to their original place in the local folder?\n\n"+
				"Only these files are written; the files they replace are kept in .omnisync-trash/pre-restore/.\n"+
				"In a mirror profile, automatic syncing is paused so the next pull does not remove the restored files:\n"+
				"review the diff, then push or resume. A two-way profile syncs them like any other change.",
			len(b.selected), b.snapshotID), "restore_files")
		return m, nil
	case "O":
		if len(b.selected) == 0 {
			return m, flash("Select files or folders with Space first", true)
		}
		m.pendingFiles = &fileRestore{targetID: b.targetID, snapshotID: b.snapshotID, paths: b.selectedPaths()}
		f := components.NewFormWithID(formRestoreFilesTo, "Restore into another folder", []components.Field{
			{Name: "target_dir", Label: "Folder", Type: components.FieldText, Required: true,
				Help: "An absolute local path; created if its parent exists"},
		})
		f.Intro = fmt.Sprintf("%d selected item(s) of snapshot %s, with their folders, into this folder.\n"+
			"Files there with the same names are kept in its .omnisync-trash/pre-restore/.", len(b.selected), b.snapshotID)
		m.form = f
		return m, nil
	case "r":
		b.loading = true
		return m, m.fetchSnapshotFiles(b)
	}
	b.table.Update(msg)
	return m, nil
}

func (m ProfileDetailModel) browseTo(path, search string) (tea.Model, tea.Cmd) {
	b := m.files
	b.path, b.search, b.loading, b.page, b.err = path, search, true, nil, nil
	b.updateTable()
	return m, m.fetchSnapshotFiles(b)
}

func (m ProfileDetailModel) renderSnapshotFiles() string {
	var sb strings.Builder
	b := m.files
	where := "/" + b.path
	if b.search != "" {
		where = fmt.Sprintf("search %q", b.search)
	}
	sb.WriteString(headerText(fmt.Sprintf("  Snapshot %s of target %d: %s", b.snapshotID, b.targetID, where)))
	sb.WriteString("\n")
	if b.err != nil {
		sb.WriteString(errorLine(b.err))
	}
	switch {
	case b.loading:
		sb.WriteString(mutedText("  Loading (an archive on a remote is downloaded once to list it)...") + "\n")
	case b.page != nil:
		sb.WriteString(b.table.View())
		sb.WriteString("\n")
		info := fmt.Sprintf("  %d entr(ies) here, %d file(s) in the snapshot, %d selected", b.page.Total, b.page.SnapshotFiles, len(b.selected))
		if b.page.Total > len(b.page.Entries) {
			info += fmt.Sprintf("; showing the first %d, search (/) to narrow", len(b.page.Entries))
		}
		sb.WriteString(mutedText(info) + "\n")
	}
	sb.WriteString(mutedText("  Enter:open folder  Backspace:up  Space:select  c:clear  /:search  R:restore here  O:restore to folder  Esc:back"))
	return sb.String()
}

// restoreFiles starts restoring the chosen files; the answer arrives as
// actionRestoreFilesStarted and the job is then followed.
func (m ProfileDetailModel) restoreFiles(req fileRestore) tea.Cmd {
	client, slug := m.client, m.slug
	return tea.Batch(
		flash(fmt.Sprintf("Starting the restore of %d item(s)...", len(req.paths)), false),
		startBackupCmd(slug, jobKindRestore, actionRestoreFilesStarted, req.targetID,
			func(ctx context.Context) (*api.BackupJobResponse, error) {
				return client.RestoreFiles(ctx, slug, req.targetID, api.RestoreFilesRequest{
					SnapshotID: req.snapshotID, Paths: req.paths, TargetDir: req.targetDir,
				})
			}),
	)
}

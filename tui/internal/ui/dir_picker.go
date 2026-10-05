package ui

import (
	"context"
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// pickerKind is what a dirPicker lists.
type pickerKind int

const (
	// pickLocal lists folders on the backend's machine (GET /browse/local).
	pickLocal pickerKind = iota
	// pickRemote lists the configured remotes (GET /remotes).
	pickRemote
	// pickRemoteDir lists folders on one remote (GET /browse/remote).
	pickRemoteDir
)

// actionBrowse tags the picker's API answers.
const actionBrowse = "browse"

// browseAnswer is the answer to one picker request; seq drops answers the
// user has already moved away from.
type browseAnswer struct {
	seq     int
	kind    pickerKind
	listing *api.BrowseResponse
	remotes []api.RemoteResponse
}

// pickerRow is one line of the picker: a folder to open, the parent, or
// "use this folder".
type pickerRow struct {
	label string
	path  string
	use   bool // choose the current folder
	up    bool // go to the parent (or back to the remote list)
}

// dirPicker is the folder picker the profile form opens with Ctrl+O. It
// browses local folders for local_dir, and remotes then their folders for
// remote_dir. It fills field with the chosen path.
type dirPicker struct {
	client *api.Client
	view   ViewID
	field  string
	kind   pickerKind
	seq    int
	// current is the folder listed ("gdrive:Backup" for a remote), parent
	// the one above it (nil at the top).
	current string
	parent  *string
	rows    []pickerRow
	cursor  int
	loading bool
	err     error
	height  int
}

// pickerChosenMsg carries the folder the user picked.
type pickerChosenMsg struct {
	field string
	path  string
}

// newLocalPicker opens the local folder browser at path ("" = home).
func newLocalPicker(client *api.Client, view ViewID, field, path string, height int) (*dirPicker, tea.Cmd) {
	p := &dirPicker{client: client, view: view, field: field, height: height}
	return p, p.loadLocal(path)
}

// newRemotePicker opens the remote folder browser. A value such as
// "gdrive:Backup" opens that folder; anything else lists the remotes.
func newRemotePicker(client *api.Client, view ViewID, field, value string, height int) (*dirPicker, tea.Cmd) {
	p := &dirPicker{client: client, view: view, field: field, height: height}
	if remoteOfPath(value) != "" {
		return p, p.loadRemoteDir(value)
	}
	return p, p.loadRemotes()
}

// remoteOfPath returns the remote of an rclone path such as "nas:Backups"
// ("nas"), or "" when path does not start with "<remote>:".
func remoteOfPath(path string) string {
	name, _, ok := strings.Cut(strings.TrimSpace(path), ":")
	if !ok || name == "" || strings.ContainsAny(name, `/\`) {
		return ""
	}
	return name
}

func (p *dirPicker) start(kind pickerKind) int {
	p.seq++
	p.kind = kind
	p.loading = true
	p.err = nil
	return p.seq
}

func (p *dirPicker) loadLocal(path string) tea.Cmd {
	seq, client, view := p.start(pickLocal), p.client, p.view
	return func() tea.Msg {
		resp, err := client.BrowseLocal(context.Background(), path)
		return ActionResultMsg{ViewID: view, Action: actionBrowse, Err: err,
			Data: browseAnswer{seq: seq, kind: pickLocal, listing: resp}}
	}
}

func (p *dirPicker) loadRemotes() tea.Cmd {
	seq, client, view := p.start(pickRemote), p.client, p.view
	return func() tea.Msg {
		remotes, err := client.ListRemotes(context.Background())
		return ActionResultMsg{ViewID: view, Action: actionBrowse, Err: err,
			Data: browseAnswer{seq: seq, kind: pickRemote, remotes: remotes}}
	}
}

func (p *dirPicker) loadRemoteDir(path string) tea.Cmd {
	seq, client, view := p.start(pickRemoteDir), p.client, p.view
	return func() tea.Msg {
		resp, err := client.BrowseRemote(context.Background(), path)
		return ActionResultMsg{ViewID: view, Action: actionBrowse, Err: err,
			Data: browseAnswer{seq: seq, kind: pickRemoteDir, listing: resp}}
	}
}

// handleAnswer applies an answer to the picker's latest request.
func (p *dirPicker) handleAnswer(msg ActionResultMsg) {
	ans, ok := msg.Data.(browseAnswer)
	if !ok || ans.seq != p.seq {
		return
	}
	p.loading = false
	p.cursor = 0
	if msg.Err != nil {
		p.err = msg.Err
		p.rows = p.fallbackRows()
		return
	}
	p.err = nil
	switch ans.kind {
	case pickRemote:
		p.current, p.parent = "", nil
		p.rows = nil
		for _, r := range ans.remotes {
			p.rows = append(p.rows, pickerRow{label: fmt.Sprintf("%s:  (%s)", safeLine(r.Name), safeLine(r.Type)), path: r.Name + ":"})
		}
	default:
		p.current, p.parent = ans.listing.Current, ans.listing.Parent
		p.rows = []pickerRow{{label: "[use this folder]", path: p.current, use: true}}
		switch {
		case p.parent != nil:
			p.rows = append(p.rows, pickerRow{label: "..", path: *p.parent, up: true})
		case ans.kind == pickRemoteDir:
			p.rows = append(p.rows, pickerRow{label: ".. (all remotes)", up: true})
		}
		for _, e := range ans.listing.Entries {
			p.rows = append(p.rows, pickerRow{label: e.Name + "/", path: p.childPath(e)})
		}
	}
}

// fallbackRows keeps a way out after an error: back to the remotes, or to
// the home folder.
func (p *dirPicker) fallbackRows() []pickerRow {
	switch p.kind {
	case pickRemoteDir:
		return []pickerRow{{label: ".. (all remotes)", up: true}}
	case pickLocal:
		return []pickerRow{{label: "~ (home folder)", path: ""}}
	}
	return nil
}

// childPath is the path of a listed folder. Remote paths are built from the
// current folder, so they stay relative to the remote's root ("gdrive:A/B",
// not "gdrive:/A/B", which some backends read as an absolute path).
func (p *dirPicker) childPath(e api.DirEntry) string {
	if p.kind != pickRemoteDir {
		return e.Path
	}
	if strings.HasSuffix(p.current, ":") || strings.HasSuffix(p.current, "/") {
		return p.current + e.Name
	}
	return p.current + "/" + e.Name
}

// open acts on a row: choose, go up or descend.
func (p *dirPicker) open(row pickerRow) tea.Cmd {
	switch {
	case row.use:
		field, path := p.field, row.path
		return func() tea.Msg { return pickerChosenMsg{field: field, path: path} }
	case row.up && row.path == "" && (p.kind == pickRemoteDir):
		return p.loadRemotes()
	case p.kind == pickLocal:
		return p.loadLocal(row.path)
	}
	return p.loadRemoteDir(row.path)
}

// update handles a key; it returns false when the picker was closed (Esc).
func (p *dirPicker) update(msg tea.KeyPressMsg) (bool, tea.Cmd) {
	switch msg.String() {
	case "esc":
		return false, nil
	case "up", "k":
		if p.cursor > 0 {
			p.cursor--
		}
	case "down", "j":
		if p.cursor < len(p.rows)-1 {
			p.cursor++
		}
	case "enter", "right", "l":
		if p.cursor < len(p.rows) && !p.loading {
			return true, p.open(p.rows[p.cursor])
		}
	case "left", "h", "backspace":
		if p.loading {
			return true, nil
		}
		for _, r := range p.rows {
			if r.up {
				return true, p.open(r)
			}
		}
	case "s", " ", "space":
		if p.kind != pickRemote && p.current != "" && !p.loading && p.err == nil {
			return true, p.open(pickerRow{use: true, path: p.current})
		}
	case "~":
		if p.kind == pickLocal {
			return true, p.loadLocal("")
		}
	}
	return true, nil
}

// hints names the picker's keys for the bottom bar.
func (p *dirPicker) hints() string {
	if p.kind == pickRemote {
		return "Up/Down:move  Enter:open remote  Esc:back to form"
	}
	h := "Up/Down:move  Enter/Right:open  Left:up  s:use this folder  Esc:back to form"
	if p.kind == pickLocal {
		h += "  ~:home"
	}
	return h
}

// View renders the picker.
func (p *dirPicker) View() string {
	var b strings.Builder
	title := "Choose the local folder"
	switch p.kind {
	case pickRemote:
		title = "Choose a remote"
	case pickRemoteDir:
		title = "Choose the folder on the remote"
	}
	b.WriteString(headerText(title))
	b.WriteString("\n")
	if p.current != "" {
		b.WriteString(mutedText("In: ") + p.current + "\n")
	}
	if p.kind == pickLocal {
		b.WriteString(mutedText("Folders on the machine the OmniSync backend runs on.") + "\n")
	}
	b.WriteString("\n")
	if p.err != nil {
		b.WriteString(lipgloss.NewStyle().Foreground(theme.Current.Error).Render("Error: "+apiDetail(p.err)) + "\n\n")
	}
	switch {
	case p.loading:
		b.WriteString("Loading...\n")
	case len(p.rows) == 0 && p.kind == pickRemote:
		b.WriteString("No remotes yet. Add one in the Remotes view (6).\n")
	}
	if !p.loading {
		b.WriteString(p.renderRows())
	}
	b.WriteString("\n" + mutedText(p.hints()))
	style := lipgloss.NewStyle().Border(theme.Current.Border).BorderForeground(theme.Current.Secondary).Padding(1, 2)
	return style.Render(b.String())
}

// renderRows draws the rows around the cursor that fit the screen.
func (p *dirPicker) renderRows() string {
	visible := p.height - 14
	if visible < 5 {
		visible = 5
	}
	start := 0
	if p.cursor >= visible {
		start = p.cursor - visible + 1
	}
	end := start + visible
	if end > len(p.rows) {
		end = len(p.rows)
	}
	var b strings.Builder
	sel := lipgloss.NewStyle().Reverse(true).Bold(true)
	g := theme.Glyphs()
	for i := start; i < end; i++ {
		label := components.Truncate(p.rows[i].label, 70)
		if i == p.cursor {
			b.WriteString(g.Cursor + " " + sel.Render(label) + "\n")
		} else {
			b.WriteString("  " + label + "\n")
		}
	}
	if end < len(p.rows) {
		b.WriteString(mutedText(fmt.Sprintf("  ... %d more", len(p.rows)-end)) + "\n")
	}
	return b.String()
}

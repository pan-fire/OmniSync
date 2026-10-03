package ui

import (
	"context"
	"fmt"
	"path"
	"strconv"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

type conflictsMode int

const (
	conflictsModeList conflictsMode = iota
	// conflictsModeChoose shows the actions for the captured conflict.
	conflictsModeChoose
)

// conflictAction is one way to resolve a conflict, as offered to the user.
type conflictAction struct {
	key        string
	resolution api.ConflictResolution
	label      string
}

// conflictActions are the choices, in the order they are shown.
var conflictActions = []conflictAction{
	{"l", api.ConflictKeepLocal, "Keep local"},
	{"r", api.ConflictKeepRemote, "Keep remote"},
	{"b", api.ConflictKeepBoth, "Keep both"},
	{"d", api.ConflictDismiss, "Dismiss"},
}

// twoWayConflictActions are the choices for a conflict a two-way sync found:
// both versions already exist on both sides, so the keep actions keep only
// one of them.
var twoWayConflictActions = []conflictAction{
	{"l", api.ConflictKeepLocal, "Keep only local"},
	{"r", api.ConflictKeepRemote, "Keep only remote"},
	{"b", api.ConflictKeepBoth, "Keep both"},
	{"d", api.ConflictDismiss, "Dismiss"},
}

// actionsFor returns the choices offered for a conflict.
func actionsFor(c api.ConflictResponse) []conflictAction {
	if c.TwoWay() {
		return twoWayConflictActions
	}
	return conflictActions
}

// resolveOutcome is the result of POST /conflicts/{id}/resolve.
type resolveOutcome struct {
	conflict   api.ConflictResponse
	resolution api.ConflictResolution
}

// conflictsPage carries GET /conflicts, tagged with the profile filter it
// answers.
type conflictsPage struct {
	Filter    string
	Conflicts []api.ConflictResponse
}

// ConflictsModel is the Conflicts view.
type ConflictsModel struct {
	client    *api.Client
	conflicts []api.ConflictResponse
	// filter is "" (all profiles) or a profile slug; f cycles through the
	// slugs in profiles.
	filter   string
	profiles []string
	table    components.Table
	mode     conflictsMode
	// pending is the conflict being resolved, captured on Enter; the
	// confirmation and the request act on exactly this one.
	pending    *api.ConflictResponse
	pendingRes api.ConflictResolution
	confirm    components.Confirm
	// resolving is set while a resolve request runs.
	resolving *api.ConflictResponse
	// notice is why the last resolve failed (the backend's detail); it stays
	// until the next resolve.
	notice  string
	loading bool
	err     error
	width   int
	height  int
}

// NewConflictsModel creates a new conflicts view.
func NewConflictsModel(client *api.Client) ConflictsModel {
	cols := []components.Column{
		{Title: "ID", Width: 5},
		{Title: "Profile", Width: 16},
		{Title: "File", Width: 34},
		{Title: "Job", Width: 5},
		{Title: "Local modified", Width: 19},
		{Title: "Remote modified", Width: 19},
	}
	return ConflictsModel{
		client:  client,
		table:   components.NewTable(cols, 15),
		loading: true,
	}
}

func (m ConflictsModel) ViewID() ViewID { return ViewConflicts }

// CapturesInput is true while choosing a resolution or answering its prompt.
func (m ConflictsModel) CapturesInput() bool {
	return m.mode == conflictsModeChoose || m.confirm.Active
}

// KeyHints names the keys of the current mode for the bottom bar.
func (m ConflictsModel) KeyHints() string {
	switch {
	case m.confirm.Active:
		return confirmHints
	case m.mode == conflictsModeChoose:
		return "l:keep local  r:keep remote  b:keep both  d:dismiss  Esc:cancel"
	}
	return "Enter:resolve  f:filter  r:refresh"
}

func (m ConflictsModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "Up/Down, j/k", Desc: "Move"},
		{Key: "n/N", Desc: "Next/previous page"},
		{Key: "f", Desc: "Filter: cycle all profiles / one profile"},
		{Key: "Enter", Desc: "Choose how to resolve the highlighted conflict"},
		{Key: "l", Desc: "(resolving) Keep local: copy the local file over the remote one (remote version to .omnisync-trash; asks first)"},
		{Key: "r", Desc: "(resolving) Keep remote: copy the remote file over the local one (local version to .omnisync-trash; asks first)"},
		{Key: "b", Desc: "(resolving) Keep both: keep both versions on both sides, the remote one as <name>.conflict-<time> (asks first)"},
		{Key: "d", Desc: "(resolving) Dismiss: close the conflict without changing any file (asks first)"},
		{Key: "l / r", Desc: "(two-way conflict, both versions already kept) Keep only local / remote under the file's name; the other copy goes to .omnisync-trash (asks first)"},
		{Key: "b / d", Desc: "(two-way conflict) Keep both / Dismiss: close the conflict; both files stay (asks first)"},
		{Key: "Esc", Desc: "(resolving) Cancel"},
		{Key: "r / R", Desc: "Refresh"},
	}
}

func (m ConflictsModel) Init() tea.Cmd {
	return tea.Batch(m.fetchConflicts(), m.fetchProfileSlugs())
}

func (m ConflictsModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.table.SetWidth(msg.Width)

	case PollResultMsg:
		return m.handlePollResult(msg)

	case ActionResultMsg:
		return m.handleActionResult(msg)

	case components.ConfirmResultMsg:
		return m.handleConfirmResult(msg)

	case TickMsg:
		return m, m.fetchConflicts()

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m ConflictsModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	switch data := msg.Data.(type) {
	case conflictsPage:
		if data.Filter != m.filter {
			return m, nil // answer to an older filter
		}
		m.loading = false
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		m.err = nil
		m.conflicts = data.Conflicts
		m.updateTable()
	case []api.ProfileStatusResponse:
		if msg.Err == nil {
			slugs := make([]string, 0, len(data))
			for _, p := range data {
				slugs = append(slugs, p.Slug)
			}
			m.profiles = slugs
		}
	default:
		if msg.Err != nil {
			m.loading = false
			m.err = msg.Err
		}
	}
	return m, nil
}

func (m ConflictsModel) handleActionResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	if msg.Action != "resolve" {
		return m, nil
	}
	m.resolving = nil
	out, _ := msg.Data.(resolveOutcome)
	label := resolutionLabel(out.conflict, out.resolution)
	if msg.Err != nil {
		m.notice = fmt.Sprintf("%s failed for %s: %s", label, out.conflict.FilePath, apiDetail(msg.Err))
		return m, tea.Batch(m.fetchConflicts(), flash(m.notice, true))
	}
	m.notice = ""
	text := fmt.Sprintf("%s: %s resolved", label, out.conflict.FilePath)
	switch {
	case out.resolution == api.ConflictDismiss:
		text = fmt.Sprintf("Conflict for %s dismissed; no file was changed", out.conflict.FilePath)
	case out.resolution == api.ConflictKeepBoth && out.conflict.TwoWay():
		text = fmt.Sprintf("Conflict for %s closed; both versions stay", out.conflict.FilePath)
	}
	return m, tea.Batch(m.fetchConflicts(), flash(text, false))
}

func (m ConflictsModel) handleConfirmResult(msg components.ConfirmResultMsg) (tea.Model, tea.Cmd) {
	if msg.Tag != "resolve" {
		return m, nil
	}
	target, res := m.pending, m.pendingRes
	m.pending = nil
	m.pendingRes = ""
	m.mode = conflictsModeList
	if target == nil {
		return m, nil
	}
	if !msg.Confirmed {
		return m, flash(resolutionLabel(*target, res)+" cancelled; nothing was changed", false)
	}
	m.resolving = target
	m.notice = ""
	return m, m.resolve(*target, res)
}

func (m ConflictsModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.confirm.Active {
		_, cmd := m.confirm.Update(msg)
		return m, cmd
	}
	if m.mode == conflictsModeChoose {
		if msg.String() == "esc" {
			m.mode = conflictsModeList
			m.pending = nil
			return m, nil
		}
		if m.pending == nil {
			return m, nil
		}
		for _, a := range actionsFor(*m.pending) {
			if msg.String() == a.key {
				m.pendingRes = a.resolution
				m.confirm = components.NewConfirm(resolvePrompt(*m.pending, a), "resolve")
				return m, nil
			}
		}
		return m, nil
	}

	switch msg.String() {
	case "enter":
		if m.resolving != nil {
			return m, flash("Wait until the running resolve has finished", true)
		}
		if row := m.table.SelectedRow(); row != nil {
			if c := m.find(row.Key); c != nil {
				m.pending = c
				m.mode = conflictsModeChoose
			}
		}
		return m, nil
	case "f":
		m.filter = nextFilter(m.filter, m.profiles)
		m.loading = true
		m.conflicts = nil
		m.updateTable()
		return m, m.fetchConflicts()
	case "r", "R":
		m.loading = true
		return m, tea.Batch(m.fetchConflicts(), m.fetchProfileSlugs())
	default:
		m.table.Update(msg)
	}
	return m, nil
}

func (m ConflictsModel) find(key string) *api.ConflictResponse {
	for _, c := range m.conflicts {
		if strconv.Itoa(c.ID) == key {
			cc := c
			return &cc
		}
	}
	return nil
}

func (m ConflictsModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder
	b.WriteString(headerText("  Conflicts"))
	b.WriteString(mutedText("  Filter: " + m.filterLabel()))
	b.WriteString("\n")

	if m.confirm.Active {
		b.WriteString(m.confirm.View())
		return tea.NewView(b.String())
	}

	if m.err != nil {
		b.WriteString(errorLine(m.err))
	}
	if m.notice != "" {
		b.WriteString(lipgloss.NewStyle().Foreground(theme.Current.Error).Render("  " + m.notice))
		b.WriteString("\n\n")
	}
	if m.resolving != nil {
		fmt.Fprintf(&b, "  Resolving %s...\n\n", m.resolving.FilePath)
	}

	if m.loading && len(m.conflicts) == 0 && m.err == nil {
		b.WriteString("  Loading...")
		return tea.NewView(b.String())
	}

	if len(m.conflicts) == 0 && m.pending == nil {
		successStyle := lipgloss.NewStyle().Foreground(theme.Current.Success)
		b.WriteString("\n")
		if m.filter != "" {
			b.WriteString(successStyle.Render(fmt.Sprintf("  No conflicts for profile %s %s all clear", m.filter, theme.Glyphs().EmDash)))
		} else {
			b.WriteString(successStyle.Render(fmt.Sprintf("  No conflicts %s all clear %s", theme.Glyphs().EmDash, theme.Glyphs().Check)))
		}
		return tea.NewView(b.String())
	}

	b.WriteString(m.table.View())
	b.WriteString("\n")
	if m.mode == conflictsModeChoose && m.pending != nil {
		b.WriteString(m.renderChoices(*m.pending))
	} else {
		if row := m.table.SelectedRow(); row != nil {
			if c := m.find(row.Key); c != nil && c.TwoWay() {
				fmt.Fprintf(&b, "  %s\n", keptAsSentence(*c))
			}
		}
	}

	return tea.NewView(b.String())
}

// renderChoices lists what each action does to the captured conflict.
func (m ConflictsModel) renderChoices(c api.ConflictResponse) string {
	warnStyle := lipgloss.NewStyle().Foreground(theme.Current.Warning)
	var b strings.Builder
	b.WriteString(warnStyle.Render(fmt.Sprintf("  Resolve conflict %d: %s in profile %s", c.ID, c.FilePath, conflictProfile(c))))
	b.WriteString("\n")
	if c.TwoWay() {
		fmt.Fprintf(&b, "  %s\n", keptAsSentence(c))
	}
	if times := conflictTimes(c); times != "" {
		fmt.Fprintf(&b, "  %s\n", times)
	}
	for _, a := range actionsFor(c) {
		fmt.Fprintf(&b, "    %s  %-17s %s\n", a.key, a.label+":", resolutionSummary(c, a.resolution))
	}
	b.WriteString(mutedText("    Esc  Cancel"))
	return b.String()
}

// keptAs is the name one version of a two-way conflict was kept under.
func keptAs(name *string, c api.ConflictResponse) string {
	if name != nil && *name != "" {
		return *name
	}
	return c.FilePath
}

// keptAsSentence says where a two-way sync kept both versions of a conflict.
func keptAsSentence(c api.ConflictResponse) string {
	return fmt.Sprintf("Both versions were kept: local version as %s, remote version as %s",
		keptAs(c.LocalKeptAs, c), keptAs(c.RemoteKeptAs, c))
}

func (m *ConflictsModel) updateTable() {
	var rows []components.Row
	for _, c := range m.conflicts {
		job := "-"
		if c.JobID != nil {
			job = strconv.Itoa(*c.JobID)
		}
		profile := "-"
		switch {
		case c.ProfileName != nil && *c.ProfileName != "":
			profile = *c.ProfileName
		case c.ProfileSlug != nil && *c.ProfileSlug != "":
			profile = *c.ProfileSlug
		}
		rows = append(rows, components.Row{
			Key: strconv.Itoa(c.ID),
			Values: []string{
				strconv.Itoa(c.ID),
				profile,
				c.FilePath,
				job,
				formatTimePtr(c.LocalModified, "-"),
				formatTimePtr(c.RemoteModified, "-"),
			},
		})
	}
	m.table.SetRows(rows)
}

// resolvePrompt asks to confirm one action on one conflict.
func resolvePrompt(c api.ConflictResponse, a conflictAction) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s for %q in profile %s?\n\n", a.label, c.FilePath, conflictProfile(c))
	fmt.Fprintf(&b, "This will %s.", resolutionEffect(c, a.resolution))
	if times := conflictTimes(c); times != "" {
		fmt.Fprintf(&b, "\n\n%s", times)
	}
	return b.String()
}

// resolutionEffect says in full what an action does to the files of a
// conflict (two lines).
func resolutionEffect(c api.ConflictResponse, res api.ConflictResolution) string {
	if c.TwoWay() {
		return twoWayResolutionEffect(c, res)
	}
	switch res {
	case api.ConflictKeepLocal:
		return "copy the local file over the remote one;\nthe remote version is moved to .omnisync-trash on the remote"
	case api.ConflictKeepRemote:
		return "copy the remote file over the local one;\nthe local version is moved to .omnisync-trash in the local folder"
	case api.ConflictKeepBoth:
		return fmt.Sprintf("keep both versions on both sides;\nthe remote version is saved next to the local one as %s", conflictCopyName(c.FilePath))
	case api.ConflictDismiss:
		return "close this conflict without changing any file;\nboth versions stay as they are"
	}
	return string(res)
}

// twoWayResolutionEffect is resolutionEffect for a conflict a two-way sync
// found, whose two versions already exist on both sides.
func twoWayResolutionEffect(c api.ConflictResponse, res api.ConflictResolution) string {
	local, remote := keptAs(c.LocalKeptAs, c), keptAs(c.RemoteKeptAs, c)
	switch res {
	case api.ConflictKeepLocal:
		return fmt.Sprintf("keep only the local version, as %s on both sides;\nthe other copy (%s) is moved to .omnisync-trash", c.FilePath, remote)
	case api.ConflictKeepRemote:
		return fmt.Sprintf("keep only the remote version, as %s on both sides;\nthe other copy (%s) is moved to .omnisync-trash", c.FilePath, local)
	case api.ConflictKeepBoth:
		return fmt.Sprintf("close this conflict and keep both files on both sides:\nthe local version as %s, the remote version as %s", local, remote)
	case api.ConflictDismiss:
		return fmt.Sprintf("close this conflict without changing any file;\nboth files stay: the local version as %s, the remote version as %s", local, remote)
	}
	return string(res)
}

// resolutionSummary is the one-line form of resolutionEffect for the menu.
func resolutionSummary(c api.ConflictResponse, res api.ConflictResolution) string {
	if c.TwoWay() {
		switch res {
		case api.ConflictKeepLocal:
			return fmt.Sprintf("keep only the local version, as %s (the other copy to .omnisync-trash)", c.FilePath)
		case api.ConflictKeepRemote:
			return fmt.Sprintf("keep only the remote version, as %s (the other copy to .omnisync-trash)", c.FilePath)
		case api.ConflictKeepBoth:
			return "close the conflict; both files stay on both sides"
		case api.ConflictDismiss:
			return "close the conflict without changing any file; both files stay"
		}
	}
	switch res {
	case api.ConflictKeepLocal:
		return "copy the local file over the remote one (remote version to .omnisync-trash)"
	case api.ConflictKeepRemote:
		return "copy the remote file over the local one (local version to .omnisync-trash)"
	case api.ConflictKeepBoth:
		return fmt.Sprintf("keep both versions on both sides (remote one as %s)", conflictCopyName(c.FilePath))
	case api.ConflictDismiss:
		return "close the conflict without changing any file"
	}
	return string(res)
}

func resolutionLabel(c api.ConflictResponse, res api.ConflictResolution) string {
	for _, a := range actionsFor(c) {
		if a.resolution == res {
			return a.label
		}
	}
	return "Resolve"
}

// conflictProfile names the profile of a conflict.
func conflictProfile(c api.ConflictResponse) string {
	switch {
	case c.ProfileName != nil && *c.ProfileName != "" && c.ProfileSlug != nil && *c.ProfileSlug != "":
		return fmt.Sprintf("%q (%s)", *c.ProfileName, *c.ProfileSlug)
	case c.ProfileName != nil && *c.ProfileName != "":
		return fmt.Sprintf("%q", *c.ProfileName)
	case c.ProfileSlug != nil && *c.ProfileSlug != "":
		return *c.ProfileSlug
	}
	return "(unknown)"
}

// conflictTimes renders the modification times that are known.
func conflictTimes(c api.ConflictResponse) string {
	var parts []string
	if c.LocalModified != nil && *c.LocalModified != "" {
		parts = append(parts, "Local modified: "+formatTime(*c.LocalModified))
	}
	if c.RemoteModified != nil && *c.RemoteModified != "" {
		parts = append(parts, "Remote modified: "+formatTime(*c.RemoteModified))
	}
	return strings.Join(parts, "   ")
}

// conflictCopyName is the name keep_both gives the remote version:
// <stem>.conflict-<time><ext>, like the backend.
func conflictCopyName(p string) string {
	base := path.Base(p)
	ext := path.Ext(base)
	stem := strings.TrimSuffix(base, ext)
	if stem == "" { // a dotfile such as .bashrc has no extension
		stem, ext = base, ""
	}
	return stem + ".conflict-<time>" + ext
}

func (m ConflictsModel) resolve(c api.ConflictResponse, resolution api.ConflictResolution) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		_, err := client.ResolveConflict(context.Background(), c.ID, resolution)
		return ActionResultMsg{ViewID: ViewConflicts, Action: "resolve", Err: err,
			Data: resolveOutcome{conflict: c, resolution: resolution}}
	}
}

func (m ConflictsModel) filterLabel() string {
	if m.filter == "" {
		return "all profiles"
	}
	return "profile " + m.filter
}

func (m ConflictsModel) fetchConflicts() tea.Cmd {
	client, filter := m.client, m.filter
	return func() tea.Msg {
		data, err := client.ListConflicts(context.Background(), filter)
		return PollResultMsg{ViewID: ViewConflicts, Data: conflictsPage{Filter: filter, Conflicts: data}, Err: err}
	}
}

func (m ConflictsModel) fetchProfileSlugs() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.ListProfiles(context.Background())
		return PollResultMsg{ViewID: ViewConflicts, Data: data, Err: err}
	}
}

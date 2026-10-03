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

type remotesMode int

const (
	remotesModeList remotesMode = iota
	remotesModeCheckingDeps
	remotesModeConfirmDelete
	// Quick add (c): load the providers, ask name and type, then the
	// provider's fields, then create.
	remotesModeLoadingProviders
	remotesModeQuickAdd
	remotesModeQuickParams
	remotesModeCreating
	// Edit (e): load the settings, the form, saving.
	remotesModeLoadingEdit
	remotesModeEdit
	remotesModeSaving
	// Import (I): the file's path, the backend's preview, the selection,
	// importing.
	remotesModeImportPath
	remotesModeImportChecking
	remotesModeImportSelect
	remotesModeImporting
)

const (
	formQuickAdd    = "quick_add_remote"
	formQuickParams = "quick_add_params"
)

// remoteDeps carries the dependencies of the remote a delete prompt is for.
type remoteDeps struct {
	Name string
	Deps *api.RemoteDependenciesResponse
}

// remoteTest and remoteInfo tag test/about answers with their remote.
type remoteTest struct {
	Name string
	Resp *api.RemoteTestResponse
}

type remoteInfo struct {
	Name string
	Resp *api.RemoteStorageInfoResponse
}

// RemotesModel is the Remotes view.
type RemotesModel struct {
	client  *api.Client
	remotes []api.RemoteResponse
	table   components.Table
	confirm components.Confirm
	mode    remotesMode
	// pendingName/pendingForce describe the delete the prompt acts on,
	// captured when it opened.
	pendingName  string
	pendingForce bool
	testResult   *remoteTest
	storageInfo  *remoteInfo
	// Quick add: the key-based providers, the form open now, and the name
	// and provider chosen in its first step.
	providers   []api.ProviderResponse
	form        components.Form
	addName     string
	addProvider *api.ProviderResponse
	// The remote being edited, and the rclone.conf being imported.
	edit      *editData
	importing *importData
	loading   bool
	err       error
	width     int
	height    int
}

// NewRemotesModel creates a new remotes view.
func NewRemotesModel(client *api.Client) RemotesModel {
	cols := []components.Column{
		{Title: "Name", Width: 20},
		{Title: "Type", Width: 24},
		{Title: "Last verified", Width: 19},
	}
	return RemotesModel{
		client:  client,
		table:   components.NewTable(cols, 15),
		loading: true,
	}
}

func (m RemotesModel) ViewID() ViewID { return ViewRemotes }

// CapturesInput is true while the delete prompt is open.
func (m RemotesModel) CapturesInput() bool { return m.mode != remotesModeList }

// KeyHints names the keys of the current mode for the bottom bar.
func (m RemotesModel) KeyHints() string {
	switch m.mode {
	case remotesModeConfirmDelete:
		return confirmHints
	case remotesModeCheckingDeps, remotesModeLoadingProviders, remotesModeLoadingEdit:
		return "Esc:cancel"
	case remotesModeQuickAdd, remotesModeQuickParams, remotesModeEdit, remotesModeImportPath, remotesModeImportSelect:
		return formHints
	case remotesModeCreating, remotesModeSaving, remotesModeImportChecking, remotesModeImporting:
		return "Working..."
	}
	return "c:quick add  e:edit  a:reconnect  I:import  t:test  i:info  w:wizard  d:delete  r:refresh"
}

func (m RemotesModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "Up/Down, j/k", Desc: "Move"},
		{Key: "t", Desc: "Test remote"},
		{Key: "i", Desc: "Storage info"},
		{Key: "c", Desc: "Quick add: a remote that signs in with keys or a password (name, type, its fields)"},
		{Key: "w", Desc: "Open the setup wizard (also for OAuth sign-in)"},
		{Key: "e", Desc: "Edit the remote's settings; empty secrets keep the stored ones"},
		{Key: "a", Desc: "Reconnect: sign an OAuth remote (Drive, Dropbox, OneDrive) in again"},
		{Key: "I", Desc: "Import remotes from an rclone.conf on this computer"},
		{Key: "d", Desc: "Delete remote (asks first)"},
		{Key: "r", Desc: "Refresh"},
	}
}

func (m RemotesModel) Init() tea.Cmd {
	return m.fetchRemotes()
}

func (m RemotesModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.table.SetWidth(msg.Width)

	case PollResultMsg:
		return m.handlePollResult(msg)

	case ActionResultMsg:
		return m.handleActionResult(msg)

	case TickMsg:
		return m, m.fetchRemotes()

	case components.ConfirmResultMsg:
		return m.handleConfirmResult(msg)

	case components.FormSubmitMsg:
		return m.handleFormSubmit(msg)

	case components.FormCancelMsg:
		if m.formOpen() {
			m.mode = remotesModeList
			m.addProvider = nil
			m.edit = nil
			m.importing = nil
			m.pendingName = ""
		}
		return m, nil

	case tea.PasteMsg:
		if m.formOpen() {
			_, cmd := m.form.Update(msg)
			return m, cmd
		}

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

// formOpen is true while one of the view's forms has the keyboard.
func (m RemotesModel) formOpen() bool {
	switch m.mode {
	case remotesModeQuickAdd, remotesModeQuickParams, remotesModeEdit, remotesModeImportPath, remotesModeImportSelect:
		return true
	}
	return false
}

func (m RemotesModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	switch data := msg.Data.(type) {
	case []api.RemoteResponse:
		m.loading = false
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		m.err = nil
		m.remotes = data
		m.updateTable()
	case remoteInfo:
		if msg.Err != nil {
			return m, errorFlash("Storage info for "+data.Name+" failed", msg.Err)
		}
		m.storageInfo = &data
	default:
		if msg.Err != nil {
			m.err = msg.Err
			m.loading = false
		}
	}
	return m, nil
}

func (m RemotesModel) handleActionResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	switch msg.Action {
	case "quick_providers":
		return m.handleProviders(msg)
	case "quick_create":
		return m.handleQuickCreated(msg)
	case "edit_loaded":
		return m.handleEditLoaded(msg)
	case "edit_saved":
		return m.handleEditSaved(msg)
	case "import_preview":
		return m.handleImportPreview(msg)
	case "import_done":
		return m.handleImportDone(msg)
	case "remote_deps":
		deps, _ := msg.Data.(remoteDeps)
		if m.mode != remotesModeCheckingDeps || deps.Name != m.pendingName {
			return m, nil
		}
		if msg.Err != nil {
			m.mode = remotesModeList
			m.pendingName = ""
			return m, errorFlash("Could not check what uses "+deps.Name, msg.Err)
		}
		m.mode = remotesModeConfirmDelete
		m.pendingForce = !deps.Deps.Empty()
		m.confirm = components.NewConfirm(deletePrompt(deps.Name, deps.Deps), "delete")
		return m, nil
	case "test_remote":
		t, _ := msg.Data.(remoteTest)
		if msg.Err != nil {
			return m, errorFlash("Test of "+t.Name+" failed", msg.Err)
		}
		m.testResult = &t
		// The test changed the remote's sign-in state on the server.
		refresh := m.fetchRemotes()
		if t.Resp != nil && !t.Resp.Success {
			errTxt := "unknown error"
			if t.Resp.Error != nil {
				errTxt = *t.Resp.Error
			}
			if t.Resp.AuthError {
				errTxt = "the provider refused the sign-in; " + m.authFix(t.Name)
			}
			return m, tea.Batch(refresh, flash("Test of "+t.Name+" failed: "+errTxt, true))
		}
		return m, tea.Batch(refresh, flash("Test of "+t.Name+" passed", false))
	case "delete_remote":
		if msg.Err != nil {
			return m, tea.Batch(m.fetchRemotes(), errorFlash("Delete failed", msg.Err))
		}
		return m, tea.Batch(m.fetchRemotes(), flash("Remote deleted", false))
	}
	return m, nil
}

// authFix names the key that fixes a refused sign-in of remote name.
func (m RemotesModel) authFix(name string) string {
	for _, r := range m.remotes {
		if r.Name != name {
			continue
		}
		if r.Reconnectable {
			return "press a to reconnect it"
		}
		if r.Editable {
			return "press e to update its credentials"
		}
	}
	return "check its credentials"
}

func deletePrompt(name string, deps *api.RemoteDependenciesResponse) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Delete remote %q?\n\nThis removes the rclone remote configuration, not the files stored on it.", name)
	if !deps.Empty() {
		b.WriteString("\n\nStill used by:")
		for _, p := range deps.Profiles {
			fmt.Fprintf(&b, "\n  profile %s (%s)", p.Name, p.Slug)
		}
		for _, t := range deps.BackupTargets {
			fmt.Fprintf(&b, "\n  backup target %s of profile %s", t.TargetName, t.ProfileSlug)
		}
		b.WriteString("\nThey stop working until they point at another remote.")
	}
	return b.String()
}

func (m RemotesModel) handleConfirmResult(msg components.ConfirmResultMsg) (tea.Model, tea.Cmd) {
	name, force := m.pendingName, m.pendingForce
	m.mode = remotesModeList
	m.pendingName = ""
	m.pendingForce = false
	if msg.Tag == "delete" && msg.Confirmed && name != "" {
		return m, m.deleteRemote(name, force)
	}
	return m, nil
}

func (m RemotesModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch m.mode {
	case remotesModeConfirmDelete:
		_, cmd := m.confirm.Update(msg)
		return m, cmd
	case remotesModeCheckingDeps:
		if msg.String() == "esc" {
			m.mode = remotesModeList
			m.pendingName = ""
		}
		return m, nil
	case remotesModeLoadingProviders:
		if msg.String() == "esc" {
			m.mode = remotesModeList
		}
		return m, nil
	case remotesModeQuickAdd, remotesModeQuickParams, remotesModeEdit, remotesModeImportPath, remotesModeImportSelect:
		_, cmd := m.form.Update(msg)
		return m, cmd
	case remotesModeLoadingEdit:
		if msg.String() == "esc" {
			m.mode = remotesModeList
			m.pendingName = ""
		}
		return m, nil
	case remotesModeCreating, remotesModeSaving, remotesModeImportChecking, remotesModeImporting:
		return m, nil
	}

	switch msg.String() {
	case "c":
		m.mode = remotesModeLoadingProviders
		return m, m.fetchProviders()
	case "t":
		if row := m.table.SelectedRow(); row != nil {
			return m, tea.Batch(flash("Testing "+row.Key+"...", false), m.testRemote(row.Key))
		}
	case "i":
		if row := m.table.SelectedRow(); row != nil {
			return m, m.fetchStorageInfo(row.Key)
		}
	case "w":
		return m, func() tea.Msg {
			return NavigateMsg{Target: ViewWizard}
		}
	case "e":
		return m.startEdit()
	case "a":
		return m.reconnect()
	case "I":
		return m.startImport()
	case "d":
		if row := m.table.SelectedRow(); row != nil {
			m.mode = remotesModeCheckingDeps
			m.pendingName = row.Key
			return m, m.fetchDeps(row.Key)
		}
	case "r":
		m.loading = true
		return m, m.fetchRemotes()
	default:
		m.table.Update(msg)
	}
	return m, nil
}

func (m RemotesModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder

	switch m.mode {
	case remotesModeConfirmDelete:
		b.WriteString(m.confirm.View())
		return tea.NewView(b.String())
	case remotesModeLoadingProviders:
		b.WriteString("  Loading the remote types...\n\n")
		b.WriteString(mutedText("  Esc: cancel"))
		return tea.NewView(b.String())
	case remotesModeQuickAdd, remotesModeQuickParams, remotesModeEdit, remotesModeImportPath, remotesModeImportSelect:
		b.WriteString(m.form.View())
		return tea.NewView(b.String())
	case remotesModeCreating:
		fmt.Fprintf(&b, "  Creating remote %q...", m.addName)
		return tea.NewView(b.String())
	case remotesModeLoadingEdit:
		fmt.Fprintf(&b, "  Loading the settings of %q...\n\n", m.pendingName)
		b.WriteString(mutedText("  Esc: cancel"))
		return tea.NewView(b.String())
	case remotesModeSaving:
		fmt.Fprintf(&b, "  Saving %q...", m.pendingName)
		return tea.NewView(b.String())
	case remotesModeImportChecking:
		b.WriteString("  Reading the rclone.conf...")
		return tea.NewView(b.String())
	case remotesModeImporting:
		b.WriteString("  Importing...")
		return tea.NewView(b.String())
	case remotesModeCheckingDeps:
		fmt.Fprintf(&b, "  Checking what uses %q...\n\n", m.pendingName)
		b.WriteString(mutedText("  Esc: cancel"))
		return tea.NewView(b.String())
	}

	b.WriteString(headerText("  Remotes"))
	b.WriteString("\n")

	if m.err != nil {
		b.WriteString(errorLine(m.err))
	}

	if m.loading && len(m.remotes) == 0 && m.err == nil {
		b.WriteString("  Loading...")
		return tea.NewView(b.String())
	}

	b.WriteString(m.table.View())

	// A refused sign-in is shown with its fix.
	if r := m.selectedRemote(); r != nil && r.AuthError {
		b.WriteString("\n")
		b.WriteString(lipgloss.NewStyle().Foreground(theme.Current.Error).Render(
			fmt.Sprintf("  %s: the provider refused the sign-in; %s", r.Name, m.authFix(r.Name))))
		b.WriteString("\n")
	}

	if t := m.testResult; t != nil && t.Resp != nil {
		b.WriteString("\n")
		latency := ""
		if t.Resp.LatencyMs != nil {
			latency = fmt.Sprintf(" (%dms)", *t.Resp.LatencyMs)
		}
		if t.Resp.Success {
			b.WriteString(lipgloss.NewStyle().Foreground(theme.Current.Success).Render(fmt.Sprintf("  Test %s: PASS%s", t.Name, latency)))
		} else {
			errTxt := ""
			if t.Resp.Error != nil {
				errTxt = *t.Resp.Error
			}
			b.WriteString(lipgloss.NewStyle().Foreground(theme.Current.Error).Render(fmt.Sprintf("  Test %s: FAIL%s %s", t.Name, latency, errTxt)))
		}
		b.WriteString("\n")
	}

	if s := m.storageInfo; s != nil && s.Resp != nil {
		b.WriteString("\n")
		infoStyle := lipgloss.NewStyle().Foreground(theme.Current.Info)
		if !s.Resp.Supported {
			b.WriteString(infoStyle.Render(fmt.Sprintf("  Storage %s: this remote does not report usage", s.Name)))
		} else {
			used, total, free := "?", "?", "?"
			if s.Resp.UsedBytes != nil {
				used = formatBytes(*s.Resp.UsedBytes)
			}
			if s.Resp.TotalBytes != nil {
				total = formatBytes(*s.Resp.TotalBytes)
			}
			if s.Resp.FreeBytes != nil {
				free = formatBytes(*s.Resp.FreeBytes)
			}
			b.WriteString(infoStyle.Render(fmt.Sprintf("  Storage %s: used %s  total %s  free %s", s.Name, used, total, free)))
		}
		b.WriteString("\n")
	}

	return tea.NewView(b.String())
}

func (m *RemotesModel) updateTable() {
	var rows []components.Row
	for _, r := range m.remotes {
		typ := r.Type
		if r.AuthError {
			typ += " (refused)"
		}
		rows = append(rows, components.Row{
			Key:    r.Name,
			Values: []string{r.Name, typ, formatTimePtr(r.LastVerified, "-")},
		})
	}
	m.table.SetRows(rows)
}

func (m RemotesModel) fetchRemotes() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.ListRemotes(context.Background())
		return PollResultMsg{ViewID: ViewRemotes, Data: data, Err: err}
	}
}

func (m RemotesModel) fetchDeps(name string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		deps, err := client.RemoteDependencies(context.Background(), name)
		return ActionResultMsg{ViewID: ViewRemotes, Action: "remote_deps", Err: err, Data: remoteDeps{Name: name, Deps: deps}}
	}
}

func (m RemotesModel) testRemote(name string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.TestRemote(context.Background(), name)
		return ActionResultMsg{ViewID: ViewRemotes, Action: "test_remote", Err: err, Data: remoteTest{Name: name, Resp: data}}
	}
}

func (m RemotesModel) fetchStorageInfo(name string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.RemoteStorageInfo(context.Background(), name)
		return PollResultMsg{ViewID: ViewRemotes, Data: remoteInfo{Name: name, Resp: data}, Err: err}
	}
}

func (m RemotesModel) deleteRemote(name string, force bool) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		err := client.DeleteRemote(context.Background(), name, force)
		return ActionResultMsg{ViewID: ViewRemotes, Action: "delete_remote", Err: err}
	}
}

// --- Quick add ---

func (m RemotesModel) fetchProviders() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.ListProviders(context.Background())
		return ActionResultMsg{ViewID: ViewRemotes, Action: "quick_providers", Err: err, Data: data}
	}
}

// providerOption names a provider in the type dropdown.
func providerOption(p api.ProviderResponse) string {
	return fmt.Sprintf("%s (%s)", p.DisplayName, p.ID)
}

// handleProviders opens the first quick-add step with the providers that
// need no browser sign-in. OAuth providers stay with the wizard.
func (m RemotesModel) handleProviders(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	if m.mode != remotesModeLoadingProviders {
		return m, nil
	}
	if msg.Err != nil {
		m.mode = remotesModeList
		return m, errorFlash("Could not load the remote types", msg.Err)
	}
	all, _ := msg.Data.([]api.ProviderResponse)
	m.providers = nil
	var options []string
	for _, p := range all {
		if p.AuthType == api.AuthTypeKey {
			m.providers = append(m.providers, p)
			options = append(options, providerOption(p))
		}
	}
	if len(m.providers) == 0 {
		m.mode = remotesModeList
		return m, flash("Every remote type needs a browser sign-in: use the wizard (w)", true)
	}
	m.mode = remotesModeQuickAdd
	m.form = components.NewFormWithID(formQuickAdd, "Quick add remote", []components.Field{
		{Name: "name", Label: "Name", Type: components.FieldText, Required: true,
			Help: "How profiles refer to it, e.g. nas (then nas:Backup)"},
		{Name: "type", Label: "Type", Type: components.FieldDropdown, Options: options,
			Help: "Remotes that sign in with keys or a password. For Google Drive, Dropbox\nor OneDrive use the wizard (w)."},
	})
	return m, nil
}

func (m RemotesModel) handleFormSubmit(msg components.FormSubmitMsg) (tea.Model, tea.Cmd) {
	switch msg.FormID {
	case formQuickAdd:
		m.addName = strings.TrimSpace(msg.Values["name"])
		m.addProvider = nil
		for i := range m.providers {
			if providerOption(m.providers[i]) == msg.Values["type"] {
				p := m.providers[i]
				m.addProvider = &p
			}
		}
		if m.addProvider == nil {
			m.form.Reopen("Choose a type")
			return m, nil
		}
		if len(m.addProvider.Fields) == 0 {
			return m.createQuick(map[string]string{})
		}
		fields := make([]components.Field, 0, len(m.addProvider.Fields))
		for _, f := range m.addProvider.Fields {
			fields = append(fields, providerFormField(f, f.Default))
		}
		m.mode = remotesModeQuickParams
		m.form = components.NewFormWithID(formQuickParams,
			fmt.Sprintf("Quick add %s remote %q", m.addProvider.DisplayName, m.addName), fields)
		return m, nil
	case formEditRemote:
		return m.submitEdit(msg.Values)
	case formImportPath:
		return m.submitImportPath(msg.Values)
	case formImportSelect:
		return m.submitImportSelect(msg.Values)
	case formQuickParams:
		params := map[string]string{}
		for k, v := range msg.Values {
			if v = strings.TrimSpace(v); v != "" && v != selectUnset {
				params[k] = v
			}
		}
		return m.createQuick(params)
	}
	return m, nil
}

// createQuick sends POST /wizard/create, the same call the wizard makes for
// key-based providers.
func (m RemotesModel) createQuick(params map[string]string) (tea.Model, tea.Cmd) {
	m.mode = remotesModeCreating
	client := m.client
	req := api.CreateRemoteRequest{Name: m.addName, ProviderID: m.addProvider.ID, Params: params}
	return m, func() tea.Msg {
		err := client.CreateRemote(context.Background(), req)
		return ActionResultMsg{ViewID: ViewRemotes, Action: "quick_create", Err: err, Data: req.Name}
	}
}

// handleQuickCreated closes the form on success; on an error the last form
// comes back with the backend's message and the values kept.
func (m RemotesModel) handleQuickCreated(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	if m.mode != remotesModeCreating {
		return m, nil
	}
	name, _ := msg.Data.(string)
	if msg.Err != nil {
		if m.form.ID == formQuickParams {
			m.mode = remotesModeQuickParams
		} else {
			m.mode = remotesModeQuickAdd
		}
		m.form.Reopen(apiDetail(msg.Err))
		return m, errorFlash("Creating remote "+name+" failed", msg.Err)
	}
	m.mode = remotesModeList
	m.addProvider = nil
	return m, tea.Batch(m.fetchRemotes(), flash("Remote "+name+" created; press t to test it", false))
}

package ui

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// Editing, reconnecting and importing remotes from the Remotes view.

const (
	formEditRemote   = "edit_remote"
	formImportPath   = "import_path"
	formImportSelect = "import_select"
	// The edit form's dropdown that removes a stored secret.
	editClearField = "_clear"
	editClearNone  = "(keep all)"
	// The backend's limit for an imported rclone.conf
	// (schemas.MAX_IMPORT_CONFIG_LENGTH).
	maxImportSize = 512 * 1024
)

// editData is what the edit form is built from.
type editData struct {
	Name     string
	Config   *api.RemoteConfigResponse
	Provider *api.ProviderResponse
}

// importData is a previewed rclone.conf: its text (sent again on import)
// and the remotes in it.
type importData struct {
	Path    string
	Content string
	Preview *api.ImportPreviewResponse
}

func (m RemotesModel) selectedRemote() *api.RemoteResponse {
	row := m.table.SelectedRow()
	if row == nil {
		return nil
	}
	for i := range m.remotes {
		if m.remotes[i].Name == row.Key {
			return &m.remotes[i]
		}
	}
	return nil
}

// --- Reconnect ---

// reconnect opens the wizard's reconnect mode for the selected OAuth remote.
func (m RemotesModel) reconnect() (tea.Model, tea.Cmd) {
	r := m.selectedRemote()
	if r == nil {
		return m, nil
	}
	if !r.Reconnectable || r.ProviderID == nil {
		return m, flash(fmt.Sprintf("%s does not sign in with a browser; press e to edit its credentials", r.Name), true)
	}
	msg := ReconnectRemoteMsg{Name: r.Name, ProviderID: *r.ProviderID}
	return m, func() tea.Msg { return NavigateMsg{Target: ViewWizard, Payload: msg} }
}

// --- Edit ---

func (m RemotesModel) startEdit() (tea.Model, tea.Cmd) {
	r := m.selectedRemote()
	if r == nil {
		return m, nil
	}
	if !r.Editable {
		if r.Reconnectable {
			return m, flash(fmt.Sprintf("%s signs in with a browser: press a to reconnect it", r.Name), true)
		}
		return m, flash(fmt.Sprintf("Remotes of type %s cannot be edited here", r.Type), true)
	}
	m.mode = remotesModeLoadingEdit
	m.pendingName = r.Name
	client, name := m.client, r.Name
	return m, func() tea.Msg {
		cfg, err := client.RemoteConfig(context.Background(), name)
		if err != nil {
			return ActionResultMsg{ViewID: ViewRemotes, Action: "edit_loaded", Err: err, Data: editData{Name: name}}
		}
		providers, err := client.ListProviders(context.Background())
		data := editData{Name: name, Config: cfg}
		for i := range providers {
			if providers[i].ID == cfg.ProviderID {
				data.Provider = &providers[i]
			}
		}
		if err == nil && data.Provider == nil {
			err = fmt.Errorf("unknown provider %q", cfg.ProviderID)
		}
		return ActionResultMsg{ViewID: ViewRemotes, Action: "edit_loaded", Err: err, Data: data}
	}
}

func (m RemotesModel) handleEditLoaded(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	data, _ := msg.Data.(editData)
	if m.mode != remotesModeLoadingEdit || data.Name != m.pendingName {
		return m, nil
	}
	if msg.Err != nil {
		m.mode = remotesModeList
		m.pendingName = ""
		return m, errorFlash("Could not load the settings of "+data.Name, msg.Err)
	}
	m.edit = &data
	m.mode = remotesModeEdit
	m.form = components.NewFormWithID(formEditRemote, fmt.Sprintf("Edit %s remote %q", safeLine(data.Provider.DisplayName), data.Name),
		editFields(data))
	intro := "Secrets are not shown: leave them empty to keep the stored ones."
	if data.Provider.ID == "crypt" {
		intro += "\nFiles already stored can only be read with the passwords and encryption\n" +
			"settings they were written with: change them only if you entered them wrongly."
	}
	if len(data.Config.OtherKeys) > 0 {
		intro += "\nOther settings stay as they are: " + strings.Join(data.Config.OtherKeys, ", ")
	}
	m.form.Intro = intro
	return m, nil
}

// editFields builds the edit form: the provider's fields with the stored
// values (secrets empty), and a dropdown to remove one stored secret.
func editFields(data editData) []components.Field {
	stored := map[string]api.RemoteConfigField{}
	for _, f := range data.Config.Fields {
		stored[f.Name] = f
	}
	var fields []components.Field
	clearable := []string{editClearNone}
	for _, pf := range data.Provider.Fields {
		info := stored[pf.Name]
		f := providerFormField(pf, info.Value)
		if info.Secret && info.IsSet {
			f.Required = false
			f.Help = "Stored; leave empty to keep it. " + f.Help
			if !pf.Required {
				clearable = append(clearable, pf.Label)
			}
		}
		fields = append(fields, f)
	}
	if len(clearable) > 1 {
		fields = append(fields, components.Field{
			Name: editClearField, Label: "Remove secret", Type: components.FieldDropdown,
			Options: clearable, Value: editClearNone,
			Help: "E.g. the SFTP password, to sign in with a key instead.",
		})
	}
	return fields
}

// submitEdit sends the changed fields: non-secrets that differ from the
// stored value (empty removes them), secrets that were typed.
func (m RemotesModel) submitEdit(vals map[string]string) (tea.Model, tea.Cmd) {
	if m.edit == nil {
		return m, nil
	}
	stored := map[string]api.RemoteConfigField{}
	for _, f := range m.edit.Config.Fields {
		stored[f.Name] = f
	}
	req := api.UpdateRemoteRequest{Params: map[string]string{}, Clear: []string{}}
	clear := vals[editClearField]
	for _, pf := range m.edit.Provider.Fields {
		v := strings.TrimSpace(vals[pf.Name])
		if pf.FieldType == api.FieldTypeSelect && v == selectUnset {
			v = ""
		}
		info := stored[pf.Name]
		switch {
		case info.Secret && pf.Label == clear:
			req.Clear = append(req.Clear, pf.Name)
		case info.Secret:
			if v != "" {
				req.Params[pf.Name] = v
			}
		case v != info.Value:
			req.Params[pf.Name] = v
		}
	}
	m.mode = remotesModeSaving
	client, name := m.client, m.edit.Name
	return m, func() tea.Msg {
		err := client.UpdateRemote(context.Background(), name, req)
		return ActionResultMsg{ViewID: ViewRemotes, Action: "edit_saved", Err: err, Data: name}
	}
}

func (m RemotesModel) handleEditSaved(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	if m.mode != remotesModeSaving {
		return m, nil
	}
	name, _ := msg.Data.(string)
	if msg.Err != nil {
		m.mode = remotesModeEdit
		m.form.Reopen(apiDetail(msg.Err))
		return m, errorFlash("Saving "+name+" failed", msg.Err)
	}
	m.mode = remotesModeList
	m.edit = nil
	m.pendingName = ""
	return m, tea.Batch(m.fetchRemotes(), flash("Remote "+name+" updated; press t to test it", false))
}

// --- Import ---

// defaultRcloneConf is where rclone keeps its config on this machine.
func defaultRcloneConf() string {
	if dir, err := os.UserConfigDir(); err == nil {
		return filepath.Join(dir, "rclone", "rclone.conf")
	}
	return "~/.config/rclone/rclone.conf"
}

func (m RemotesModel) startImport() (tea.Model, tea.Cmd) {
	m.mode = remotesModeImportPath
	m.form = components.NewFormWithID(formImportPath, "Import an rclone.conf", []components.Field{{
		Name: "path", Label: "File", Type: components.FieldText, Value: defaultRcloneConf(), Required: true,
		Help: "An rclone.conf on this computer (where the TUI runs). Its text is sent to\n" +
			"the OmniSync backend, which shows the remotes in it before importing any.",
	}})
	return m, nil
}

// readImportFile reads the rclone.conf at path (~ expanded), refusing files
// above the backend's limit.
func readImportFile(path string) (string, error) {
	if strings.HasPrefix(path, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			path = filepath.Join(home, path[2:])
		}
	}
	info, err := os.Stat(path)
	if err != nil {
		return "", err
	}
	if info.IsDir() {
		return "", fmt.Errorf("%s is a folder", path)
	}
	if info.Size() > maxImportSize {
		return "", fmt.Errorf("%s is too large for an rclone.conf (at most 512 KB)", path)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func (m RemotesModel) submitImportPath(vals map[string]string) (tea.Model, tea.Cmd) {
	path := strings.TrimSpace(vals["path"])
	content, err := readImportFile(path)
	if err != nil {
		m.form.Reopen(err.Error())
		return m, nil
	}
	m.mode = remotesModeImportChecking
	client := m.client
	return m, func() tea.Msg {
		preview, err := client.PreviewImport(context.Background(), content)
		return ActionResultMsg{ViewID: ViewRemotes, Action: "import_preview", Err: err,
			Data: importData{Path: path, Content: content, Preview: preview}}
	}
}

// freeRemoteName is name, or name-imported(-N) if that is taken.
func freeRemoteName(name string, taken map[string]bool) string {
	if !taken[name] {
		return name
	}
	for i := 1; ; i++ {
		candidate := name + "-imported"
		if i > 1 {
			candidate = fmt.Sprintf("%s-imported-%d", name, i)
		}
		if !taken[candidate] {
			return candidate
		}
	}
}

func (m RemotesModel) handleImportPreview(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	if m.mode != remotesModeImportChecking {
		return m, nil
	}
	data, _ := msg.Data.(importData)
	if msg.Err != nil || data.Preview == nil {
		m.mode = remotesModeImportPath
		err := msg.Err
		if err == nil {
			err = fmt.Errorf("no answer")
		}
		m.form.Reopen(apiDetail(err))
		return m, nil
	}
	if len(data.Preview.Errors) > 0 {
		m.mode = remotesModeImportPath
		m.form.Reopen(strings.Join(data.Preview.Errors, " "))
		return m, nil
	}
	taken := map[string]bool{}
	for _, r := range m.remotes {
		taken[r.Name] = true
	}
	var fields []components.Field
	var refused []string
	for _, c := range data.Preview.Remotes {
		if len(c.Problems) > 0 {
			refused = append(refused, fmt.Sprintf("  %s (%s): %s", safeLine(c.Name), safeLine(c.Type), safeLine(strings.Join(c.Problems, "; "))))
			continue
		}
		value, help := c.Name, "Import under this name; empty skips it."
		if c.Exists {
			// A clash starts skipped; the suggested free name is in the help.
			value = ""
			help = fmt.Sprintf("A remote %q exists already: enter another name, e.g. %s, or leave empty to skip.",
				c.Name, freeRemoteName(c.Name, taken))
		} else {
			taken[c.Name] = true
		}
		fields = append(fields, components.Field{
			Name: c.Name, Label: fmt.Sprintf("%s (%s)", safeLine(c.Name), safeLine(c.Type)), Type: components.FieldText,
			Value: value, Help: help,
		})
	}
	if len(fields) == 0 {
		m.mode = remotesModeImportPath
		reason := "The file holds no remotes."
		if len(refused) > 0 {
			reason = "None of its remotes can be imported:\n" + strings.Join(refused, "\n")
		}
		m.form.Reopen(reason)
		return m, nil
	}
	m.importing = &data
	m.mode = remotesModeImportSelect
	m.form = components.NewFormWithID(formImportSelect, "Import remotes from "+data.Path, fields)
	intro := "Remotes in the file. Values (passwords, tokens) are copied as they are."
	if len(refused) > 0 {
		intro += "\nNot imported:\n" + strings.Join(refused, "\n")
	}
	m.form.Intro = intro
	return m, nil
}

func (m RemotesModel) submitImportSelect(vals map[string]string) (tea.Model, tea.Cmd) {
	if m.importing == nil {
		return m, nil
	}
	req := api.ImportRemotesRequest{Content: m.importing.Content}
	sources := make([]string, 0, len(vals))
	for source := range vals {
		sources = append(sources, source)
	}
	sort.Strings(sources)
	for _, source := range sources {
		name := strings.TrimSpace(vals[source])
		if name == "" {
			continue
		}
		sel := api.ImportRemoteSelection{Source: source}
		if name != source {
			n := name
			sel.Name = &n
		}
		req.Remotes = append(req.Remotes, sel)
	}
	if len(req.Remotes) == 0 {
		m.form.Reopen("Give at least one remote a name to import it")
		return m, nil
	}
	m.mode = remotesModeImporting
	client := m.client
	return m, func() tea.Msg {
		resp, err := client.ImportRemotes(context.Background(), req)
		return ActionResultMsg{ViewID: ViewRemotes, Action: "import_done", Err: err, Data: resp}
	}
}

func (m RemotesModel) handleImportDone(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	if m.mode != remotesModeImporting {
		return m, nil
	}
	if msg.Err != nil {
		m.mode = remotesModeImportSelect
		m.form.Reopen(apiDetail(msg.Err))
		return m, errorFlash("Import failed", msg.Err)
	}
	resp, _ := msg.Data.(*api.ImportRemotesResponse)
	m.mode = remotesModeList
	m.importing = nil
	n := 0
	if resp != nil {
		n = len(resp.Imported)
	}
	text := fmt.Sprintf("Imported %d remotes", n)
	if n == 1 {
		text = "Imported 1 remote"
	}
	return m, tea.Batch(m.fetchRemotes(), flash(text, false))
}

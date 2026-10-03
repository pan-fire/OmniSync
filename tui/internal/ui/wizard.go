package ui

import (
	"context"
	"fmt"
	"os/exec"
	"runtime"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

type wizardStep int

const (
	wizardStepSelectProvider wizardStep = iota
	wizardStepFillFields
	wizardStepAuthorizing
	wizardStepCreating
	wizardStepDone
)

const (
	formWizardFields = "wizard_fields"
	wizardNameField  = "_remote_name"
	// The OAuth providers' own fields for the user's OAuth app. The one
	// pair the user enters goes both to POST /wizard/authorize (consent URL
	// and token exchange) and, as params, into rclone.conf (token refresh).
	oauthClientIDField     = "client_id"
	oauthClientSecretField = "client_secret"
	// Where the full steps to create an own OAuth app are documented; each
	// provider has its own section (oauthDocsAnchors).
	oauthDocsURL = "https://github.com/pan-fire/OmniSync/blob/main/docs/gem/remotes.md"
	// The dropdown entry of an optional select field that leaves it unset.
	selectUnset = "(rclone default)"
)

// oauthDocsAnchors maps OAuth provider IDs to their section in oauthDocsURL.
var oauthDocsAnchors = map[string]string{
	"drive":    "google-drive",
	"dropbox":  "dropbox",
	"onedrive": "onedrive",
}

// BrowserOpener opens a URL in the user's browser. It is a variable so tests
// can replace it; LookBrowserOpener finds the default.
var BrowserOpener = LookBrowserOpener()

// BrowserCommands lists, in order of preference, the commands that open a
// URL in the default browser on goos; the URL is appended as the last
// argument. Windows has neither xdg-open nor open: rundll32 hands the URL to
// the registered handler without a shell, so the '&' in an OAuth URL is not
// read as a command separator; cmd's start is the fallback.
func BrowserCommands(goos string) [][]string {
	switch goos {
	case "windows":
		return [][]string{
			{"rundll32", "url.dll,FileProtocolHandler"},
			{"cmd", "/c", "start", ""},
		}
	case "darwin":
		return [][]string{{"open"}, {"xdg-open"}}
	}
	return [][]string{{"xdg-open"}}
}

// LookBrowserOpener returns a function that opens URLs with the first
// installed command of BrowserCommands, or nil if none is installed.
func LookBrowserOpener() func(string) error {
	for _, argv := range BrowserCommands(runtime.GOOS) {
		path, err := exec.LookPath(argv[0])
		if err != nil {
			continue
		}
		prefix := append([]string(nil), argv[1:]...)
		return func(url string) error {
			cmd := exec.Command(path, append(prefix, url)...)
			if err := cmd.Start(); err != nil {
				return err
			}
			go func() { _ = cmd.Wait() }()
			return nil
		}
	}
	return nil
}

// WizardModel is the multi-step remote setup wizard.
//
// Key-based providers (s3, b2, sftp, ftp, ...) collect their fields and
// create the remote directly. OAuth providers collect the remote name and
// the client ID (Google Drive: and secret) of the user's own OAuth app, then
// show the provider's consent URL. The form's intro says how to create that
// app and which redirect URI to register with it (GET
// /wizard/oauth/redirect-uri). The provider redirects the browser to the
// backend's callback, so the sign-in finishes by itself while the TUI polls
// the session; the remote is then created from the completed wizard
// session. The token never passes through the TUI.
//
// In reconnect mode (ReconnectRemoteMsg, from the Remotes view) the wizard
// signs an existing OAuth remote in again: the same sign-in, and then
// POST /wizard/reconnect replaces only the remote's token. There the client
// ID and secret may stay empty to keep the app stored in the remote.
type WizardModel struct {
	client    *api.Client
	providers []api.ProviderResponse
	table     components.Table
	form      components.Form
	step      wizardStep
	selected  *api.ProviderResponse
	// Captured from the form: the remote to create.
	remoteName string
	params     map[string]string
	sessionID  string
	authURL    string
	openErr    error
	// The redirect URI to register with the own OAuth app, from the
	// backend; redirectErr is set when asking for it failed.
	redirectURI string
	redirectErr error
	testResult  *api.TestRemoteResponse
	// reconnect names the existing remote being signed in again; empty
	// when a new remote is set up. reconnectProvider waits for the
	// providers to load.
	reconnect         string
	reconnectProvider string
	loading           bool
	err               error
	width             int
	height            int
}

// NewWizardModel creates a new wizard view.
func NewWizardModel(client *api.Client) WizardModel {
	cols := []components.Column{
		{Title: "Provider", Width: 24},
		{Title: "Setup", Width: 20},
	}
	return WizardModel{
		client:  client,
		table:   components.NewTable(cols, 15),
		loading: true,
	}
}

func (m WizardModel) ViewID() ViewID { return ViewWizard }

// CapturesInput is true on every step after choosing a provider.
func (m WizardModel) CapturesInput() bool {
	return m.step != wizardStepSelectProvider
}

func (m WizardModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "Up/Down, j/k", Desc: "Choose provider"},
		{Key: "Enter", Desc: "Select provider / submit"},
		{Key: "o", Desc: "(authorizing) Open the sign-in URL in the browser"},
		{Key: "Esc", Desc: "Back / cancel"},
	}
}

// PollSpec polls the OAuth session every 2 seconds while a sign-in is open.
func (m WizardModel) PollSpec() *PollSpec {
	return &PollSpec{
		FastInterval: FastPollInterval,
		SlowInterval: SlowTickInterval,
		IsActive: func(interface{}) bool {
			return m.step == wizardStepAuthorizing
		},
	}
}

func (m WizardModel) Init() tea.Cmd {
	if m.step == wizardStepSelectProvider || m.step == wizardStepDone {
		return m.fetchProviders()
	}
	return nil
}

func (m WizardModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.table.SetWidth(msg.Width)

	case ReconnectRemoteMsg:
		return m.startReconnect(msg)

	case PollResultMsg:
		return m.handlePollResult(msg)

	case ActionResultMsg:
		return m.handleActionResult(msg)

	case components.FormSubmitMsg:
		if m.step == wizardStepFillFields && msg.FormID == formWizardFields {
			return m.submitFields(msg.Values)
		}

	case components.FormCancelMsg:
		if m.step == wizardStepFillFields {
			if m.reconnect != "" {
				m.reset()
				return m, func() tea.Msg { return NavigateMsg{Target: ViewRemotes} }
			}
			m.step = wizardStepSelectProvider
			m.selected = nil
		}

	case TickMsg:
		if m.step == wizardStepAuthorizing && m.sessionID != "" {
			return m, m.pollSession()
		}

	case tea.PasteMsg:
		if m.step == wizardStepFillFields && m.form.Active {
			_, cmd := m.form.Update(msg)
			return m, cmd
		}

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m WizardModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	switch data := msg.Data.(type) {
	case []api.ProviderResponse:
		m.loading = false
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		m.providers = data
		m.updateProviderTable()
		if m.reconnectProvider != "" {
			return m.selectReconnectProvider()
		}
	case *api.WizardSessionResponse:
		if m.step != wizardStepAuthorizing {
			return m, nil
		}
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		if data == nil || data.SessionID != m.sessionID {
			return m, nil
		}
		m.err = nil
		switch data.Status {
		case api.WizardCompleted:
			m.step = wizardStepCreating
			return m, m.createRemote()
		case api.WizardFailed:
			errText := "authorization failed"
			if data.Error != nil {
				errText = *data.Error
			}
			m.err = fmt.Errorf("%s", errText)
			m.restart()
		}
	default:
		if msg.Err != nil {
			m.err = msg.Err
			m.loading = false
		}
	}
	return m, nil
}

func (m WizardModel) handleActionResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	switch msg.Action {
	case "authorize":
		if msg.Err != nil {
			// E.g. a missing client ID or secret (422): back to the form,
			// with what the user entered and the backend's message.
			m.step = wizardStepFillFields
			m.form.Reopen(msg.Err.Error())
			return m, nil
		}
		if resp, ok := msg.Data.(*api.AuthorizeResponse); ok && resp != nil {
			m.err = nil
			m.sessionID = resp.SessionID
			m.authURL = resp.AuthURL
			m.step = wizardStepAuthorizing
		}
	case "redirect_uri":
		if resp, ok := msg.Data.(*api.OAuthRedirectResponse); ok && msg.Err == nil && resp != nil && resp.RedirectURI != "" {
			m.redirectURI = resp.RedirectURI
			m.redirectErr = nil
		} else {
			m.redirectErr = msg.Err
			if m.redirectErr == nil {
				m.redirectErr = fmt.Errorf("the backend sent no redirect URI")
			}
		}
		if m.step == wizardStepFillFields && m.selected != nil {
			m.form.Intro = m.formIntro()
		}
	case "create_remote":
		if msg.Err != nil {
			m.err = msg.Err
			m.restart()
			if m.reconnect != "" {
				return m, errorFlash("Reconnecting the remote failed", msg.Err)
			}
			return m, errorFlash("Creating the remote failed", msg.Err)
		}
		m.step = wizardStepDone
		m.err = nil
		done := fmt.Sprintf("Remote %q created", m.remoteName)
		if m.reconnect != "" {
			done = fmt.Sprintf("Remote %q reconnected", m.remoteName)
		}
		return m, tea.Batch(flash(done, false), m.testRemote())
	case "test_remote":
		if resp, ok := msg.Data.(*api.TestRemoteResponse); ok && msg.Err == nil {
			m.testResult = resp
		} else if msg.Err != nil {
			errText := msg.Err.Error()
			m.testResult = &api.TestRemoteResponse{Success: false, Error: &errText}
		}
	case "cancel_session":
		if m.reconnect != "" {
			m.reset()
			return m, func() tea.Msg { return NavigateMsg{Target: ViewRemotes} }
		}
		m.step = wizardStepSelectProvider
		m.sessionID = ""
		m.authURL = ""
	case "open_url":
		m.openErr = msg.Err
	}
	return m, nil
}

func (m WizardModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch m.step {
	case wizardStepFillFields:
		_, cmd := m.form.Update(msg)
		return m, cmd

	case wizardStepAuthorizing:
		if msg.String() == "esc" {
			return m, m.cancelSession()
		}
		if msg.String() == "o" {
			return m, m.openURL()
		}
		return m, nil

	case wizardStepCreating:
		return m, nil

	case wizardStepDone:
		if msg.String() == "esc" || msg.String() == "enter" {
			m.reset()
			return m, func() tea.Msg { return NavigateMsg{Target: ViewRemotes} }
		}
		return m, nil
	}

	// Provider selection
	switch msg.String() {
	case "enter":
		if row := m.table.SelectedRow(); row != nil {
			for i := range m.providers {
				if m.providers[i].ID == row.Key {
					p := m.providers[i]
					m.selected = &p
					break
				}
			}
			if m.selected != nil {
				m.err = nil
				m.step = wizardStepFillFields
				return m, m.openFieldForm()
			}
		}
	case "esc":
		return m, func() tea.Msg { return NavigateMsg{Target: ViewRemotes} }
	default:
		m.table.Update(msg)
	}
	return m, nil
}

func (m *WizardModel) reset() {
	m.step = wizardStepSelectProvider
	m.selected = nil
	m.remoteName = ""
	m.params = nil
	m.sessionID = ""
	m.authURL = ""
	m.openErr = nil
	m.testResult = nil
	m.reconnect = ""
	m.reconnectProvider = ""
}

// restart goes back to where a failed sign-in starts again: the provider
// list, or in reconnect mode the remote's sign-in form (with the error).
func (m *WizardModel) restart() {
	m.sessionID = ""
	if m.reconnect != "" && m.selected != nil {
		m.step = wizardStepFillFields
		// The redirect URI is known by now (or its fallback is shown).
		_ = m.openFieldForm()
		if m.err != nil {
			m.form.Error = m.err.Error()
		}
		return
	}
	m.step = wizardStepSelectProvider
}

// startReconnect opens the sign-in form for an existing OAuth remote, as
// soon as the providers are known.
func (m WizardModel) startReconnect(msg ReconnectRemoteMsg) (tea.Model, tea.Cmd) {
	m.reset()
	m.err = nil
	m.reconnect = msg.Name
	m.reconnectProvider = msg.ProviderID
	if len(m.providers) == 0 {
		// Init (run when the view is shown) loads them.
		m.loading = true
		return m, nil
	}
	return m.selectReconnectProvider()
}

func (m WizardModel) selectReconnectProvider() (tea.Model, tea.Cmd) {
	id := m.reconnectProvider
	m.reconnectProvider = ""
	for i := range m.providers {
		if m.providers[i].ID == id && m.providers[i].AuthType == api.AuthTypeOAuth {
			p := m.providers[i]
			m.selected = &p
			m.step = wizardStepFillFields
			return m, m.openFieldForm()
		}
	}
	m.err = fmt.Errorf("remote %q does not sign in with OAuth and cannot be reconnected", m.reconnect)
	m.reconnect = ""
	return m, nil
}

// submitFields captures the form and either creates a key-based remote or
// starts the OAuth flow.
func (m WizardModel) submitFields(vals map[string]string) (tea.Model, tea.Cmd) {
	if m.selected == nil {
		return m, nil
	}
	m.remoteName = strings.TrimSpace(vals[wizardNameField])
	if m.reconnect != "" {
		m.remoteName = m.reconnect
	}
	m.params = map[string]string{}
	for _, f := range m.selected.Fields {
		v := vals[f.Name]
		if f.Name == oauthClientIDField || f.Name == oauthClientSecretField {
			v = strings.TrimSpace(v)
		}
		if f.FieldType == api.FieldTypeSelect && v == selectUnset {
			v = ""
		}
		if v != "" {
			m.params[f.Name] = v
		}
	}
	if m.selected.AuthType == api.AuthTypeOAuth {
		// The same pair as in m.params, so the token rclone stores was
		// issued to the client that rclone.conf names.
		req := api.AuthorizeRequest{ProviderID: m.selected.ID}
		if id, ok := m.params[oauthClientIDField]; ok {
			req.ClientID = &id
		}
		if secret, ok := m.params[oauthClientSecretField]; ok {
			req.ClientSecret = &secret
		}
		if m.reconnect != "" {
			name := m.reconnect
			req.RemoteName = &name
		}
		m.step = wizardStepCreating
		client := m.client
		return m, func() tea.Msg {
			resp, err := client.Authorize(context.Background(), req)
			return ActionResultMsg{ViewID: ViewWizard, Action: "authorize", Data: resp, Err: err}
		}
	}
	m.step = wizardStepCreating
	return m, m.createRemote()
}

func (m WizardModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder
	successStyle := lipgloss.NewStyle().Foreground(theme.Current.Success).Bold(true)

	switch m.step {
	case wizardStepFillFields:
		b.WriteString(m.form.View())
		return tea.NewView(b.String())

	case wizardStepCreating:
		b.WriteString(headerText("  Wizard " + theme.Glyphs().EmDash + " Working"))
		b.WriteString("\n\n  Talking to the backend...")
		return tea.NewView(b.String())

	case wizardStepAuthorizing:
		b.WriteString(headerText("  Wizard " + theme.Glyphs().EmDash + " Sign in"))
		b.WriteString("\n\n")
		b.WriteString("  Open this URL in a browser and allow access:\n\n")
		b.WriteString("  " + m.authURL + "\n\n")
		if BrowserOpener != nil {
			b.WriteString(mutedText("  o: open it in the default browser"))
			b.WriteString("\n")
		}
		if m.openErr != nil {
			b.WriteString(errorLine(fmt.Errorf("could not open the browser: %w", m.openErr)))
		}
		if m.err != nil {
			b.WriteString(errorLine(m.err))
		}
		b.WriteString(mutedText("  Waiting for the sign-in to finish (checked every 2 seconds). Esc: cancel"))
		return tea.NewView(b.String())

	case wizardStepDone:
		b.WriteString(headerText("  Wizard " + theme.Glyphs().EmDash + " Complete"))
		b.WriteString("\n\n")
		done := fmt.Sprintf("  Remote %q created.", m.remoteName)
		if m.reconnect != "" {
			done = fmt.Sprintf("  Remote %q reconnected.", m.remoteName)
		}
		b.WriteString(successStyle.Render(done))
		b.WriteString("\n")
		switch {
		case m.testResult == nil:
			b.WriteString("  Testing the connection...\n")
		case m.testResult.Success:
			b.WriteString(successStyle.Render("  Connection test passed."))
			b.WriteString("\n")
		default:
			errText := "unknown error"
			if m.testResult.Error != nil {
				errText = *m.testResult.Error
			}
			b.WriteString(errorLine(fmt.Errorf("connection test failed: %s", errText)))
		}
		b.WriteString("\n")
		b.WriteString(mutedText("  Press Enter or Esc to return to Remotes."))
		return tea.NewView(b.String())
	}

	b.WriteString(headerText("  Wizard " + theme.Glyphs().EmDash + " Select Provider"))
	b.WriteString("\n")

	if m.err != nil {
		b.WriteString(errorLine(m.err))
	}

	if m.loading {
		b.WriteString("  Loading providers...")
		return tea.NewView(b.String())
	}

	b.WriteString(m.table.View())
	if row := m.table.SelectedRow(); row != nil {
		for _, p := range m.providers {
			if p.ID == row.Key && p.SetupGuide != "" {
				b.WriteString("\n" + mutedText("  "+p.SetupGuide) + "\n")
			}
		}
	}
	b.WriteString("\n")
	b.WriteString(mutedText("  Enter:select  Esc:back"))

	return tea.NewView(b.String())
}

// openFieldForm builds the field form of the selected provider. For an
// OAuth provider it returns the command that asks the backend for the
// redirect URI to register, unless that is known already.
func (m *WizardModel) openFieldForm() tea.Cmd {
	m.buildFieldForm()
	if m.selected == nil || m.selected.AuthType != api.AuthTypeOAuth || m.redirectURI != "" {
		return nil
	}
	m.redirectErr = nil
	m.form.Intro = m.formIntro()
	client := m.client
	return func() tea.Msg {
		resp, err := client.OAuthRedirectURI(context.Background())
		return ActionResultMsg{ViewID: ViewWizard, Action: "redirect_uri", Data: resp, Err: err}
	}
}

func (m *WizardModel) buildFieldForm() {
	if m.selected == nil {
		return
	}
	var fields []components.Field
	if m.reconnect == "" {
		fields = append(fields, components.Field{
			Name: wizardNameField, Label: "Remote name", Type: components.FieldText,
			Value: m.selected.DefaultName, Required: true,
			Help: "The name profiles use, as in <name>:path",
		})
	}
	for _, pf := range m.selected.Fields {
		f := providerFormField(pf, pf.Default)
		if m.reconnect != "" && (pf.Name == oauthClientIDField || pf.Name == oauthClientSecretField) {
			// Empty keeps the app stored in the remote.
			f.Required = false
			f.Help = strings.TrimSpace("Leave empty to keep the remote's current app. " + f.Help)
		}
		fields = append(fields, f)
	}
	title := fmt.Sprintf("Set up %s", m.selected.DisplayName)
	if m.selected.AuthType == api.AuthTypeOAuth {
		// The own app's credentials are the provider's own client_id and
		// client_secret fields above; there is no second pair.
		title += " (sign-in in the browser follows)"
	}
	if m.reconnect != "" {
		title = fmt.Sprintf("Reconnect %q (%s)", m.reconnect, m.selected.DisplayName)
	}
	m.form = components.NewFormWithID(formWizardFields, title, fields)
	m.form.Intro = m.formIntro()
}

// formIntro is the text above the field form: for OAuth providers how to
// create the own app, the redirect URI to register and where the full steps
// are; in reconnect mode first what a reconnect does.
func (m WizardModel) formIntro() string {
	if m.selected == nil {
		return ""
	}
	var parts []string
	if m.reconnect != "" {
		parts = append(parts, "Sign in again to renew the access of this remote. Only its sign-in is\n"+
			"replaced; its settings and the profiles that use it stay as they are.\n"+
			"Leave the client ID and secret empty to keep the app stored in the remote.\n"+
			"A remote made with rclone's own app stores none: OmniSync no longer uses\n"+
			"rclone's app, so enter the client ID (and secret) of your own app.")
	} else if m.selected.SetupGuide != "" {
		parts = append(parts, m.selected.SetupGuide)
	}
	if m.selected.AuthType == api.AuthTypeOAuth {
		parts = append(parts, m.oauthAppHint())
	}
	if m.reconnect != "" {
		parts = append(parts, "Press Enter to continue.")
	}
	return strings.Join(parts, "\n\n")
}

// oauthAppHint names the redirect URI to register with the own OAuth app
// and the docs section with the full steps.
func (m WizardModel) oauthAppHint() string {
	const label = "Redirect URI to register with your app: "
	var redirect string
	switch {
	case m.redirectURI != "":
		redirect = label + m.redirectURI
	case m.redirectErr != nil:
		redirect = label + m.client.BaseURL() + "/wizard/oauth/callback\n" +
			"(assumed; the backend could not be asked: " + m.redirectErr.Error() + ")"
	default:
		redirect = label + "(asking the backend...)"
	}
	return redirect + "\nFull steps: " + oauthDocsLink(m.selected)
}

// oauthDocsLink is the docs section that explains how to create the own app
// for provider p.
func oauthDocsLink(p *api.ProviderResponse) string {
	anchor, ok := oauthDocsAnchors[p.ID]
	if !ok {
		anchor = strings.ToLower(strings.ReplaceAll(strings.TrimSpace(p.DisplayName), " ", "-"))
	}
	if anchor == "" {
		return oauthDocsURL
	}
	return oauthDocsURL + "#" + anchor
}

func (m *WizardModel) updateProviderTable() {
	var rows []components.Row
	for _, p := range m.providers {
		setup := "keys / password"
		if p.AuthType == api.AuthTypeOAuth {
			setup = "browser sign-in"
		}
		rows = append(rows, components.Row{
			Key:    p.ID,
			Values: []string{p.DisplayName, setup},
		})
	}
	m.table.SetRows(rows)
}

func (m WizardModel) fetchProviders() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.ListProviders(context.Background())
		return PollResultMsg{ViewID: ViewWizard, Data: data, Err: err}
	}
}

func (m WizardModel) pollSession() tea.Cmd {
	client, sessionID := m.client, m.sessionID
	return func() tea.Msg {
		data, err := client.WizardSession(context.Background(), sessionID)
		return PollResultMsg{ViewID: ViewWizard, Data: data, Err: err}
	}
}

func (m WizardModel) cancelSession() tea.Cmd {
	client, sessionID := m.client, m.sessionID
	return func() tea.Msg {
		err := client.CancelSession(context.Background(), sessionID)
		return ActionResultMsg{ViewID: ViewWizard, Action: "cancel_session", Err: err}
	}
}

func (m WizardModel) openURL() tea.Cmd {
	opener, url := BrowserOpener, m.authURL
	if opener == nil || url == "" {
		return nil
	}
	return func() tea.Msg {
		return ActionResultMsg{ViewID: ViewWizard, Action: "open_url", Err: opener(url)}
	}
}

// providerFormField is the form field for one provider setting: a text,
// password or dropdown field, starting at value.
func providerFormField(pf api.ProviderField, value string) components.Field {
	f := components.Field{
		Name:     pf.Name,
		Label:    pf.Label,
		Type:     components.FieldText,
		Value:    value,
		Required: pf.Required,
		Help:     pf.HelpText,
	}
	switch pf.FieldType {
	case api.FieldTypePassword:
		f.Type = components.FieldPassword
	case api.FieldTypeSelect:
		f.Type = components.FieldDropdown
		f.Options = append([]string(nil), pf.Options...)
		if !pf.Required {
			f.Options = append([]string{selectUnset}, f.Options...)
			if value == "" {
				f.Value = selectUnset
			}
		}
	case api.FieldTypeRemotePath:
		f.Help = strings.TrimSpace(f.Help + " Format: <remote>:<folder>, e.g. gdrive:Encrypted.")
	}
	return f
}

// createRemote sends POST /wizard/create with the captured name and params;
// for OAuth providers it names the completed session instead of a token.
// In reconnect mode it sends POST /wizard/reconnect instead, which stores
// the session's token in the existing remote.
func (m WizardModel) createRemote() tea.Cmd {
	if m.selected == nil {
		return nil
	}
	if m.reconnect != "" {
		client, name, sid := m.client, m.reconnect, m.sessionID
		return func() tea.Msg {
			err := client.ReconnectRemote(context.Background(), name, sid)
			return ActionResultMsg{ViewID: ViewWizard, Action: "create_remote", Err: err}
		}
	}
	req := api.CreateRemoteRequest{
		Name:       m.remoteName,
		ProviderID: m.selected.ID,
		Params:     m.params,
	}
	if m.selected.AuthType == api.AuthTypeOAuth && m.sessionID != "" {
		sid := m.sessionID
		req.SessionID = &sid
	}
	client := m.client
	return func() tea.Msg {
		err := client.CreateRemote(context.Background(), req)
		return ActionResultMsg{ViewID: ViewWizard, Action: "create_remote", Err: err}
	}
}

func (m WizardModel) testRemote() tea.Cmd {
	client, name := m.client, m.remoteName
	return func() tea.Msg {
		resp, err := client.TestWizardRemote(context.Background(), name)
		return ActionResultMsg{ViewID: ViewWizard, Action: "test_remote", Err: err, Data: resp}
	}
}

package ui

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

type configMode int

const (
	configModeView configMode = iota
	configModeEdit
	configModeTestSync
)

const (
	formEditConfig = "edit_config"
	formTestSync   = "test_sync"
)

var logLevels = []string{"DEBUG", "INFO", "WARNING", "ERROR"}

// The backend's default and largest history_days (GlobalConfigUpdateRequest).
const (
	defaultHistoryDays = 90
	maxHistoryDays     = 3650
)

// ConfigModel is the Config view.
type ConfigModel struct {
	client   *api.Client
	config   *api.GlobalConfigResponse
	form     components.Form
	testForm components.Form
	mode     configMode
	testing  bool
	loading  bool
	err      error
	width    int
	height   int
}

// NewConfigModel creates a new config view.
func NewConfigModel(client *api.Client) ConfigModel {
	return ConfigModel{
		client:  client,
		loading: true,
	}
}

func (m ConfigModel) ViewID() ViewID { return ViewConfig }

// CapturesInput is true while a form is open.
func (m ConfigModel) CapturesInput() bool { return m.mode != configModeView }

// KeyHints names the keys of the current mode for the bottom bar.
func (m ConfigModel) KeyHints() string {
	if m.mode != configModeView {
		return formHints
	}
	return "e:edit  t:test sync  r:refresh"
}

func (m ConfigModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "e", Desc: "Edit config"},
		{Key: "t", Desc: "Test sync between two directories"},
		{Key: "r", Desc: "Refresh"},
	}
}

func (m ConfigModel) Init() tea.Cmd {
	return m.fetchConfig()
}

func (m ConfigModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height

	case PollResultMsg:
		return m.handlePollResult(msg)

	case ActionResultMsg:
		return m.handleActionResult(msg)

	case components.FormSubmitMsg:
		return m.handleFormSubmit(msg)

	case components.FormCancelMsg:
		m.mode = configModeView
		return m, nil

	case tea.PasteMsg:
		switch m.mode {
		case configModeEdit:
			_, cmd := m.form.Update(msg)
			return m, cmd
		case configModeTestSync:
			_, cmd := m.testForm.Update(msg)
			return m, cmd
		}

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m ConfigModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	m.loading = false
	if msg.Err != nil {
		m.err = msg.Err
		return m, nil
	}
	if cfg, ok := msg.Data.(*api.GlobalConfigResponse); ok {
		m.err = nil
		m.config = cfg
	}
	return m, nil
}

func (m ConfigModel) handleActionResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	if msg.Action == "test_sync" {
		m.testing = false
	}
	if msg.Err != nil {
		return m, errorFlash("Error", msg.Err)
	}
	switch msg.Action {
	case "update_config":
		return m, tea.Batch(m.fetchConfig(), flash("Config updated", false))
	case "test_sync":
		if result, ok := msg.Data.(*api.TestSyncResponse); ok && result != nil {
			if result.Success {
				return m, flash("Sync test passed", false)
			}
			errTxt := "unknown error"
			if result.Error != nil {
				errTxt = *result.Error
			}
			return m, flash("Sync test failed: "+errTxt, true)
		}
	}
	return m, nil
}

func (m ConfigModel) handleFormSubmit(msg components.FormSubmitMsg) (tea.Model, tea.Cmd) {
	m.mode = configModeView
	switch msg.FormID {
	case formEditConfig:
		days, err := strconv.Atoi(strings.TrimSpace(msg.Values["history_days"]))
		if err != nil || days < 0 || days > maxHistoryDays {
			return m, flash(fmt.Sprintf("Keep history (days) must be a whole number from 0 to %d", maxHistoryDays), true)
		}
		return m, m.updateConfig(msg.Values["log_level"], days)
	case formTestSync:
		m.testing = true
		return m, tea.Batch(flash("Running sync test...", false),
			m.testSync(strings.TrimSpace(msg.Values["local_dir"]), strings.TrimSpace(msg.Values["remote_dir"])))
	}
	return m, nil
}

func (m ConfigModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch m.mode {
	case configModeEdit:
		_, cmd := m.form.Update(msg)
		return m, cmd
	case configModeTestSync:
		_, cmd := m.testForm.Update(msg)
		return m, cmd
	}

	switch msg.String() {
	case "e":
		logLevel := "INFO"
		historyDays := defaultHistoryDays
		if m.config != nil {
			if m.config.LogLevel != "" {
				logLevel = strings.ToUpper(m.config.LogLevel)
			}
			historyDays = m.config.HistoryDays
		}
		m.mode = configModeEdit
		m.form = components.NewFormWithID(formEditConfig, "Edit Configuration", []components.Field{
			{Name: "log_level", Label: "Log Level", Type: components.FieldDropdown, Value: logLevel, Options: logLevels},
			{Name: "history_days", Label: "Keep history (days)", Type: components.FieldText, Required: true,
				Value: strconv.Itoa(historyDays),
				Help:  "Older sync and backup jobs are removed daily; the newest 100 per profile stay. 0 keeps all."},
		})
		return m, nil
	case "t":
		m.mode = configModeTestSync
		m.testForm = components.NewFormWithID(formTestSync, "Test Sync Connection", []components.Field{
			{Name: "local_dir", Label: "Local Directory", Type: components.FieldText, Required: true},
			{Name: "remote_dir", Label: "Remote Directory", Type: components.FieldText, Required: true,
				Help: "<remote>:<path>, e.g. gdrive:Test"},
		})
		return m, nil
	case "r":
		m.loading = true
		return m, m.fetchConfig()
	}
	return m, nil
}

func (m ConfigModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder

	switch m.mode {
	case configModeEdit:
		b.WriteString(m.form.View())
		return tea.NewView(b.String())
	case configModeTestSync:
		b.WriteString(m.testForm.View())
		return tea.NewView(b.String())
	}

	b.WriteString(headerText("  Configuration"))
	b.WriteString("\n\n")

	if m.err != nil {
		b.WriteString(errorLine(m.err))
	}

	if m.loading && m.config == nil && m.err == nil {
		b.WriteString("  Loading...")
		return tea.NewView(b.String())
	}

	if m.config != nil {
		valueStyle := lipgloss.NewStyle().Foreground(theme.Current.Foreground)
		b.WriteString(fmt.Sprintf("  %s  %s\n", mutedText("Log Level:"), valueStyle.Render(m.config.LogLevel)))
		history := fmt.Sprintf("%d days", m.config.HistoryDays)
		if m.config.HistoryDays == 0 {
			history = "forever"
		}
		b.WriteString(fmt.Sprintf("  %s  %s\n", mutedText("Keep History:"), valueStyle.Render(history)))
	}
	if m.testing {
		b.WriteString("\n  Running sync test...\n")
	}

	return tea.NewView(b.String())
}

func (m ConfigModel) fetchConfig() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.GetConfig(context.Background())
		return PollResultMsg{ViewID: ViewConfig, Data: data, Err: err}
	}
}

func (m ConfigModel) updateConfig(logLevel string, historyDays int) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		_, err := client.UpdateConfig(context.Background(), api.GlobalConfigUpdateRequest{
			LogLevel: &logLevel, HistoryDays: &historyDays,
		})
		return ActionResultMsg{ViewID: ViewConfig, Action: "update_config", Err: err}
	}
}

func (m ConfigModel) testSync(localDir, remoteDir string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.TestSync(context.Background(), api.TestSyncRequest{LocalDir: localDir, RemoteDir: remoteDir})
		return ActionResultMsg{ViewID: ViewConfig, Action: "test_sync", Data: data, Err: err}
	}
}

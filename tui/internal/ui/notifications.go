package ui

import (
	"context"
	"fmt"
	"sort"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// NotificationsModel is the Notifications view.
type NotificationsModel struct {
	client       *api.Client
	config       *api.NotificationConfigResponse
	channels     *api.ChannelStatusResponse
	history      *api.NotificationHistoryResponse
	channelTable components.Table
	table        components.Table
	tabs         components.Tabs
	form         components.Form
	formChannel  string // the channel whose settings form is open, or ""
	loading      bool
	err          error
	width        int
	height       int
}

// NewNotificationsModel creates a new notifications view.
func NewNotificationsModel(client *api.Client) NotificationsModel {
	channelCols := []components.Column{
		{Title: "Name", Width: 20},
		{Title: "Enabled", Width: 10},
		{Title: "Available", Width: 12},
		{Title: "Severity", Width: 10},
	}
	cols := []components.Column{
		{Title: "Time", Width: 20},
		{Title: "Severity", Width: 10},
		{Title: "Title", Width: 30},
		{Title: "Channels", Width: 20},
	}
	return NotificationsModel{
		client:       client,
		channelTable: components.NewTable(channelCols, 15),
		table:        components.NewTable(cols, 15),
		tabs:         components.NewTabs("Channels", "History"),
		loading:      true,
	}
}

func (m NotificationsModel) ViewID() ViewID { return ViewNotifications }

// CapturesInput is true while a channel's settings form is open.
func (m NotificationsModel) CapturesInput() bool { return m.formChannel != "" }

// KeyHints names the keys for the bottom bar.
func (m NotificationsModel) KeyHints() string {
	if m.formChannel != "" {
		return formHints
	}
	return "e:toggle  s:severity  c:configure  t:test channel  T:test all  r:refresh  " + theme.Glyphs().LeftRight + ":tab"
}

func (m NotificationsModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "Left/Right", Desc: "Switch tab"},
		{Key: "Up/Down, j/k", Desc: "Move"},
		{Key: "n/N", Desc: "Next/previous page"},
		{Key: "e / Enter", Desc: "Toggle channel"},
		{Key: "s", Desc: "Cycle severity"},
		{Key: "c", Desc: "Configure webhook, ntfy or email"},
		{Key: "t", Desc: "Test the selected channel"},
		{Key: "T", Desc: "Test all enabled channels"},
		{Key: "r", Desc: "Refresh"},
	}
}

func (m NotificationsModel) Init() tea.Cmd {
	return tea.Batch(m.fetchChannelStatus(), m.fetchHistory(), m.fetchConfig())
}

func (m NotificationsModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.channelTable.SetWidth(msg.Width)
		m.table.SetWidth(msg.Width)

	case PollResultMsg:
		return m.handlePollResult(msg)

	case ActionResultMsg:
		return m.handleActionResult(msg)

	case TickMsg:
		return m, tea.Batch(m.fetchChannelStatus(), m.fetchHistory())

	case components.FormSubmitMsg:
		if msg.FormID == formChannelSettings {
			return m.submitChannelSettings(msg.Values)
		}

	case components.FormCancelMsg:
		if msg.FormID == formChannelSettings {
			m.formChannel = ""
		}

	case tea.PasteMsg:
		if m.formChannel != "" {
			_, cmd := m.form.Update(msg)
			return m, cmd
		}

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m NotificationsModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	m.loading = false
	if msg.Err != nil {
		m.err = msg.Err
		return m, nil
	}
	m.err = nil
	switch data := msg.Data.(type) {
	case *api.ChannelStatusResponse:
		m.channels = data
		m.updateChannelsTable()
	case *api.NotificationHistoryResponse:
		m.history = data
		m.updateHistoryTable()
	case *api.NotificationConfigResponse:
		m.config = data
		m.updateChannelsTable()
	}
	return m, nil
}

func (m NotificationsModel) handleActionResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	if msg.Action == "save_channel_settings" {
		if msg.Err != nil {
			// Keep what was typed; show why the backend refused it.
			m.form.Reopen(msg.Err.Error())
			return m, nil
		}
		m.formChannel = ""
		if cfg, ok := msg.Data.(*api.NotificationConfigResponse); ok && cfg != nil {
			m.config = cfg
			m.updateChannelsTable()
		}
		return m, tea.Batch(m.fetchChannelStatus(), flash("Channel settings saved", false))
	}
	if msg.Err != nil {
		return m, func() tea.Msg {
			return FlashMsg{Text: fmt.Sprintf("Error: %s", msg.Err.Error()), IsError: true}
		}
	}
	if msg.Action == "test_notification" {
		if result, ok := msg.Data.(*api.TestNotificationResponse); ok {
			text := fmt.Sprintf("Delivered: %s", strings.Join(result.ChannelsDelivered, ", "))
			if len(result.Errors) > 0 {
				var failed []string
				for ch := range result.Errors {
					failed = append(failed, ch)
				}
				text += fmt.Sprintf(" | Failed: %s", strings.Join(failed, ", "))
			}
			return m, func() tea.Msg { return FlashMsg{Text: text} }
		}
	}
	if msg.Action == "update_notification_config" {
		if cfg, ok := msg.Data.(*api.NotificationConfigResponse); ok {
			m.config = cfg
			m.updateChannelsTable()
			return m, func() tea.Msg { return FlashMsg{Text: "Notification settings updated"} }
		}
	}
	return m, nil
}

func (m NotificationsModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.formChannel != "" {
		_, cmd := m.form.Update(msg)
		return m, cmd
	}
	switch msg.String() {
	case "c":
		if row := m.channelTable.SelectedRow(); m.tabs.Active() == "Channels" && row != nil {
			if !configurableChannel(row.Key) {
				return m, flash(row.Key+" has no settings to configure", false)
			}
			m.formChannel = row.Key
			m.form = channelSettingsForm(row.Key, m.currentChannelConfig(row.Key))
			return m, nil
		}
	case "t":
		if row := m.channelTable.SelectedRow(); m.tabs.Active() == "Channels" && row != nil {
			return m, m.testChannel(row.Key)
		}
	case "right":
		m.tabs.Next()
		return m, nil
	case "left":
		m.tabs.Prev()
		return m, nil
	case "T":
		return m, m.testNotification()
	case "e", "enter":
		if m.tabs.Active() == "Channels" {
			return m, m.toggleSelectedChannel()
		}
	case "s":
		if m.tabs.Active() == "Channels" {
			return m, m.cycleSelectedChannelSeverity()
		}
	case "r":
		m.loading = true
		return m, tea.Batch(m.fetchChannelStatus(), m.fetchHistory(), m.fetchConfig())
	}

	if m.tabs.Active() == "Channels" {
		m.channelTable.Update(msg)
	} else {
		m.table.Update(msg)
	}
	return m, nil
}

func (m NotificationsModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder
	if m.formChannel != "" {
		b.WriteString(m.form.View())
		return tea.NewView(b.String())
	}
	headerStyle := lipgloss.NewStyle().Bold(true).Foreground(theme.Current.Primary)

	b.WriteString(headerStyle.Render("  Notifications"))
	b.WriteString("\n")
	b.WriteString(m.tabs.View())
	b.WriteString("\n\n")

	if m.err != nil {
		errStyle := lipgloss.NewStyle().Foreground(theme.Current.Error)
		b.WriteString(errStyle.Render(fmt.Sprintf("  Error: %s", safeLine(m.err.Error()))))
		b.WriteString("\n\n")
	}

	if m.loading && m.channels == nil {
		b.WriteString("  Loading...")
		return tea.NewView(b.String())
	}

	if m.tabs.Active() == "Channels" {
		b.WriteString(m.renderChannels())
	} else {
		b.WriteString(m.table.View())
	}

	return tea.NewView(b.String())
}

func (m NotificationsModel) renderChannels() string {
	if m.channels == nil && m.config == nil {
		return "  No channel data"
	}
	var b strings.Builder
	b.WriteString(m.channelTable.View())
	if len(m.channelTable.Rows) == 0 {
		b.WriteString("  No channels configured")
	} else if row := m.channelTable.SelectedRow(); row != nil {
		muted := lipgloss.NewStyle().Foreground(theme.Current.Muted)
		b.WriteString("\n")
		hint := "  Selected: %s  e:toggle  s:cycle severity  t:test  T:test all"
		if configurableChannel(row.Key) {
			hint = "  Selected: %s  e:toggle  s:cycle severity  c:configure  t:test  T:test all"
		}
		b.WriteString(muted.Render(fmt.Sprintf(hint, row.Key)))
		if configurableChannel(row.Key) {
			b.WriteString("\n")
			b.WriteString(muted.Render("  " + channelSettingsSummary(row.Key, m.currentChannelConfig(row.Key))))
		}
		if info, ok := m.channelsOrEmpty()[row.Key]; ok && !info.Available && len(info.MissingDependencies) > 0 {
			b.WriteString("\n")
			b.WriteString(muted.Render("  Missing: " + strings.Join(info.MissingDependencies, ", ")))
		}
	}
	return b.String()
}

func (m NotificationsModel) submitChannelSettings(values map[string]string) (tea.Model, tea.Cmd) {
	name := m.formChannel
	upd, err := channelSettingsUpdate(name, m.currentChannelConfig(name), values)
	if err != nil {
		m.form.Reopen(err.Error())
		return m, nil
	}
	client := m.client
	req := api.NotificationConfigUpdateRequest{Channels: map[string]api.ChannelConfigUpdate{name: upd}}
	return m, func() tea.Msg {
		data, err := client.UpdateNotificationConfig(context.Background(), req)
		return ActionResultMsg{ViewID: ViewNotifications, Action: "save_channel_settings", Data: data, Err: err}
	}
}

func (m NotificationsModel) testChannel(name string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.TestNotificationChannel(context.Background(), name)
		return ActionResultMsg{ViewID: ViewNotifications, Action: "test_notification", Data: data, Err: err}
	}
}

func (m *NotificationsModel) updateChannelsTable() {
	channelMap := make(map[string]struct{})
	for name := range m.channelsOrEmpty() {
		channelMap[name] = struct{}{}
	}
	for name := range m.configOrEmpty() {
		channelMap[name] = struct{}{}
	}

	names := make([]string, 0, len(channelMap))
	for name := range channelMap {
		names = append(names, name)
	}
	sort.Strings(names)

	var rows []components.Row
	for _, name := range names {
		cfg := m.currentChannelConfig(name)
		available := "unavailable"
		if info, ok := m.channelsOrEmpty()[name]; ok && info.Available {
			available = "available"
		}
		enabled := "disabled"
		if cfg.Enabled {
			enabled = "enabled"
		}
		rows = append(rows, components.Row{
			Key:    name,
			Values: []string{name, enabled, available, string(cfg.MinSeverity)},
		})
	}
	m.channelTable.SetRows(rows)
}

func (m NotificationsModel) channelsOrEmpty() map[string]api.ChannelStatusInfo {
	if m.channels == nil {
		return map[string]api.ChannelStatusInfo{}
	}
	return m.channels.Channels
}

func (m NotificationsModel) configOrEmpty() map[string]api.ChannelConfig {
	if m.config == nil {
		return map[string]api.ChannelConfig{}
	}
	return m.config.Channels
}

func (m NotificationsModel) currentChannelConfig(name string) api.ChannelConfig {
	if m.config != nil {
		if cfg, ok := m.config.Channels[name]; ok {
			return cfg
		}
	}
	return api.ChannelConfig{Enabled: true, MinSeverity: api.SeverityWarning}
}

func nextNotificationSeverity(current api.NotificationSeverity) api.NotificationSeverity {
	levels := []api.NotificationSeverity{api.SeverityDebug, api.SeverityInfo, api.SeverityWarning, api.SeverityError}
	for idx, level := range levels {
		if level == current {
			return levels[(idx+1)%len(levels)]
		}
	}
	return api.SeverityWarning
}

func (m NotificationsModel) toggleSelectedChannel() tea.Cmd {
	row := m.channelTable.SelectedRow()
	if row == nil {
		return nil
	}
	enabled := !m.currentChannelConfig(row.Key).Enabled
	return m.updateChannelConfig(row.Key, api.ChannelConfigUpdate{Enabled: &enabled})
}

func (m NotificationsModel) cycleSelectedChannelSeverity() tea.Cmd {
	row := m.channelTable.SelectedRow()
	if row == nil {
		return nil
	}
	severity := nextNotificationSeverity(m.currentChannelConfig(row.Key).MinSeverity)
	return m.updateChannelConfig(row.Key, api.ChannelConfigUpdate{MinSeverity: &severity})
}

// updateChannelConfig sends a partial update of one channel.
func (m NotificationsModel) updateChannelConfig(name string, upd api.ChannelConfigUpdate) tea.Cmd {
	client := m.client
	channels := map[string]api.ChannelConfigUpdate{name: upd}

	return func() tea.Msg {
		data, err := client.UpdateNotificationConfig(context.Background(), api.NotificationConfigUpdateRequest{Channels: channels})
		return ActionResultMsg{ViewID: ViewNotifications, Action: "update_notification_config", Data: data, Err: err}
	}
}

func (m *NotificationsModel) updateHistoryTable() {
	if m.history == nil {
		return
	}
	var rows []components.Row
	for _, entry := range m.history.Items {
		rows = append(rows, components.Row{
			Key: fmt.Sprintf("%d", entry.ID),
			Values: []string{
				formatTime(entry.Timestamp),
				entry.Severity,
				entry.Title,
				strings.Join(entry.ChannelsDelivered, ", "),
			},
		})
	}
	m.table.SetRows(rows)
}

func (m NotificationsModel) fetchChannelStatus() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.ChannelStatus(context.Background())
		return PollResultMsg{ViewID: ViewNotifications, Data: data, Err: err}
	}
}

func (m NotificationsModel) fetchHistory() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.NotificationHistory(context.Background(), 50, 0, "")
		return PollResultMsg{ViewID: ViewNotifications, Data: data, Err: err}
	}
}

func (m NotificationsModel) testNotification() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.TestNotification(context.Background())
		return ActionResultMsg{ViewID: ViewNotifications, Action: "test_notification", Data: data, Err: err}
	}
}

func (m NotificationsModel) fetchConfig() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.NotificationConfig(context.Background())
		return PollResultMsg{ViewID: ViewNotifications, Data: data, Err: err}
	}
}

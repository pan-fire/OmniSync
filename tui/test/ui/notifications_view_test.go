package ui_test

import (
	"fmt"
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// desktopOnlyBackend serves one channel without a settings form.
func desktopOnlyBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/notifications/channels/status", 200, map[string]any{"channels": map[string]any{"desktop": map[string]any{"available": true}}})
	b.json("GET", "/notifications/config", 200, map[string]any{"channels": map[string]any{"desktop": map[string]any{"enabled": true, "min_severity": "warning"}}})
	b.json("GET", "/notifications/history", 200, map[string]any{"items": []any{}, "total": 0})
	return b
}

// T tests every enabled channel (no body) and names both the channels that
// got the message and the ones that failed.
func TestNotificationsView_TestAllNamesDeliveredAndFailed(t *testing.T) {
	b := headlessBackend(t, 200)
	b.json("POST", "/notifications/test", 200, map[string]any{"success": false,
		"channels_delivered": []string{"ntfy", "webhook"}, "errors": map[string]any{"email": "connection refused"}})
	m := open(t, ui.NewNotificationsModel(b.client()))
	_, flashes := listsFlashes(t, m, press("T"))
	if got := strings.Join(flashes, "|"); got != "Delivered: ntfy, webhook | Failed: email" {
		t.Errorf("flashes = %q", got)
	}
	if body := bodyOf(b, "POST /notifications/test"); body != nil {
		t.Errorf("test all sent a channel: %v", body)
	}
}

// A refused test shows the backend's detail from the error envelope, for
// "test all" and for one channel alike.
func TestNotificationsView_TestRefusalShowsTheDetail(t *testing.T) {
	b := headlessBackend(t, 200)
	b.json("POST", "/notifications/test", 503, map[string]any{
		"detail": "No notification channel is enabled", "code": "no_channels", "details": map[string]any{"enabled": 0}})
	m := open(t, ui.NewNotificationsModel(b.client()))
	m, flashes := listsFlashes(t, m, press("T"))
	if len(flashes) != 1 || flashes[0] != "Error: API error 503: No notification channel is enabled" {
		t.Errorf("test all flashes = %v", flashes)
	}
	b.json("POST", "/notifications/test", 404, map[string]any{"detail": "Unknown channel: email", "code": "not_found", "details": nil})
	_, flashes = listsFlashes(t, m, press("t"))
	if len(flashes) != 1 || !strings.Contains(flashes[0], "Unknown channel: email") {
		t.Errorf("test channel flashes = %v", flashes)
	}
}

// The answer to a toggle is the new configuration: the table shows it at
// once and the user is told it was saved.
func TestNotificationsView_ToggleShowsTheSavedState(t *testing.T) {
	b := desktopOnlyBackend(t)
	b.json("PUT", "/notifications/config", 200, map[string]any{"channels": map[string]any{"desktop": map[string]any{"enabled": false, "min_severity": "error"}}})
	m := open(t, ui.NewNotificationsModel(b.client()))
	m, flashes := listsFlashes(t, m, press("enter"))
	if strings.Join(flashes, "|") != "Notification settings updated" {
		t.Errorf("flashes = %v", flashes)
	}
	if v := content(m); !strings.Contains(v, "disabled") || !strings.Contains(v, "error") {
		t.Errorf("table not updated from the answer:\n%s", v)
	}
}

// A refused toggle keeps the old state and says why.
func TestNotificationsView_RefusedToggleKeepsTheOldState(t *testing.T) {
	b := desktopOnlyBackend(t)
	b.json("PUT", "/notifications/config", 422, map[string]any{
		"detail": "desktop needs a session bus", "code": "validation_error", "details": map[string]any{"channel": "desktop"}})
	m := open(t, ui.NewNotificationsModel(b.client()))
	m, flashes := listsFlashes(t, m, press("e"))
	if len(flashes) != 1 || flashes[0] != "Error: API error 422: desktop needs a session bus" {
		t.Errorf("flashes = %v", flashes)
	}
	if v := content(m); !strings.Contains(v, "enabled") || strings.Contains(v, "disabled") {
		t.Errorf("table changed after a refusal:\n%s", v)
	}
}

// Before the first answer the view says it is loading; a failed poll shows
// the error instead of an empty table, and a backend without channels says
// so.
func TestNotificationsView_LoadingErrorAndEmptyStates(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/notifications/channels/status", 500, map[string]any{"detail": "notifier crashed", "code": "internal", "details": nil})
	b.json("GET", "/notifications/config", 500, map[string]any{"detail": "notifier crashed", "code": "internal", "details": nil})
	b.json("GET", "/notifications/history", 200, map[string]any{"items": []any{}, "total": 0})
	m := drive(t, ui.NewNotificationsModel(b.client()), tea.WindowSizeMsg{Width: 140, Height: 40})
	if v := content(m); !strings.Contains(v, "Loading...") {
		t.Errorf("no loading state:\n%s", v)
	}
	// The last answer of the batch (the configuration) is the failed one.
	m = drive(t, m, runAll(m.Init())...)
	v := content(m)
	if !strings.Contains(v, "Error: API error 500: notifier crashed") || !strings.Contains(v, "No channel data") {
		t.Errorf("error state:\n%s", v)
	}
	b.json("GET", "/notifications/channels/status", 200, map[string]any{"channels": map[string]any{}})
	b.json("GET", "/notifications/config", 200, map[string]any{"channels": map[string]any{}})
	b.json("GET", "/notifications/history", 200, map[string]any{"items": []any{}, "total": 0})
	m = drive(t, m, press("r"))
	v = content(m)
	if strings.Contains(v, "Error:") || !strings.Contains(v, "No channels configured") {
		t.Errorf("empty state:\n%s", v)
	}
	// Nothing to act on: toggle, severity and test send nothing.
	b.reset()
	_ = drive(t, m, press("e"), press("s"), press("t"), press("c"))
	if got := b.matching("P"); len(got) != 0 {
		t.Errorf("sent %v without a channel", got)
	}
}

// A tick refreshes status and history but not the configuration, which only
// changes through this view.
func TestNotificationsView_TickSkipsTheConfig(t *testing.T) {
	b := desktopOnlyBackend(t)
	m := open(t, ui.NewNotificationsModel(b.client()))
	b.reset()
	m = drive(t, m, ui.TickMsg{})
	if got := fmt.Sprint(b.log()); strings.Contains(got, "/notifications/config") || !strings.Contains(got, "/channels/status") || !strings.Contains(got, "/history") {
		t.Errorf("tick requests = %s", got)
	}
	b.reset()
	_ = drive(t, m, press("r"))
	if got := b.matching("GET /notifications/config"); len(got) != 1 {
		t.Errorf("r did not reload the config: %v", b.log())
	}
}

// c on a channel without settings explains instead of opening an empty form.
func TestNotificationsView_DesktopHasNoSettingsForm(t *testing.T) {
	b := desktopOnlyBackend(t)
	m := open(t, ui.NewNotificationsModel(b.client()))
	m, flashes := listsFlashes(t, m, press("c"))
	if m.CapturesInput() || strings.Join(flashes, "|") != "desktop has no settings to configure" {
		t.Errorf("captures=%v flashes=%v", m.CapturesInput(), flashes)
	}
	if v := content(m); strings.Contains(v, "c:configure") {
		t.Errorf("hint offers c for desktop:\n%s", v)
	}
}

// On the History tab the channel keys do nothing: e, s and c must not
// change a channel the user cannot see.
func TestNotificationsView_ChannelKeysIgnoredOnHistory(t *testing.T) {
	b := desktopOnlyBackend(t)
	b.json("GET", "/notifications/history", 200, map[string]any{"items": []any{
		map[string]any{"id": 1, "event_type": "sync_failed", "severity": "error", "title": "Erster", "body": "b", "timestamp": "2026-09-27T08:00:00+00:00", "channels_delivered": []string{"desktop"}},
		map[string]any{"id": 2, "event_type": "sync_failed", "severity": "error", "title": "Zweiter", "body": "b", "timestamp": "2026-09-27T09:00:00+00:00", "channels_delivered": []string{}},
	}, "total": 2})
	m := open(t, ui.NewNotificationsModel(b.client()))
	b.reset()
	m = drive(t, m, press("right"), press("down"), press("e"), press("s"), press("c"), press("t"))
	if len(b.log()) != 0 || m.CapturesInput() {
		t.Errorf("history tab sent %v or opened a form", b.log())
	}
	if v := content(m); !strings.Contains(v, "Zweiter") {
		t.Errorf("history:\n%s", v)
	}
	m = drive(t, m, press("left"))
	if !strings.Contains(content(m), "Selected: desktop") {
		t.Errorf("left did not return to the channels:\n%s", content(m))
	}
}

// The ntfy form sends the new token, and an empty username clears the
// stored password (a password without a user is meaningless).
func TestNotificationsView_NtfyFormSendsTokenAndClearsPassword(t *testing.T) {
	b := headlessBackend(t, 200)
	b.json("PUT", "/notifications/config", 200, map[string]any{"channels": map[string]any{
		"ntfy": map[string]any{"enabled": true, "min_severity": "warning", "ntfy": map[string]any{
			"server": "http://ntfy.lan", "topic": "nas", "allow_http": true, "username": "", "token_set": true, "password_set": false}},
	}})
	m := open(t, ui.NewNotificationsModel(b.client()))
	m = drive(t, m, press("down"))
	if v := content(m); !strings.Contains(v, "https://ntfy.sh/nas  token: set") {
		t.Errorf("ntfy summary:\n%s", v)
	}
	m = drive(t, m, press("c"))
	if !m.CapturesInput() || m.KeyHints() == "" || strings.Contains(m.KeyHints(), "T:test all") {
		t.Fatalf("form not open or hints wrong: %q", m.KeyHints())
	}
	if v := content(m); !strings.Contains(v, "ntfy settings") || !strings.Contains(v, "https://ntfy.sh or your own server") {
		t.Errorf("form:\n%s", v)
	}
	// Server: replace with a LAN address by pasting.
	for range "https://ntfy.sh" {
		m = drive(t, m, press("backspace"))
	}
	m = drive(t, m, tea.PasteMsg{Content: "http://ntfy.lan"})
	m = drive(t, m, press("tab"), press("tab"), press("right"), press("tab"))
	if v := content(m); !strings.Contains(v, "•••• set — leave empty to keep") {
		t.Errorf("stored token not marked as set:\n%s", v)
	}
	m = drive(t, m, typeKeys("tk_neu")...)
	m, flashes := listsFlashes(t, m, press("enter"))
	if m.CapturesInput() || strings.Join(flashes, "|") != "Channel settings saved" {
		t.Errorf("captures=%v flashes=%v", m.CapturesInput(), flashes)
	}
	ntfy := bodyOf(b, "PUT /notifications/config")["channels"].(map[string]any)["ntfy"].(map[string]any)["ntfy"].(map[string]any)
	if ntfy["server"] != "http://ntfy.lan" || ntfy["topic"] != "nas" || ntfy["allow_http"] != true || ntfy["token"] != "tk_neu" || ntfy["username"] != "" {
		t.Errorf("ntfy = %v", ntfy)
	}
	if _, ok := ntfy["password"]; ok {
		t.Errorf("empty password sent: %v", ntfy)
	}
	if fmt.Sprint(ntfy["clear"]) != "[password]" {
		t.Errorf("clear = %v", ntfy["clear"])
	}
	if v := content(m); !strings.Contains(v, "http://ntfy.lan/nas") {
		t.Errorf("summary not updated from the answer:\n%s", v)
	}
}

// Emptying the header name removes the stored header.
func TestNotificationsView_WebhookEmptyHeaderNameRemovesIt(t *testing.T) {
	b := headlessBackend(t, 200)
	m := open(t, ui.NewNotificationsModel(b.client()))
	m = drive(t, m, press("down"), press("down"), press("c"), press("tab"), press("tab"))
	for range "Authorization" {
		m = drive(t, m, press("backspace"))
	}
	_ = drive(t, m, press("enter"))
	wh := bodyOf(b, "PUT /notifications/config")["channels"].(map[string]any)["webhook"].(map[string]any)["webhook"].(map[string]any)
	if h, ok := wh["headers"].([]any); !ok || len(h) != 0 {
		t.Errorf("headers = %v", wh["headers"])
	}
}

// A webhook with two headers: the form edits the first, the second is kept
// by name (its secret value stays on the server).
func TestNotificationsView_WebhookKeepsFurtherHeaders(t *testing.T) {
	b := headlessBackend(t, 200)
	b.json("GET", "/notifications/channels/status", 200, map[string]any{"channels": map[string]any{"webhook": map[string]any{"available": true}}})
	b.json("GET", "/notifications/config", 200, map[string]any{"channels": map[string]any{
		"webhook": map[string]any{"enabled": true, "min_severity": "error", "webhook": map[string]any{
			"url": "http://192.168.1.5:8123/api/webhook/x", "allow_http": true,
			"headers": []any{map[string]any{"name": "Authorization", "value_set": true}, map[string]any{"name": "X-Extra", "value_set": false}}}},
	}})
	m := open(t, ui.NewNotificationsModel(b.client()))
	if v := content(m); !strings.Contains(v, "Headers: Authorization (set), X-Extra (not set)") {
		t.Errorf("summary:\n%s", v)
	}
	m = drive(t, m, press("c"))
	if v := content(m); !strings.Contains(v, "yes") {
		t.Errorf("allow http not shown as yes:\n%s", v)
	}
	_ = drive(t, m, press("enter"))
	wh := bodyOf(b, "PUT /notifications/config")["channels"].(map[string]any)["webhook"].(map[string]any)["webhook"].(map[string]any)
	h, _ := wh["headers"].([]any)
	if wh["allow_http"] != true || len(h) != 2 || h[0].(map[string]any)["name"] != "Authorization" || h[1].(map[string]any)["name"] != "X-Extra" {
		t.Errorf("webhook = %v", wh)
	}
}

// A "To" field of only commas passes the required check but names nobody:
// the form says so and nothing is sent.
func TestNotificationsView_EmailNeedsARecipient(t *testing.T) {
	b := headlessBackend(t, 200)
	m := open(t, ui.NewNotificationsModel(b.client()))
	m = drive(t, m, press("c"))
	m = drive(t, m, typeKeys("smtp.example.com")...)
	m = drive(t, m, press("tab"), press("tab"), press("tab"), press("tab"), press("tab"))
	m = drive(t, m, typeKeys("nas@example.com")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys(" , ")...)
	m = drive(t, m, press("enter"))
	if v := content(m); !m.CapturesInput() || !strings.Contains(v, "give at least one recipient") {
		t.Errorf("view:\n%s", v)
	}
	if len(b.matching("PUT")) != 0 {
		t.Error("sent an email setting without a recipient")
	}
	m = drive(t, m, press("esc"))
	if m.CapturesInput() {
		t.Error("Esc did not close the form")
	}
}

// Channels without stored settings show "Not configured" and their forms
// start from sensible defaults (ntfy.sh, port 587 with STARTTLS); a
// configured email channel is summarised without its password.
func TestNotificationsView_DefaultsAndSummaries(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/notifications/channels/status", 200, map[string]any{"channels": map[string]any{}})
	b.json("GET", "/notifications/config", 200, map[string]any{"channels": map[string]any{
		"email": map[string]any{"enabled": true, "min_severity": "info", "email": map[string]any{
			"host": "smtp.example.com", "port": 465, "security": "tls", "username": "nas", "password_set": true,
			"from_addr": "nas@example.com", "to": []string{"a@example.com", "b@example.com"}}},
		"ntfy":    map[string]any{"enabled": false, "min_severity": "warning"},
		"webhook": map[string]any{"enabled": false, "min_severity": "warning"},
	}})
	b.json("GET", "/notifications/history", 200, map[string]any{"items": []any{}, "total": 0})
	m := open(t, ui.NewNotificationsModel(b.client()))
	v := content(m)
	if !strings.Contains(v, "smtp.example.com:465 (tls) → a@example.com, b@example.com  password: set") {
		t.Errorf("email summary:\n%s", v)
	}
	m = drive(t, m, press("down"))
	if v := content(m); !strings.Contains(v, "Not configured — press c") {
		t.Errorf("ntfy summary:\n%s", v)
	}
	m = drive(t, m, press("c"), press("tab"), press("tab"), press("tab"))
	if v := content(m); !strings.Contains(v, "https://ntfy.sh") || !strings.Contains(v, "Optional (tk_") {
		t.Errorf("ntfy defaults:\n%s", v)
	}
	m = drive(t, m, press("esc"))
	m = drive(t, m, press("down"))
	if v := content(m); !strings.Contains(v, "Selected: webhook") || !strings.Contains(v, "Not configured — press c") {
		t.Errorf("webhook summary:\n%s", v)
	}
	m = drive(t, m, press("c"), press("tab"), press("tab"), press("tab"))
	if v := content(m); !strings.Contains(v, "Webhook settings") || !strings.Contains(v, "e.g. Bearer <token>") {
		t.Errorf("webhook defaults:\n%s", v)
	}
}

// The help overlay lists the test keys.
func TestNotificationsView_KeyBindingsNameTheTests(t *testing.T) {
	m := ui.NewNotificationsModel(newBackend(t).client())
	if m.ViewID() != ui.ViewNotifications {
		t.Errorf("view id = %v", m.ViewID())
	}
	keys := map[string]string{}
	for _, kb := range m.KeyBindings() {
		keys[kb.Key] = kb.Desc
	}
	if keys["T"] != "Test all enabled channels" || keys["t"] != "Test the selected channel" {
		t.Errorf("bindings = %v", keys)
	}
	if !strings.Contains(m.KeyHints(), "T:test all") {
		t.Errorf("hints = %q", m.KeyHints())
	}
}

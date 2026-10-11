package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// configViewBackend serves a config with history kept forever.
func configViewBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/config", 200, map[string]any{"log_level": "debug", "history_days": 0})
	b.json("PUT", "/config", 200, map[string]any{"log_level": "DEBUG", "history_days": 0})
	b.json("POST", "/config/test-sync", 200, map[string]any{"success": true, "steps": []any{}, "error": nil})
	return b
}

// The view shows "Loading..." first, then the values; 0 days reads as
// "forever", not "0 days".
func TestConfigView_LoadingThenValues(t *testing.T) {
	b := configViewBackend(t)
	m := ui.NewConfigModel(b.client())
	if content(m) != "" || m.ViewID() != ui.ViewConfig {
		t.Errorf("unsized view %q, id %v", content(m), m.ViewID())
	}
	m = drive(t, m, tea.WindowSizeMsg{Width: 120, Height: 30})
	if !strings.Contains(content(m), "Loading...") {
		t.Errorf("loading:\n%s", content(m))
	}
	m = drive(t, m, runAll(m.Init())...)
	if v := content(m); !strings.Contains(v, "debug") || !strings.Contains(v, "forever") {
		t.Errorf("values:\n%s", v)
	}
}

// A failed read shows the backend's message; r reads again and recovers.
func TestConfigView_LoadErrorAndRefresh(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/config", 503, map[string]any{"detail": "database is locked", "code": "db_busy"})
	m := open(t, ui.NewConfigModel(b.client()))
	if v := content(m); !strings.Contains(v, "Error: API error 503: database is locked") {
		t.Fatalf("error not shown:\n%s", v)
	}
	b.json("GET", "/config", 200, map[string]any{"log_level": "INFO", "history_days": 30})
	m = drive(t, m, press("r"))
	if v := content(m); strings.Contains(v, "Error") || !strings.Contains(v, "30 days") {
		t.Errorf("refresh did not recover:\n%s", v)
	}
}

// The edit form starts at the stored values (log level upper-cased to match
// the choices), and saving says so and reads the config again.
func TestConfigView_EditStartsAtStoredValuesAndSaves(t *testing.T) {
	b := configViewBackend(t)
	m := open(t, ui.NewConfigModel(b.client()))
	m = drive(t, m, press("e"))
	if !m.CapturesInput() || m.KeyHints() != "Enter:submit  Esc:cancel  Tab/Shift+Tab:field  Left/Right:choice" {
		t.Errorf("form mode: %v %q", m.CapturesInput(), m.KeyHints())
	}
	b.reset()
	m, out := rvDrive(t, m, press("enter"))
	if body := bodyOf(b, "PUT /config"); body["log_level"] != "DEBUG" || body["history_days"] != float64(0) {
		t.Errorf("body = %v", body)
	}
	if !out.has("Config updated") || len(b.matching("GET /config")) != 1 || m.CapturesInput() {
		t.Errorf("flashes = %v, requests = %v", out.flashes, b.log())
	}
}

// Without a loaded config the form offers the defaults (INFO, 90 days).
func TestConfigView_EditWithoutConfigUsesDefaults(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/config", 500, map[string]any{"detail": "boom"})
	b.json("PUT", "/config", 200, map[string]any{"log_level": "INFO", "history_days": 90})
	m := open(t, ui.NewConfigModel(b.client()))
	_ = drive(t, m, press("e"), press("enter"))
	if body := bodyOf(b, "PUT /config"); body["log_level"] != "INFO" || body["history_days"] != float64(90) {
		t.Errorf("body = %v", body)
	}
}

// History days outside 0..3650 or not a number are refused locally: the
// backend is never asked to store them.
func TestConfigView_EditRejectsBadHistoryDays(t *testing.T) {
	for _, input := range []string{"abc", "-1", "3651"} {
		b := configViewBackend(t)
		m := open(t, ui.NewConfigModel(b.client()))
		m = drive(t, m, press("e"), press("tab"), press("backspace"))
		m = drive(t, m, typeKeys(input)...)
		_, out := rvDrive(t, m, press("enter"))
		if !out.has("Keep history (days) must be a whole number from 0 to 3650") {
			t.Errorf("%s: flashes = %v", input, out.flashes)
		}
		if got := b.matching("PUT"); len(got) != 0 {
			t.Errorf("%s: sent %v", input, got)
		}
	}
}

// A save the backend refuses shows its message.
func TestConfigView_EditRefusalShowsDetail(t *testing.T) {
	b := configViewBackend(t)
	b.json("PUT", "/config", 422, map[string]any{"detail": "log_level: unknown level", "code": "validation_error", "details": map[string]any{"field": "log_level"}})
	m := open(t, ui.NewConfigModel(b.client()))
	m = drive(t, m, press("e"))
	_, out := rvDrive(t, m, press("enter"))
	if !out.has("Error: API error 422: log_level: unknown level") || out.has("Config updated") {
		t.Errorf("flashes = %v", out.flashes)
	}
}

// Esc closes either form without sending anything.
func TestConfigView_EscCancelsForms(t *testing.T) {
	b := configViewBackend(t)
	m := open(t, ui.NewConfigModel(b.client()))
	b.reset()
	for _, key := range []string{"e", "t"} {
		m = drive(t, m, press(key))
		if !m.CapturesInput() {
			t.Fatalf("%s: no form", key)
		}
		m = drive(t, m, press("esc"))
		if m.CapturesInput() || !strings.Contains(content(m), "Configuration") {
			t.Errorf("%s: Esc kept the form:\n%s", key, content(m))
		}
	}
	if reqs := b.log(); len(reqs) != 0 {
		t.Errorf("sent %v", reqs)
	}
}

// Pasted paths land in the open form's field (both forms take paste).
func TestConfigView_PasteIntoForms(t *testing.T) {
	b := configViewBackend(t)
	m := open(t, ui.NewConfigModel(b.client()))
	m = drive(t, m, press("t"))
	m = drive(t, m, tea.PasteMsg{Content: "/srv/data"})
	m = drive(t, m, press("tab"))
	m = drive(t, m, tea.PasteMsg{Content: "nas:Backup"})
	m = drive(t, m, press("enter"))
	if body := bodyOf(b, "POST /config/test-sync"); body["local_dir"] != "/srv/data" || body["remote_dir"] != "nas:Backup" {
		t.Errorf("body = %v", body)
	}
	m = drive(t, m, press("e"), press("tab"), press("backspace"))
	m = drive(t, m, tea.PasteMsg{Content: "7"})
	_ = drive(t, m, press("enter"))
	if body := bodyOf(b, "PUT /config"); body["history_days"] != float64(7) {
		t.Errorf("body = %v", body)
	}
}

// The test sync shows that it runs, and its result (pass, fail with the
// reason, or the API error) is flashed.
func TestConfigView_TestSyncResults(t *testing.T) {
	cases := []struct {
		name   string
		status int
		reply  map[string]any
		want   string
	}{
		{"pass", 200, map[string]any{"success": true, "steps": []any{}, "error": nil}, "Sync test passed"},
		{"fail", 200, map[string]any{"success": false, "steps": []any{}, "error": "remote dir not found"}, "Sync test failed: remote dir not found"},
		{"fail without reason", 200, map[string]any{"success": false, "steps": []any{}, "error": nil}, "Sync test failed: unknown error"},
		{"api error", 400, map[string]any{"detail": "local_dir does not exist", "code": "bad_path"}, "Error: API error 400: local_dir does not exist"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := configViewBackend(t)
			b.json("POST", "/config/test-sync", tc.status, tc.reply)
			m := open(t, ui.NewConfigModel(b.client()))
			m = drive(t, m, press("t"))
			m = drive(t, m, typeKeys("/tmp/a")...)
			m = drive(t, m, press("tab"))
			m = drive(t, m, typeKeys("nas:a")...)
			m, submit := rvStep(m, press("enter"))
			m, msgs := rvStep(m, submit[0])
			if !strings.Contains(content(m), "Running sync test...") {
				t.Errorf("running:\n%s", content(m))
			}
			m, out := rvDrive(t, m, msgs...)
			if !out.has(tc.want) {
				t.Errorf("flashes = %v", out.flashes)
			}
			if strings.Contains(content(m), "Running sync test...") {
				t.Errorf("still running:\n%s", content(m))
			}
		})
	}
}

// Every key the bottom bar offers has a help entry.
func TestConfigView_HintsHaveHelpEntries(t *testing.T) {
	m := ui.NewConfigModel(newBackend(t).client())
	keys := map[string]bool{}
	for _, kb := range m.KeyBindings() {
		keys[kb.Key] = true
	}
	for _, hint := range strings.Split(m.KeyHints(), "  ") {
		if key := strings.SplitN(hint, ":", 2)[0]; !keys[key] {
			t.Errorf("hint %q has no help entry", hint)
		}
	}
}

package ui_test

import (
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// headlessBackend serves the three headless channels; the webhook already
// has an Authorization header stored (only value_set comes back).
func headlessBackend(t *testing.T, putStatus int) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/notifications/channels/status", 200, map[string]any{"channels": map[string]any{
		"email":   map[string]any{"available": false, "missing_dependencies": []string{"smtp_host", "mail_from", "mail_to"}},
		"ntfy":    map[string]any{"available": true},
		"webhook": map[string]any{"available": true},
	}})
	b.json("GET", "/notifications/config", 200, map[string]any{"channels": map[string]any{
		"email": map[string]any{"enabled": false, "min_severity": "warning", "email": map[string]any{
			"host": "", "port": 587, "security": "starttls", "username": "", "password_set": false, "from_addr": "", "to": []string{}}},
		"ntfy": map[string]any{"enabled": true, "min_severity": "warning", "ntfy": map[string]any{
			"server": "https://ntfy.sh", "topic": "nas", "allow_http": false, "username": "", "token_set": true, "password_set": false}},
		"webhook": map[string]any{"enabled": true, "min_severity": "error", "webhook": map[string]any{
			"url": "https://hooks.example.com/x", "allow_http": false,
			"headers": []any{map[string]any{"name": "Authorization", "value_set": true}}}},
	}})
	b.json("GET", "/notifications/history", 200, map[string]any{"items": []any{}, "total": 0})
	if putStatus == 200 {
		b.json("PUT", "/notifications/config", 200, map[string]any{"channels": map[string]any{}})
	} else {
		b.json("PUT", "/notifications/config", putStatus, map[string]any{"detail": "Invalid webhook URL: the URL must use https"})
	}
	b.json("POST", "/notifications/test", 200, map[string]any{"success": true, "channels_delivered": []string{"webhook"}, "errors": map[string]any{}})
	return b
}

// Rows are sorted: email, ntfy, webhook.
func TestNotifications_WebhookFormKeepsTheStoredSecret(t *testing.T) {
	b := headlessBackend(t, 200)
	m := open(t, ui.NewNotificationsModel(b.client()))
	v := content(m)
	if !strings.Contains(v, "Missing: smtp_host, mail_from, mail_to") {
		t.Errorf("email row lacks what is missing:\n%s", v)
	}
	m = drive(t, m, press("down"), press("down"))
	if v = content(m); !strings.Contains(v, "URL: https://hooks.example.com/x  Headers: Authorization (set)") {
		t.Errorf("summary:\n%s", v)
	}
	m = drive(t, m, press("c"))
	if !m.CapturesInput() {
		t.Fatal("form not open")
	}
	v = content(m)
	for _, want := range []string{"Webhook settings", "URL", "Allow http", "Header name", "Header value"} {
		if !strings.Contains(v, want) {
			t.Errorf("form lacks %q:\n%s", want, v)
		}
	}
	// Change only the URL; the header value stays empty (kept).
	m = drive(t, m, press("backspace"))
	m = drive(t, m, typeKeys("y")...)
	m = drive(t, m, press("enter"))
	if m.CapturesInput() {
		t.Fatalf("form still open:\n%s", content(m))
	}
	body := bodyOf(b, "PUT /notifications/config")
	ch := body["channels"].(map[string]any)
	if len(ch) != 1 {
		t.Fatalf("update touches more than the webhook: %v", body)
	}
	wh := ch["webhook"].(map[string]any)["webhook"].(map[string]any)
	if wh["url"] != "https://hooks.example.com/y" || wh["allow_http"] != false {
		t.Errorf("webhook = %v", wh)
	}
	headers := wh["headers"].([]any)
	if len(headers) != 1 || headers[0].(map[string]any)["name"] != "Authorization" || headers[0].(map[string]any)["value"] != "" {
		t.Errorf("headers = %v", headers)
	}
}

func TestNotifications_RefusedSettingsReopenTheForm(t *testing.T) {
	b := headlessBackend(t, 400)
	m := open(t, ui.NewNotificationsModel(b.client()))
	m = drive(t, m, press("down"), press("down"), press("c"), press("enter"))
	if !m.CapturesInput() {
		t.Fatal("form closed after a refusal")
	}
	if v := content(m); !strings.Contains(v, "must use https") {
		t.Errorf("error not shown:\n%s", v)
	}
	m = drive(t, m, press("esc"))
	if m.CapturesInput() {
		t.Error("Esc did not close the form")
	}
}

func TestNotifications_EmailFormSendsAllFields(t *testing.T) {
	b := headlessBackend(t, 200)
	m := open(t, ui.NewNotificationsModel(b.client()))
	m = drive(t, m, press("c")) // email
	m = drive(t, m, typeKeys("smtp.example.com")...)
	m = drive(t, m, press("tab"), press("tab"), press("right")) // security: tls
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("nas")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("pässwort")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("nas@example.com")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("a@example.com, b@example.com")...)
	if strings.Contains(content(m), "pässwort") {
		t.Error("password shown in clear")
	}
	m = drive(t, m, press("enter"))
	body := bodyOf(b, "PUT /notifications/config")
	email := body["channels"].(map[string]any)["email"].(map[string]any)["email"].(map[string]any)
	if email["host"] != "smtp.example.com" || email["port"] != float64(587) || email["security"] != "tls" ||
		email["username"] != "nas" || email["password"] != "pässwort" || email["from_addr"] != "nas@example.com" {
		t.Errorf("email = %v", email)
	}
	if to := email["to"].([]any); len(to) != 2 || to[1] != "b@example.com" {
		t.Errorf("to = %v", to)
	}
	_ = m
}

func TestNotifications_EmailFormChecksThePort(t *testing.T) {
	b := headlessBackend(t, 200)
	m := open(t, ui.NewNotificationsModel(b.client()))
	m = drive(t, m, press("c"))
	m = drive(t, m, typeKeys("smtp.example.com")...)
	m = drive(t, m, press("tab"), press("backspace"), press("backspace"), press("backspace"))
	m = drive(t, m, typeKeys("99999")...)
	m = drive(t, m, press("tab"), press("tab"), press("tab"), press("tab"))
	m = drive(t, m, typeKeys("a@b.c")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("d@e.f")...)
	m = drive(t, m, press("enter"))
	if v := content(m); !strings.Contains(v, "port must be a number from 1 to 65535") {
		t.Errorf("view:\n%s", v)
	}
	if len(b.matching("PUT")) != 0 {
		t.Error("sent an invalid port")
	}
}

func TestNotifications_TestSelectedChannel(t *testing.T) {
	b := headlessBackend(t, 200)
	m := open(t, ui.NewNotificationsModel(b.client()))
	_ = drive(t, m, press("down"), press("down"), press("t"))
	if body := bodyOf(b, "POST /notifications/test"); body["channel"] != "webhook" {
		t.Errorf("body = %v", body)
	}
}

func TestNotifications_ToggleSendsOnlyThatChannel(t *testing.T) {
	b := headlessBackend(t, 200)
	m := open(t, ui.NewNotificationsModel(b.client()))
	_ = drive(t, m, press("down"), press("s"))
	body := bodyOf(b, "PUT /notifications/config")
	ch := body["channels"].(map[string]any)
	if len(ch) != 1 || ch["ntfy"].(map[string]any)["min_severity"] != "error" {
		t.Errorf("body = %v", body)
	}
	if _, ok := ch["ntfy"].(map[string]any)["enabled"]; ok {
		t.Errorf("severity change also sent enabled: %v", body)
	}
}

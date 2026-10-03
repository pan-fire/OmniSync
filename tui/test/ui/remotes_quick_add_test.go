package ui_test

import (
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

func quickAddBackend(t *testing.T) *backend {
	t.Helper()
	b := wizardBackend(t)
	b.json("GET", "/remotes", 200, []any{})
	return b
}

// c asks name and type (key-based providers only), then the
// provider's fields, and creates the remote with POST /wizard/create.
func TestRemotes_QuickAddCreatesKeyRemote(t *testing.T) {
	b := quickAddBackend(t)
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("c"))
	if !m.CapturesInput() {
		t.Fatal("the quick-add form must own the keyboard")
	}
	v := content(m)
	if !strings.Contains(v, "Quick add remote") || !strings.Contains(v, "Name") {
		t.Fatalf("form:\n%s", v)
	}
	m = drive(t, m, typeKeys("mys3")...)
	m = drive(t, m, press("tab"), press("right")) // one choice: stays on S3
	if line := rawLine(t, m.View().Content, "Type:"); !strings.Contains(stripANSI(line), "Amazon S3 (s3)") {
		t.Errorf("type choices must be the key-based providers only: %q", stripANSI(line))
	}
	m = drive(t, m, press("enter"))
	if v = content(m); !strings.Contains(v, `Quick add Amazon S3 remote "mys3"`) || !strings.Contains(v, "Access key") {
		t.Fatalf("fields step:\n%s", v)
	}
	m = drive(t, m, typeKeys("AKIA")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("s3cr3t")...)
	m = drive(t, m, press("enter"))
	body := bodyOf(b, "POST /wizard/create")
	if body == nil {
		t.Fatalf("no create: %v", b.log())
	}
	params, _ := body["params"].(map[string]any)
	if body["name"] != "mys3" || body["provider_id"] != "s3" || params["access_key_id"] != "AKIA" || params["secret_access_key"] != "s3cr3t" {
		t.Errorf("body = %v", body)
	}
	if _, ok := body["session_id"]; ok {
		t.Errorf("session_id sent: %v", body)
	}
	if m.CapturesInput() {
		t.Errorf("form still open after create:\n%s", content(m))
	}
	if n := len(b.matching("GET /remotes")); n != 2 {
		t.Errorf("list not refreshed after create (%d reads)", n)
	}
}

// A refused create brings the fields back with the message; Esc cancels.
func TestRemotes_QuickAddErrorKeepsInput(t *testing.T) {
	b := quickAddBackend(t)
	b.json("POST", "/wizard/create", 400, map[string]any{"detail": "Remote 'mys3' already exists"})
	m := open(t, ui.NewRemotesModel(b.client()))
	m = drive(t, m, press("c"))
	m = drive(t, m, typeKeys("mys3")...)
	m = drive(t, m, press("enter"))
	m = drive(t, m, typeKeys("AKIA")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("x")...)
	m = drive(t, m, press("enter"))
	v := content(m)
	if !strings.Contains(v, "already exists") || !strings.Contains(v, "AKIA") || !m.CapturesInput() {
		t.Fatalf("error not shown in the form:\n%s", v)
	}
	m = drive(t, m, press("esc"))
	if m.CapturesInput() || !strings.Contains(content(m), "Remotes") {
		t.Errorf("Esc did not return to the list:\n%s", content(m))
	}
}

package ui_test

import (
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
)

// t flips the highlighted target's enabled flag (only that field is sent)
// and reloads the list, so the table shows the new state.
func TestProfileDetailBackups_ToggleEnabled(t *testing.T) {
	for _, enabled := range []bool{true, false} {
		b := backupsBackend(t, map[string]any{"enabled": enabled})
		b.json("PUT", "/profiles/docs/backups/3", 200, map[string]any{"id": 3})
		m := backupsTab(t, b)
		b.reset()
		_ = detailStep(t, m, press("t"))
		bodies := b.bodiesOf("PUT /profiles/docs/backups/3")
		if len(bodies) != 1 || bodies[0]["enabled"] != !enabled || len(bodies[0]) != 1 {
			t.Errorf("enabled=%v: PUT bodies = %v", enabled, bodies)
		}
		if got := b.matching("GET /profiles/docs/backups"); len(got) != 1 {
			t.Errorf("enabled=%v: list not reloaded: %v", enabled, b.log())
		}
	}
}

// A refused toggle shows the envelope's detail and does not reload.
func TestProfileDetailBackups_ToggleRefused(t *testing.T) {
	b := backupsBackend(t, nil)
	b.json("PUT", "/profiles/docs/backups/3", 422, errorEnvelope("The target folder no longer exists", "invalid_target"))
	m := backupsTab(t, b)
	b.reset()
	got := flashesAfter(m, "t", time.Second)
	if strings.Join(got, "|") != "Error: API error 422: The target folder no longer exists" {
		t.Errorf("flashes = %q", got)
	}
	if reloads := b.matching("GET /profiles/docs/backups"); len(reloads) != 0 {
		t.Errorf("reloaded after a refusal: %v", reloads)
	}
}

// Without any target, t has nothing to toggle and sends nothing.
func TestProfileDetailBackups_ToggleWithoutTargets(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/profiles/docs/backups", 200, []any{})
	m := backupsTab(t, b)
	b.reset()
	_ = detailStep(t, m, press("t"))
	if got := b.matching("PUT "); len(got) != 0 {
		t.Errorf("requests = %v", got)
	}
}

// Pasting into the create form fills the focused field (a long target path
// is usually pasted, not typed); Esc cancels without creating anything.
func TestProfileDetailBackups_PasteIntoCreateForm(t *testing.T) {
	b := backupsBackend(t, nil)
	m := backupsTab(t, b)
	m = detailStep(t, m, press("c"))
	m = detailStep(t, m, tea.PasteMsg{Content: "Pasted Nightly"})
	if v := view(m); !strings.Contains(v, "Pasted Nightly") {
		t.Fatalf("paste not in the form:\n%s", v)
	}
	b.reset()
	m = detailStep(t, m, press("esc"))
	if m.CapturesInput() || len(b.matching("POST ")) != 0 {
		t.Errorf("Esc did not cancel the form: %v", b.log())
	}
	// A paste with no form open changes nothing.
	m = detailStep(t, m, tea.PasteMsg{Content: "stray"})
	if strings.Contains(view(m), "stray") {
		t.Errorf("stray paste shown:\n%s", view(m))
	}
}

package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// t disables an enabled profile and enables a disabled one; each sends one
// request for the highlighted profile and reloads the list.
func TestProfilesView_ToggleFollowsTheEnabledFlag(t *testing.T) {
	b := profilesBackend(t)
	pics := profileJSON("pics", "Bilder", "idle")
	pics["enabled"] = false
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "Dokumente", "idle"), pics})
	b.json("POST", "/profiles/docs/disable", 200, profileJSON("docs", "Dokumente", "idle"))
	b.json("POST", "/profiles/pics/enable", 200, profileJSON("pics", "Bilder", "idle"))
	m := open(t, ui.NewProfilesModel(b.client()))
	b.reset()
	m, flashes := listsFlashes(t, m, press("t"))
	if got := b.matching("POST"); len(got) != 1 || got[0] != "POST /profiles/docs/disable" {
		t.Errorf("toggle docs = %v", got)
	}
	if strings.Join(flashes, "|") != "Profile toggled" {
		t.Errorf("flashes = %v", flashes)
	}
	if len(b.matching("GET /profiles")) == 0 {
		t.Error("list not reloaded after the toggle")
	}
	m = drive(t, m, press("down"))
	b.reset()
	_, _ = listsFlashes(t, m, press("t"))
	if got := b.matching("POST"); len(got) != 1 || got[0] != "POST /profiles/pics/enable" {
		t.Errorf("toggle pics = %v", got)
	}
}

// A refused toggle shows the backend's reason from the error envelope.
func TestProfilesView_RefusedToggleShowsTheDetail(t *testing.T) {
	b := profilesBackend(t)
	b.json("POST", "/profiles/docs/disable", 409, map[string]any{
		"detail": "A sync of docs is running; stop it first", "code": "sync_running", "details": map[string]any{"slug": "docs"}})
	m := open(t, ui.NewProfilesModel(b.client()))
	_, flashes := listsFlashes(t, m, press("t"))
	if len(flashes) != 1 || !strings.Contains(flashes[0], "API error 409: A sync of docs is running; stop it first") {
		t.Errorf("flashes = %v", flashes)
	}
}

// A refused delete is reported too; the list is reloaded so it shows what
// is really there.
func TestProfilesView_RefusedDeleteShowsTheDetail(t *testing.T) {
	b := profilesBackend(t)
	b.json("DELETE", "/profiles/docs", 409, map[string]any{"detail": "Profile docs is syncing", "code": "sync_running", "details": nil})
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("d"))
	if m.KeyHints() != "y:yes  n/Esc:no" {
		t.Errorf("confirm hints = %q", m.KeyHints())
	}
	b.reset()
	m, flashes := listsFlashes(t, m, press("y"))
	if len(flashes) != 1 || !strings.Contains(flashes[0], "Profile deleted failed: API error 409: Profile docs is syncing") {
		t.Errorf("flashes = %v", flashes)
	}
	if m.CapturesInput() {
		t.Error("prompt still open")
	}
}

// Esc at the delete prompt sends nothing, like n.
func TestProfilesView_DeleteEscSendsNothing(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("d"), press("esc"))
	if len(b.matching("DELETE")) != 0 || m.CapturesInput() {
		t.Errorf("Esc sent %v or left the prompt open", b.matching("DELETE"))
	}
}

// Before the first answer the view says it is loading; a failed poll shows
// the backend's reason, and the next good poll clears it.
func TestProfilesView_LoadingAndPollError(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/profiles", 503, map[string]any{"detail": "database is locked", "code": "db_busy", "details": nil})
	m := drive(t, ui.NewProfilesModel(b.client()), tea.WindowSizeMsg{Width: 140, Height: 40})
	if !strings.Contains(content(m), "Loading...") {
		t.Errorf("no loading state:\n%s", content(m))
	}
	m = drive(t, m, runAll(m.Init())...)
	if v := content(m); !strings.Contains(v, "Error: API error 503: database is locked") {
		t.Errorf("error not shown:\n%s", v)
	}
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "Dokumente", "idle")})
	m = drive(t, m, ui.TickMsg{})
	if v := content(m); strings.Contains(v, "Error:") || !strings.Contains(v, "Dokumente") {
		t.Errorf("tick did not recover:\n%s", v)
	}
	// Nothing selected-dependent may fire on an empty list.
	b.json("GET", "/profiles", 200, []any{})
	m = drive(t, m, press("r"))
	b.reset()
	m = drive(t, m, press("t"), press("e"), press("d"))
	if len(b.log()) != 0 || m.CapturesInput() {
		t.Errorf("empty list sent %v or opened a mode", b.log())
	}
}

// While the backend saves, the form is replaced by a notice and ignores
// keys, so a second Enter cannot send the profile twice.
func TestProfilesView_SavingIgnoresKeys(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("c"))
	if !strings.Contains(m.KeyHints(), "Ctrl+O:browse folders") {
		t.Errorf("form hints = %q", m.KeyHints())
	}
	m = drive(t, m, tea.PasteMsg{Content: "Neu"})
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("/home/u/neu")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("gdrive:neu")...)
	// Submit, but hold the request back.
	updated, cmd := m.Update(press("enter"))
	m = updated.(ui.ProfilesModel)
	submit, ok := find[tea.Msg](runAll(cmd))
	if !ok {
		t.Fatal("no submit message")
	}
	updated, save := m.Update(submit)
	m = updated.(ui.ProfilesModel)
	if v := content(m); !strings.Contains(v, "Saving the profile...") || m.KeyHints() != "Saving..." {
		t.Errorf("saving state: hints %q view:\n%s", m.KeyHints(), v)
	}
	updated, again := m.Update(press("enter"))
	m = updated.(ui.ProfilesModel)
	if again != nil {
		t.Error("Enter while saving produced a command")
	}
	m = drive(t, m, runAll(save)...)
	if bodies := b.bodiesOf("POST /profiles"); len(bodies) != 1 || bodies[0]["name"] != "Neu" {
		t.Errorf("bodies = %v", bodies)
	}
	if m.CapturesInput() {
		t.Error("form still open after the save")
	}
}

// Ctrl+O only browses on the folder fields; elsewhere it explains.
func TestProfilesView_CtrlOOnNameExplains(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("c"))
	m, flashes := listsFlashes(t, m, ctrlO)
	if strings.Join(flashes, "|") != "Ctrl+O browses folders on the Local Dir and Remote Dir fields" || !m.CapturesInput() {
		t.Errorf("flashes = %v", flashes)
	}
	m = drive(t, m, press("esc"))
	if m.CapturesInput() || !strings.Contains(m.KeyHints(), "t:toggle") {
		t.Errorf("Esc did not return to the list: %q", m.KeyHints())
	}
}

// The view polls fast only while a profile syncs, and the help overlay
// names the toggle key.
func TestProfilesView_PollSpecAndBindings(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	if m.ViewID() != ui.ViewProfiles || m.PollSpec().IsActive(nil) {
		t.Error("idle profiles polled fast")
	}
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "Dokumente", "pushing")})
	m = drive(t, m, ui.TickMsg{})
	if !m.PollSpec().IsActive(nil) {
		t.Error("a pushing profile is not polled fast")
	}
	found := false
	for _, kb := range m.KeyBindings() {
		if kb.Key == "t" && kb.Desc == "Toggle enable/disable" {
			found = true
		}
	}
	if !found {
		t.Errorf("bindings = %v", m.KeyBindings())
	}
}

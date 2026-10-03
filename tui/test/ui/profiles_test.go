package ui_test

import (
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

func profilesBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	pics := profileJSON("pics", "Bilder", "error")
	pics["last_error"] = "remote unreachable"
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "Dokumente", "idle"), pics})
	b.json("POST", "/profiles", 201, profileJSON("new", "Neu", "idle"))
	b.json("PUT", "/profiles/docs", 200, profileJSON("docs", "Dokumente", "idle"))
	b.json("PUT", "/profiles/pics", 200, profileJSON("pics", "Bilder", "idle"))
	b.json("DELETE", "/profiles/docs", 204, nil)
	b.json("DELETE", "/profiles/pics", 204, nil)
	return b
}

func bodyOf(b *backend, prefix string) map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	for i, r := range b.requests {
		if strings.HasPrefix(r, prefix) {
			return b.bodies[i]
		}
	}
	return nil
}

// The create form takes spaces, digits, 'q' and umlauts, and Tab
// moves between fields.
func TestProfiles_CreateFormAcceptsOrdinaryInput(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("c"))
	if !m.CapturesInput() {
		t.Fatal("the form must own the keyboard")
	}
	msgs := typeKeys("Meine Dateien 2 q ÄÖÜ")
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("/home/u/Meine Dateien")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("gdrive:Sicherung 2026")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("- *.tmp, - .cache/**")...)
	msgs = append(msgs, press("enter"))
	m = drive(t, m, msgs...)

	body := bodyOf(b, "POST /profiles")
	if body == nil {
		t.Fatalf("no create request: %v", b.log())
	}
	if body["name"] != "Meine Dateien 2 q ÄÖÜ" || body["local_dir"] != "/home/u/Meine Dateien" || body["remote_dir"] != "gdrive:Sicherung 2026" {
		t.Errorf("body = %v", body)
	}
	if f, _ := body["rclone_filter"].([]any); len(f) != 2 || f[0] != "- *.tmp" {
		t.Errorf("filters = %v", body["rclone_filter"])
	}
	if m.CapturesInput() {
		t.Error("form still open after submit")
	}
}

// The edit form writes to the profile it was opened for, even if a
// poll reorders the table while it is open.
func TestProfiles_EditUsesCapturedProfile(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("e")) // cursor on docs
	// Poll arrives: pics is now first.
	m = drive(t, m, ui.PollResultMsg{ViewID: ui.ViewProfiles, Data: []api.ProfileStatusResponse{
		{ProfileResponse: api.ProfileResponse{Slug: "pics", Name: "Bilder"}},
		{ProfileResponse: api.ProfileResponse{Slug: "docs", Name: "Dokumente"}},
	}})
	m = drive(t, m, press("enter"))
	if len(b.matching("PUT /profiles/docs")) != 1 || len(b.matching("PUT /profiles/pics")) != 0 {
		t.Errorf("edit went to %v", b.matching("PUT"))
	}
	_ = m
}

// Delete asks, then sends ?confirm=true for the captured row.
func TestProfiles_DeleteConfirmsCapturedProfile(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("d"))
	if len(b.matching("DELETE")) != 0 {
		t.Fatal("deleted without confirmation")
	}
	if !strings.Contains(content(m), "Dokumente") {
		t.Errorf("prompt: %s", content(m))
	}
	m = drive(t, m, ui.PollResultMsg{ViewID: ui.ViewProfiles, Data: []api.ProfileStatusResponse{
		{ProfileResponse: api.ProfileResponse{Slug: "pics"}}, {ProfileResponse: api.ProfileResponse{Slug: "docs"}},
	}})
	m = drive(t, m, press("y"))
	if got := b.matching("DELETE"); len(got) != 1 || got[0] != "DELETE /profiles/docs?confirm=true" {
		t.Errorf("delete = %v", got)
	}
	_ = m
}

func TestProfiles_DeleteCancelled(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("d"), press("n"))
	if len(b.matching("DELETE")) != 0 || m.CapturesInput() {
		t.Error("cancelled delete sent a request or left the prompt open")
	}
}

// A poll keeps the cursor on the same profile.
func TestProfiles_PollKeepsSelection(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("down")) // pics
	m = drive(t, m, ui.PollResultMsg{ViewID: ui.ViewProfiles, Data: []api.ProfileStatusResponse{
		{ProfileResponse: api.ProfileResponse{Slug: "pics", Name: "Bilder"}},
		{ProfileResponse: api.ProfileResponse{Slug: "zzz", Name: "Neu"}},
		{ProfileResponse: api.ProfileResponse{Slug: "docs", Name: "Dokumente"}},
	}})
	_, cmd := m.Update(press("enter"))
	nav, _ := find[ui.NavigateMsg](runAll(cmd))
	if nav.Param != "pics" {
		t.Errorf("selection moved to %q", nav.Param)
	}
}

func TestProfiles_ShowsLastError(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	if !strings.Contains(content(m), "error: remote unreach") {
		t.Errorf("state column lacks the reason:\n%s", content(m))
	}
	m = drive(t, m, press("down"))
	if !strings.Contains(content(m), "Bilder: remote unreachable") {
		t.Errorf("selected profile's error not shown:\n%s", content(m))
	}
	if got := b.matching("GET /profiles/pics/sync/status"); len(got) != 0 {
		t.Errorf("extra status lookups: %v", got)
	}
}

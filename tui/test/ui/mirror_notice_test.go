package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

func mirrorProfileJSON(slug, name string, dismissed bool) map[string]any {
	p := profileJSON(slug, name, "idle")
	p["sync_mode"] = "mirror"
	p["mirror_notice_dismissed"] = dismissed
	return p
}

// A mirror profile explains mirror mode, its risk and the switch; h hides
// the note on the backend, and a hidden note is not shown.
func TestMirrorNotice_DetailExplainsAndHides(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/profiles/docs", 200, mirrorProfileJSON("docs", "Dokumente", false))
	b.json("PUT", "/profiles/docs", 200, mirrorProfileJSON("docs", "Dokumente", true))
	m := openDetail(t, b)
	v := view(m)
	for _, want := range []string{"Mirror mode:", "one-way copy", "overwrites or", "paused or offline",
		"w: switch to two-way", "resync", "deletes nothing", "h: hide this note"} {
		if !strings.Contains(v, want) {
			t.Errorf("overview lacks %q:\n%s", want, v)
		}
	}
	if !hasKey(m.KeyBindings(), "h") {
		t.Error("h is not in the key help")
	}

	b.reset()
	b.json("GET", "/profiles/docs", 200, mirrorProfileJSON("docs", "Dokumente", true))
	m = detailStep(t, m, press("h"))
	bodies := b.bodiesOf("PUT /profiles/docs")
	if len(bodies) != 1 || len(bodies[0]) != 1 || bodies[0]["mirror_notice_dismissed"] != true {
		t.Fatalf("hide bodies = %v", bodies)
	}
	v = view(m)
	if strings.Contains(v, "Mirror mode:") {
		t.Errorf("hidden note still shown:\n%s", v)
	}
	// The mode and the switch key stay visible.
	if !strings.Contains(v, "Mode: mirror") || !strings.Contains(v, "w:switch to two-way") {
		t.Errorf("mode or switch key gone:\n%s", v)
	}

	// h shows it again.
	b.reset()
	_ = detailStep(t, m, press("h"))
	if bodies := b.bodiesOf("PUT /profiles/docs"); len(bodies) != 1 || bodies[0]["mirror_notice_dismissed"] != false {
		t.Errorf("show bodies = %v", bodies)
	}
}

func TestMirrorNotice_NotShownForTwoWay(t *testing.T) {
	b := twoWayBackend(t, false, false)
	m := openDetail(t, b)
	if v := view(m); strings.Contains(v, "Mirror mode:") {
		t.Errorf("two-way profile shows the mirror note:\n%s", v)
	}
	b.reset()
	if got := detailFlashes(m, "h"); len(got) != 1 || !strings.Contains(got[0], "only for mirror profiles") {
		t.Errorf("h flash = %v", got)
	}
	if len(mutations(b)) != 0 {
		t.Errorf("h sent %v", mutations(b))
	}
}

// mirrorDashboard has two mirror profiles (one with its note hidden) and a
// two-way one.
func mirrorDashboard(t *testing.T) *backend {
	t.Helper()
	b := dashboardBackend(t)
	docs := mirrorProfileJSON("docs", "Dokumente", false)
	pics := mirrorProfileJSON("pics", "Bilder", true)
	music := profileJSON("music", "Musik", "idle")
	music["sync_mode"] = "two_way"
	b.json("GET", "/profiles", 200, []any{docs, pics, music})
	b.json("PUT", "/profiles/docs", 200, profileJSON("docs", "Dokumente", "idle"))
	b.json("PUT", "/profiles/pics", 200, profileJSON("pics", "Bilder", "idle"))
	return b
}

// dashStepFlashes is dashStep that also returns the flash texts produced.
func dashStepFlashes(m ui.DashboardModel, msg tea.Msg) (ui.DashboardModel, []string) {
	var flashes []string
	queue := []tea.Msg{msg}
	for i := 0; i < 50 && len(queue) > 0; i++ {
		next := queue[0]
		queue = queue[1:]
		if f, ok := next.(ui.FlashMsg); ok {
			flashes = append(flashes, f.Text)
			continue
		}
		updated, cmd := m.Update(next)
		m = updated.(ui.DashboardModel)
		queue = append(queue, runAll(cmd)...)
	}
	return m, flashes
}

func TestMirrorNotice_DashboardListsShownNotes(t *testing.T) {
	b := mirrorDashboard(t)
	m := openDashboard(t, b)
	v := stripANSI(m.View().Content)
	for _, want := range []string{"Mirror mode (1)", "Dokumente sync(s) as a one-way mirror", "overwritten or deleted",
		"deletes nothing", "w: switch all mirror profiles to two-way", "Mode", "two-way"} {
		if !strings.Contains(v, want) {
			t.Errorf("dashboard lacks %q:\n%s", want, v)
		}
	}
	if !strings.Contains(m.KeyHints(), "w:all to two-way") {
		t.Errorf("key hints lack w: %q", m.KeyHints())
	}
	if strings.Contains(v, "Bilder sync(s)") || strings.Contains(v, "Bilder, ") {
		t.Errorf("hidden note of Bilder shown:\n%s", v)
	}
}

// w lists every mirror profile (hidden notes too) in one prompt; n changes
// nothing, y sends one sync_mode update per mirror profile.
func TestMirrorNotice_DashboardSwitchAll(t *testing.T) {
	b := mirrorDashboard(t)
	m := openDashboard(t, b)
	b.reset()

	m = dashStep(t, m, press("w"))
	v := stripANSI(m.View().Content)
	for _, want := range []string{"Switch all 2 mirror profile(s) to two-way sync?", "Dokumente (docs)", "Bilder (pics)",
		"resync", "nothing is deleted"} {
		if !strings.Contains(v, want) {
			t.Errorf("prompt lacks %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "Musik") {
		t.Errorf("two-way profile listed:\n%s", v)
	}
	m = dashStep(t, m, press("n"))
	if puts := b.matching("PUT "); len(puts) != 0 {
		t.Fatalf("cancelled switch sent %v", puts)
	}

	m = dashStep(t, m, press("w"))
	_, flashes := dashStepFlashes(m, press("y"))
	if puts := b.matching("PUT "); len(puts) != 2 || puts[0] != "PUT /profiles/docs" || puts[1] != "PUT /profiles/pics" {
		t.Errorf("puts = %v", puts)
	}
	for _, line := range []string{"PUT /profiles/docs", "PUT /profiles/pics"} {
		if bodies := b.bodiesOf(line); len(bodies) != 1 || len(bodies[0]) != 1 || bodies[0]["sync_mode"] != string(api.SyncModeTwoWay) {
			t.Errorf("%s bodies = %v", line, bodies)
		}
	}
	if len(flashes) != 1 || !strings.Contains(flashes[0], "2 profile(s) now sync two-way") {
		t.Errorf("flashes = %v", flashes)
	}
}

func TestMirrorNotice_DashboardSwitchAllReportsFailures(t *testing.T) {
	b := mirrorDashboard(t)
	b.json("PUT", "/profiles/pics", 409, map[string]any{"detail": "Profile folder overlaps another profile"})
	m := openDashboard(t, b)
	m = dashStep(t, m, press("w"))
	_, flashes := dashStepFlashes(m, press("y"))
	if len(flashes) != 1 || !strings.Contains(flashes[0], "Switched 1 profile(s) to two-way; 1 failed") || !strings.Contains(flashes[0], "pics") {
		t.Errorf("flashes = %v", flashes)
	}
}

func TestMirrorNotice_DashboardWithoutMirrorProfiles(t *testing.T) {
	b := dashboardBackend(t)
	music := profileJSON("music", "Musik", "idle")
	music["sync_mode"] = "two_way"
	b.json("GET", "/profiles", 200, []any{music})
	m := openDashboard(t, b)
	v := stripANSI(m.View().Content)
	if strings.Contains(v, "Mirror mode") || strings.Contains(v, "w:all to two-way") {
		t.Errorf("notice shown without mirror profiles:\n%s", v)
	}
	b.reset()
	_, cmd := m.Update(press("w"))
	msgs := runAll(cmd)
	if f, ok := find[ui.FlashMsg](msgs); !ok || !strings.Contains(f.Text, "already syncs two-way") {
		t.Errorf("w messages = %v", msgs)
	}
	if puts := b.matching("PUT "); len(puts) != 0 {
		t.Errorf("puts = %v", puts)
	}
}

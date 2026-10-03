package ui_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// twoWayProfileJSON is a two-way profile; paused and resync_required as given.
func twoWayProfileJSON(paused, resyncRequired bool) map[string]any {
	p := profileJSON("docs", "Dokumente", "idle")
	p["sync_mode"] = "two_way"
	p["max_delete"] = 25
	p["intervals_paused"] = paused
	p["resync_required"] = resyncRequired
	if resyncRequired {
		p["last_error"] = "Two-way sync state is missing; confirm a resync"
	} else if paused {
		p["last_error"] = "Two-way sync stopped: it would delete 60 remote files (limit 25)"
	}
	return p
}

// twoWayPreviewJSON is a sync/preview answer of a two-way profile.
func twoWayPreviewJSON() map[string]any {
	p := previewJSON(2, 3, 25)
	p["sync_mode"] = "two_way"
	p["two_way"] = map[string]any{
		"local":     map[string]any{"deletes": 1, "replaces": 2, "creates": 3, "exceeds_max_delete": false},
		"remote":    map[string]any{"deletes": 60, "replaces": 4, "creates": 5, "exceeds_max_delete": true},
		"conflicts": 2, "resync": false, "resync_required": false, "error": nil,
	}
	return p
}

func twoWayBackend(t *testing.T, paused, resyncRequired bool) *backend {
	t.Helper()
	b := detailBackend(t)
	b.json("GET", "/profiles/docs", 200, twoWayProfileJSON(paused, resyncRequired))
	b.json("POST", "/profiles/docs/sync/preview", 200, twoWayPreviewJSON())
	b.json("POST", "/profiles/docs/sync/start", 200, map[string]any{"job_id": 9, "state": "idle"})
	b.json("POST", "/profiles/docs/sync/resync", 200, map[string]any{"job_id": 10, "state": "idle"})
	b.json("PUT", "/profiles/docs", 200, profileJSON("docs", "Dokumente", "idle"))
	return b
}

func hasKey(keys []components.KeyBinding, key string) bool {
	for _, k := range keys {
		if k.Key == key {
			return true
		}
	}
	return false
}

// detailFlashes presses key and returns the flash texts the resulting
// command produced (without feeding anything back to the model).
func detailFlashes(m ui.ProfileDetailModel, key string) []string {
	_, cmd := m.Update(press(key))
	var out []string
	for _, msg := range runAll(cmd) {
		if f, ok := msg.(ui.FlashMsg); ok {
			out = append(out, f.Text)
		}
	}
	return out
}

func TestTwoWay_OverviewShowsModeAndKeys(t *testing.T) {
	b := twoWayBackend(t, false, false)
	m := openDetail(t, b)
	v := view(m)
	for _, want := range []string{"Mode: two-way: changes on either side are carried to the other", "n:sync now", "R:resync"} {
		if !strings.Contains(v, want) {
			t.Errorf("overview lacks %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "Two-way sync is available") {
		t.Errorf("two-way profile shows the switch hint:\n%s", v)
	}
	keys := m.KeyBindings()
	if !hasKey(keys, "n") || !hasKey(keys, "R") || hasKey(keys, "w") {
		t.Errorf("key bindings = %+v", keys)
	}
}

// Sync now on a two-way profile that is not paused starts at once, with
// direction two_way and without force, and needs no preview.
func TestTwoWay_SyncNowStartsWithoutForce(t *testing.T) {
	b := twoWayBackend(t, false, false)
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, press("n"))
	if m.CapturesInput() {
		t.Errorf("an unpaused Sync now must not ask:\n%s", view(m))
	}
	bodies := b.bodiesOf("POST /profiles/docs/sync/start")
	if len(bodies) != 1 {
		t.Fatalf("expected one sync start, got %v", b.log())
	}
	if bodies[0]["direction"] != "two_way" {
		t.Errorf("start body = %v, want direction two_way", bodies[0])
	}
	if _, ok := bodies[0]["force"]; ok {
		t.Errorf("force sent without confirmation: %v", bodies[0])
	}
	if len(b.matching("POST /profiles/docs/sync/preview")) != 0 {
		t.Errorf("unexpected preview: %v", b.log())
	}
}

// Sync now on a paused two-way profile previews both sides and asks; 'n'
// sends nothing, 'y' sends force=true.
func TestTwoWay_SyncNowPausedAsksThenForces(t *testing.T) {
	b := twoWayBackend(t, true, false)
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, press("n"))
	if !m.CapturesInput() {
		t.Fatalf("a paused Sync now must ask first:\n%s", view(m))
	}
	v := view(m)
	for _, want := range []string{"Sync profile \"Dokumente\" (docs) both ways now?",
		"Local folder: deletes 1, replaces 2, creates 3 file(s)",
		"Remote:       deletes 60, replaces 4, creates 5 file(s)", "more deletes than the limit of 25",
		"2 file(s) changed on both sides: both versions will be kept",
		"Delete limit: 25 files on each side", "change nothing and pause the profile",
		".omnisync-trash", "paused; this sync runs anyway", "Why they are paused: Two-way sync stopped: it would delete 60 remote files (limit 25)", "[y]es"} {
		if !strings.Contains(v, want) {
			t.Errorf("prompt lacks %q:\n%s", want, v)
		}
	}
	m = detailStep(t, m, press("n"))
	if m.CapturesInput() || len(b.matching("POST /profiles/docs/sync/start")) != 0 {
		t.Fatalf("'n' must cancel without syncing: %v", b.log())
	}
	m = detailStep(t, m, press("n"))
	_ = detailStep(t, m, press("y"))
	bodies := b.bodiesOf("POST /profiles/docs/sync/start")
	if len(bodies) != 1 || bodies[0]["direction"] != "two_way" || bodies[0]["force"] != true {
		t.Errorf("start bodies = %v, want one two_way start with force", bodies)
	}
}

// A resync preview says the run is a union and nothing is deleted; a failed
// dry run is reported.
func TestTwoWay_PromptShowsResyncAndPreviewError(t *testing.T) {
	b := twoWayBackend(t, true, false)
	p := twoWayPreviewJSON()
	tw := p["two_way"].(map[string]any)
	tw["resync"] = true
	b.json("POST", "/profiles/docs/sync/preview", 200, p)
	m := openDetail(t, b)
	m = detailStep(t, m, press("n"))
	if v := view(m); !strings.Contains(v, "This run is a resync") || !strings.Contains(v, "nothing is deleted") {
		t.Errorf("prompt lacks the resync note:\n%s", v)
	}
	m = detailStep(t, m, press("esc"))

	tw["error"] = "bisync dry run failed"
	b.json("POST", "/profiles/docs/sync/preview", 200, p)
	m = detailStep(t, m, press("n"))
	if v := view(m); !strings.Contains(v, "Could not preview the two-way sync: bisync dry run failed") {
		t.Errorf("prompt lacks the preview error:\n%s", v)
	}
}

// A paused Sync now whose preview has no two_way part (or a failed dry run)
// counts as a failed preview: 'y' sends nothing, "force" + Enter forces.
func TestTwoWay_SyncNowWithoutTwoWayPreviewNeedsTypedForce(t *testing.T) {
	for _, name := range []string{"no two_way part", "dry run failed"} {
		t.Run(name, func(t *testing.T) {
			b := twoWayBackend(t, true, false)
			p := twoWayPreviewJSON()
			if name == "no two_way part" {
				delete(p, "two_way")
			} else {
				p["two_way"].(map[string]any)["error"] = "bisync dry run failed"
			}
			b.json("POST", "/profiles/docs/sync/preview", 200, p)
			m := openDetail(t, b)
			b.reset()
			m = detailStep(t, m, press("n"))
			m = detailStep(t, m, press("y"))
			if len(b.matching("POST /profiles/docs/sync/start")) != 0 || !m.CapturesInput() {
				t.Fatalf("'y' forced a two-way sync without a preview: %v", b.log())
			}
			m = detailStep(t, m, press("backspace"))
			for _, k := range typeKeys(ui.ForceWord) {
				m = detailStep(t, m, k)
			}
			_ = detailStep(t, m, press("enter"))
			bodies := b.bodiesOf("POST /profiles/docs/sync/start")
			if len(bodies) != 1 || bodies[0]["direction"] != "two_way" || bodies[0]["force"] != true {
				t.Errorf("start bodies = %v, want one forced two_way start", bodies)
			}
		})
	}
}

// resync_required is shown prominently with last_error, and Sync now is
// refused locally (the backend would answer 409).
func TestTwoWay_ResyncRequiredShownAndSyncNowRefused(t *testing.T) {
	b := twoWayBackend(t, false, true)
	m := openDetail(t, b)
	v := view(m)
	for _, want := range []string{"Resync required: automatic two-way syncing is paused",
		"Why: Two-way sync state is missing; confirm a resync", "Press R to resync"} {
		if !strings.Contains(v, want) {
			t.Errorf("overview lacks %q:\n%s", want, v)
		}
	}
	b.reset()
	if got := detailFlashes(m, "n"); len(got) != 1 || !strings.Contains(got[0], "press R") {
		t.Errorf("flash = %v", got)
	}
	m = detailStep(t, m, press("n"))
	if len(mutations(b)) != 0 || m.CapturesInput() {
		t.Errorf("Sync now with resync_required sent %v", mutations(b))
	}
}

// Resync sits behind a confirmation; 'n' sends nothing, 'y' sends
// {"confirm": true}.
func TestTwoWay_ResyncAsksFirst(t *testing.T) {
	b := twoWayBackend(t, false, true)
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, press("R"))
	if !m.CapturesInput() {
		t.Fatalf("Resync must ask first:\n%s", view(m))
	}
	v := view(m)
	for _, want := range []string{"Resync profile \"Dokumente\" (docs)?", "union of both sides", "nothing is deleted",
		"the newer version wins", ".omnisync-trash", "automatic two-way syncing resumes",
		"Reason: Two-way sync state is missing; confirm a resync", "[y]es"} {
		if !strings.Contains(v, want) {
			t.Errorf("resync prompt lacks %q:\n%s", want, v)
		}
	}
	m = detailStep(t, m, press("n"))
	if m.CapturesInput() || len(mutations(b)) != 0 {
		t.Fatalf("'n' must cancel the resync: %v", mutations(b))
	}
	m = detailStep(t, m, press("R"))
	for _, k := range []string{"l", "p", "R", "enter"} {
		m = detailStep(t, m, press(k))
	}
	if len(mutations(b)) != 0 {
		t.Fatalf("the resync prompt was answered by another key: %v", mutations(b))
	}
	_ = detailStep(t, m, press("y"))
	bodies := b.bodiesOf("POST /profiles/docs/sync/resync")
	if len(bodies) != 1 || len(bodies[0]) != 1 || bodies[0]["confirm"] != true {
		t.Errorf("resync bodies = %v (requests %v)", bodies, b.log())
	}
	if got := mutations(b); len(got) != 1 {
		t.Errorf("mutations = %v", got)
	}
}

// Push and pull stay available on a two-way profile, as one-way overrides.
func TestTwoWay_PushIsAnOverride(t *testing.T) {
	b := twoWayBackend(t, false, false)
	m := openDetail(t, b)
	m = detailStep(t, m, press("p"))
	if v := view(m); !strings.Contains(v, "one-way override") || !strings.Contains(v, "Deletes 2 file(s) on the remote") {
		t.Errorf("push prompt:\n%s", v)
	}
}

// A mirror profile shows its mode and the two-way hint; n and R do nothing
// but say why; w switches to two-way after a confirmation.
func TestTwoWay_MirrorProfileHintAndSwitch(t *testing.T) {
	b := twoWayBackend(t, false, false)
	mirror := profileJSON("docs", "Dokumente", "idle")
	mirror["sync_mode"] = "mirror"
	b.json("GET", "/profiles/docs", 200, mirror)
	m := openDetail(t, b)
	v := view(m)
	for _, want := range []string{"Mode: mirror: one-way push/pull only", "Mirror mode:", "w: switch to two-way (asks first)", "deletes nothing", "w:switch to two-way"} {
		if !strings.Contains(v, want) {
			t.Errorf("overview lacks %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "n:sync now") {
		t.Errorf("mirror profile offers Sync now:\n%s", v)
	}
	keys := m.KeyBindings()
	if hasKey(keys, "n") || hasKey(keys, "R") || !hasKey(keys, "w") {
		t.Errorf("key bindings = %+v", keys)
	}
	b.reset()
	if got := detailFlashes(m, "n"); len(got) != 1 || !strings.Contains(got[0], "two-way profiles") {
		t.Errorf("Sync now flash = %v", got)
	}
	if got := detailFlashes(m, "R"); len(got) != 1 || !strings.Contains(got[0], "two-way profiles") {
		t.Errorf("Resync flash = %v", got)
	}
	m = detailStep(t, m, press("n"))
	m = detailStep(t, m, press("R"))
	if m.CapturesInput() || len(mutations(b)) != 0 {
		t.Fatalf("mirror profile: n/R sent %v", mutations(b))
	}

	m = detailStep(t, m, press("w"))
	if v := view(m); !strings.Contains(v, "Switch profile \"Dokumente\" (docs) to two-way sync?") || !strings.Contains(v, "resync") {
		t.Errorf("switch prompt:\n%s", v)
	}
	m = detailStep(t, m, press("n"))
	if len(mutations(b)) != 0 {
		t.Fatalf("cancelled switch sent %v", mutations(b))
	}
	m = detailStep(t, m, press("w"))
	_ = detailStep(t, m, press("y"))
	bodies := b.bodiesOf("PUT /profiles/docs")
	if len(bodies) != 1 || len(bodies[0]) != 1 || bodies[0]["sync_mode"] != "two_way" {
		t.Errorf("switch bodies = %v", bodies)
	}
}

// The syncing state renders as a busy state.
func TestTwoWay_SyncingStateIsBusy(t *testing.T) {
	b := twoWayBackend(t, false, false)
	m := ui.NewProfileDetailModel(b.client())
	m = detailStep(t, m, tea.WindowSizeMsg{Width: 120, Height: 40})
	m = detailStep(t, m, ui.OpenProfileMsg{Slug: "docs"})
	status := &api.ProfileStatusResponse{
		ProfileResponse: api.ProfileResponse{Slug: "docs", Name: "Dokumente", SyncMode: api.SyncModeTwoWay},
		State:           api.SyncStateSyncing,
	}
	// Feed the poll without running the spinner's tick commands.
	updated, _ := m.Update(ui.PollResultMsg{ViewID: ui.ViewProfileDetail, Data: ui.ProfileDetailData{Slug: "docs", Value: status}})
	m = updated.(ui.ProfileDetailModel)
	if v := view(m); !strings.Contains(v, "[SYNCING]") {
		t.Errorf("overview:\n%s", v)
	}
	if !m.PollSpec().IsActive(status) {
		t.Error("a syncing profile must poll fast")
	}
	b.reset()
	if got := detailFlashes(m, "n"); len(got) != 1 || !strings.Contains(got[0], "already running") {
		t.Errorf("flash = %v", got)
	}
	if len(b.matching("POST /profiles/docs/sync/start")) != 0 {
		t.Errorf("Sync now while syncing sent %v", b.log())
	}
}

// --- Profiles form ---

// New profiles default to two-way; the mode can be switched to mirror.
func TestTwoWay_CreateFormDefaultsToTwoWay(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("c"))
	msgs := typeKeys("Docs")
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("/home/u/Docs")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("gdrive:Docs")...)
	msgs = append(msgs, press("tab"), press("tab"))
	m = drive(t, m, msgs...)
	v := content(m)
	for _, want := range []string{"Mode:", "Two-way (recommended)", "a file changed on both sides keeps both versions", "Mirror: one-way push/pull only"} {
		if !strings.Contains(v, want) {
			t.Errorf("form lacks %q:\n%s", want, v)
		}
	}
	m = drive(t, m, press("enter"))
	if body := bodyOf(b, "POST /profiles"); body == nil || body["sync_mode"] != "two_way" {
		t.Errorf("create body = %v", body)
	}

	b.reset()
	m = drive(t, m, press("c"))
	msgs = typeKeys("Old")
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("/home/u/Old")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys("gdrive:Old")...)
	msgs = append(msgs, press("tab"), press("tab"), press("right"))
	m = drive(t, m, msgs...)
	if !strings.Contains(content(m), "Mirror (one-way push/pull only)") {
		t.Errorf("mode not switched:\n%s", content(m))
	}
	_ = drive(t, m, press("enter"))
	if body := bodyOf(b, "POST /profiles"); body == nil || body["sync_mode"] != "mirror" {
		t.Errorf("create body = %v", body)
	}
}

// The edit form shows the profile's mode and sends sync_mode only when the
// user changed it (switching to two-way makes the next sync a resync).
func TestTwoWay_EditSendsModeOnlyWhenChanged(t *testing.T) {
	b := profilesBackend(t)
	docs := profileJSON("docs", "Dokumente", "idle")
	docs["sync_mode"] = "mirror"
	b.json("GET", "/profiles", 200, []any{docs})
	m := open(t, ui.NewProfilesModel(b.client()))
	if !strings.Contains(content(m), "mirror") {
		t.Errorf("list lacks the mode:\n%s", content(m))
	}
	m = drive(t, m, press("e"), press("enter"))
	body := bodyOf(b, "PUT /profiles/docs")
	if body == nil {
		t.Fatalf("no update: %v", b.log())
	}
	if _, ok := body["sync_mode"]; ok {
		t.Errorf("unchanged mode sent: %v", body)
	}

	b.reset()
	m = drive(t, m, press("e"))
	m = drive(t, m, press("tab"), press("tab"), press("tab"), press("tab"))
	if !strings.Contains(content(m), "Mirror (one-way push/pull only)") {
		t.Errorf("edit form does not show the profile's mode:\n%s", content(m))
	}
	_ = drive(t, m, press("right"), press("enter"))
	if body := bodyOf(b, "PUT /profiles/docs"); body == nil || body["sync_mode"] != "two_way" {
		t.Errorf("switch body = %v", body)
	}
}

func TestTwoWay_ProfilesListShowsResyncRequired(t *testing.T) {
	b := profilesBackend(t)
	b.json("GET", "/profiles", 200, []any{twoWayProfileJSON(false, true)})
	m := open(t, ui.NewProfilesModel(b.client()))
	v := content(m)
	for _, want := range []string{"two-way", "resync required", "Two-way sync state is missing", "press R"} {
		if !strings.Contains(v, want) {
			t.Errorf("list lacks %q:\n%s", want, v)
		}
	}
}

// --- Dashboard ---

func TestTwoWay_DashboardWarnsResyncRequired(t *testing.T) {
	b := dashboardBackend(t)
	b.json("GET", "/sync/status/aggregate", 200, map[string]any{
		"overall_state": "syncing", "total_pending_changes": 0, "paused_profiles": []any{},
		"profiles_summary": []any{map[string]any{"slug": "docs", "name": "Dokumente", "state": "idle", "last_sync": nil,
			"pending_changes": 0, "intervals_paused": false, "last_error": "state lost", "resync_required": true}},
	})
	m := openDashboard(t, b)
	v := stripANSI(m.View().Content)
	for _, want := range []string{"[SYNCING]", "Dokumente: resync required", "(state lost)", "press R"} {
		if !strings.Contains(v, want) {
			t.Errorf("dashboard lacks %q:\n%s", want, v)
		}
	}
	if !m.PollSpec().IsActive(&api.AggregateStatusResponse{OverallState: api.SyncStateSyncing}) {
		t.Error("the dashboard must poll fast while a two-way sync runs")
	}
}

// --- Jobs ---

func TestTwoWay_JobsShowDirectionAndSide(t *testing.T) {
	b := newBackend(t)
	jobs := jobsJSON(2, 100)
	jobs[0].(map[string]any)["direction"] = "two_way"
	jobs[1].(map[string]any)["direction"] = "resync"
	b.json("GET", "/jobs", 200, jobs)
	b.json("GET", "/profiles", 200, []any{})
	b.json("GET", "/jobs/100", 200, jobs[0])
	b.json("GET", "/jobs/100/files", 200, []any{
		map[string]any{"id": 1, "job_id": 100, "file_path": "a.txt", "action": "modified", "size_bytes": 3, "side": "local"},
		map[string]any{"id": 2, "job_id": 100, "file_path": "b.txt", "action": "deleted", "size_bytes": nil, "side": "remote"},
		map[string]any{"id": 3, "job_id": 100, "file_path": "c.txt", "action": "created", "size_bytes": 1, "side": nil},
	})
	m := open(t, ui.NewJobsModel(b.client()))
	v := content(m)
	if !strings.Contains(v, "two-way") || !strings.Contains(v, "resync") {
		t.Errorf("jobs list:\n%s", v)
	}
	m = drive(t, m, press("enter"))
	v = content(m)
	for _, want := range []string{"Direction: two-way", "Side", "local", "remote"} {
		if !strings.Contains(v, want) {
			t.Errorf("job detail lacks %q:\n%s", want, v)
		}
	}
}

// --- Conflicts ---

func twoWayConflictsBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/conflicts", 200, []any{
		map[string]any{"id": 4, "job_id": 42, "file_path": "docs/plan.odt", "local_modified": "2026-09-27T08:00:00Z",
			"remote_modified": "2026-09-27T09:45:30Z", "resolved": false, "resolution": nil, "profile_slug": "docs",
			"profile_name": "Dokumente", "local_kept_as": "docs/plan.local-conflict1.odt", "remote_kept_as": "docs/plan.odt"},
	})
	b.json("POST", "/conflicts/4/resolve", 200, map[string]any{"id": 4, "job_id": 42, "file_path": "docs/plan.odt", "resolved": true})
	return b
}

func TestTwoWay_ConflictWordingAndActions(t *testing.T) {
	b := twoWayConflictsBackend(t)
	m := open(t, ui.NewConflictsModel(b.client()))
	kept := "Both versions were kept: local version as docs/plan.local-conflict1.odt, remote version as docs/plan.odt"
	if v := content(m); !strings.Contains(v, kept) {
		t.Errorf("list lacks the kept-as sentence:\n%s", v)
	}
	m = drive(t, m, press("enter"))
	v := content(m)
	for _, want := range []string{kept, "Keep only local:", "keep only the local version, as docs/plan.odt",
		"Keep only remote:", "keep only the remote version", "Keep both:", "both files stay", "Dismiss:"} {
		if !strings.Contains(v, want) {
			t.Errorf("choices lack %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "conflict-<time>") || strings.Contains(v, "copy the local file over") {
		t.Errorf("two-way conflict shows the diff-conflict wording:\n%s", v)
	}
	for key, want := range map[string]string{
		"l": "the other copy (docs/plan.odt) is moved to .omnisync-trash",
		"r": "the other copy (docs/plan.local-conflict1.odt) is moved to .omnisync-trash",
		"b": "keep both files on both sides",
		"d": "close this conflict without changing any file",
	} {
		m = drive(t, m, press(key))
		if v := content(m); !strings.Contains(v, want) {
			t.Errorf("%s: confirmation lacks %q:\n%s", key, want, v)
		}
		m = drive(t, m, press("n"))
		m = drive(t, m, press("enter"))
	}
	if len(b.matching("POST")) != 0 {
		t.Fatalf("cancelled confirmations sent %v", b.matching("POST"))
	}
	m = drive(t, m, press("l"), press("y"))
	if bodies := b.bodiesOf("POST /conflicts/4/resolve"); len(bodies) != 1 || bodies[0]["resolution"] != "keep_local" {
		t.Errorf("resolve bodies = %v", bodies)
	}
	_ = m
}

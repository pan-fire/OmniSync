package ui_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"sync"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func profileJSON(slug, name, state string) map[string]any {
	return map[string]any{
		"id": 1, "slug": slug, "name": name, "local_dir": "/home/u/" + slug, "remote_dir": "gdrive:" + slug,
		"debounce_seconds": 5, "pull_interval_minutes": 5, "rclone_filter": []string{}, "rclone_args": []string{},
		"backup_dir": nil, "max_retries": 3, "enabled": true, "created_at": "2026-09-27T08:00:00Z",
		"updated_at": "2026-09-27T08:00:00Z", "state": state, "last_sync": "2026-09-27T08:30:00Z",
		"current_job_id": nil, "files_processed": 4, "errors": 0, "pending_changes": 2,
		"intervals_paused": false, "paused_at": nil, "last_error": nil, "max_delete": 50,
	}
}

// previewJSON is a sync/preview answer: a push deletes pushDeletes remote
// files, a pull deletes pullDeletes local ones.
func previewJSON(pushDeletes, pullDeletes int, maxDelete any) map[string]any {
	exceeds := func(n int) bool { limit, ok := maxDelete.(int); return ok && n > limit }
	return map[string]any{
		"push":     map[string]any{"deletes": pushDeletes, "replaces": 1, "creates": 3, "exceeds_max_delete": exceeds(pushDeletes)},
		"pull":     map[string]any{"deletes": pullDeletes, "replaces": 4, "creates": 2, "exceeds_max_delete": exceeds(pullDeletes)},
		"excluded": 0, "max_delete": maxDelete, "error": nil,
	}
}

func detailBackend(t *testing.T) *backend {
	t.Helper()
	b := newBackend(t)
	b.json("GET", "/profiles/docs", 200, profileJSON("docs", "Dokumente", "idle"))
	b.json("POST", "/profiles/docs/sync/preview", 200, previewJSON(2, 3, 25))
	b.json("POST", "/profiles/docs/sync/start", 200, map[string]any{"job_id": 9, "state": "idle"})
	return b
}

// mutations returns the recorded requests other than reads and the preview.
func mutations(b *backend) []string {
	var out []string
	for _, r := range b.log() {
		if strings.HasPrefix(r, "GET ") || strings.HasSuffix(r, "/sync/preview") {
			continue
		}
		out = append(out, r)
	}
	return out
}

// detailStep feeds msg to the model and runs the resulting commands, feeding
// their messages back, until nothing is left (bounded).
func detailStep(t *testing.T, m ui.ProfileDetailModel, msg tea.Msg) ui.ProfileDetailModel {
	t.Helper()
	queue := []tea.Msg{msg}
	for i := 0; i < 50 && len(queue) > 0; i++ {
		next := queue[0]
		queue = queue[1:]
		switch next.(type) {
		case ui.FlashMsg, ui.NavigateMsg:
			continue
		}
		updated, cmd := m.Update(next)
		m = updated.(ui.ProfileDetailModel)
		queue = append(queue, runAll(cmd)...)
	}
	return m
}

func openDetail(t *testing.T, b *backend) ui.ProfileDetailModel {
	t.Helper()
	m := ui.NewProfileDetailModel(b.client())
	m = detailStep(t, m, tea.WindowSizeMsg{Width: 120, Height: 40})
	m = detailStep(t, m, ui.OpenProfileMsg{Slug: "docs"})
	updated, cmd := m.Update(nil)
	m = updated.(ui.ProfileDetailModel)
	_ = cmd
	for _, msg := range runAll(m.Init()) {
		m = detailStep(t, m, msg)
	}
	return m
}

func view(m ui.ProfileDetailModel) string {
	return stripANSI(m.View().Content)
}

// 'p' then 'n' sends no sync request. The only request is the
// side-effect-free preview that produces the counts shown in the prompt.
func TestProfileDetail_PushThenNoSendsNoSyncRequest(t *testing.T) {
	b := detailBackend(t)
	m := openDetail(t, b)
	b.reset()

	m = detailStep(t, m, press("p"))
	if !m.CapturesInput() {
		t.Fatal("the push prompt must own the keyboard")
	}
	v := view(m)
	for _, want := range []string{"Push", "Dokumente", "docs", "local /home/u/docs", "remote gdrive:docs",
		"Deletes 2 file(s) on the remote", "Replaces 1 changed file(s) on the remote", "Uploads 3 new file(s)",
		".omnisync-trash", "Delete limit: 25 files", "stops at the limit", "[y]es"} {
		if !strings.Contains(v, want) {
			t.Errorf("prompt lacks %q:\n%s", want, v)
		}
	}
	if strings.Contains(v, "50") {
		t.Errorf("prompt shows the old fixed limit:\n%s", v)
	}

	m = detailStep(t, m, press("n"))
	if m.CapturesInput() {
		t.Error("prompt still open after 'n'")
	}
	for _, r := range b.log() {
		if r != "POST /profiles/docs/sync/preview" {
			t.Errorf("unexpected request after p, n: %s", r)
		}
	}
	if len(b.matching("POST /profiles/docs/sync/check")) != 0 {
		t.Error("the confirmation must not use sync/check (it changes the pending count and pause state)")
	}
}

func TestProfileDetail_PullPromptCountsLocalDeletes(t *testing.T) {
	b := detailBackend(t)
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, press("l"))
	v := view(m)
	for _, want := range []string{"Pull", "Deletes 3 local file(s)", "Replaces 4 changed local file(s)", "Downloads 2 new file(s)",
		"remote gdrive:docs", "Delete limit: 25 files"} {
		if !strings.Contains(v, want) {
			t.Errorf("pull prompt lacks %q:\n%s", want, v)
		}
	}
	m = detailStep(t, m, press("esc"))
	if m.CapturesInput() || len(mutations(b)) != 0 {
		t.Errorf("Esc must cancel without syncing: %v", mutations(b))
	}
}

// max_delete null means the profile has no delete limit.
func TestProfileDetail_PromptWithoutDeleteLimit(t *testing.T) {
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/sync/preview", 200, previewJSON(2, 3, nil))
	m := openDetail(t, b)
	m = detailStep(t, m, press("p"))
	v := view(m)
	if !strings.Contains(v, "no delete limit") || strings.Contains(v, "Delete limit:") {
		t.Errorf("prompt:\n%s", v)
	}
}

// A push over the limit warns that it stops at the limit.
func TestProfileDetail_PromptWarnsWhenLimitExceeded(t *testing.T) {
	b := detailBackend(t)
	preview := previewJSON(120, 3, 25)
	preview["error"] = "partial listing"
	preview["excluded"] = 4
	b.json("POST", "/profiles/docs/sync/preview", 200, preview)
	m := openDetail(t, b)
	m = detailStep(t, m, press("p"))
	v := view(m)
	for _, want := range []string{"Deletes 120 file(s)", "would delete 120 files, more than the limit of 25",
		"stop at the limit", "delete no more files", "The comparison reported a problem: partial listing",
		"Leaves out 4 differing file(s)"} {
		if !strings.Contains(v, want) {
			t.Errorf("prompt lacks %q:\n%s", want, v)
		}
	}
	// The pull of the same preview stays under the limit: no warning.
	m = detailStep(t, m, press("n"))
	m = detailStep(t, m, press("l"))
	if strings.Contains(view(m), "more than the limit") {
		t.Errorf("pull prompt warns although it is under the limit:\n%s", view(m))
	}
}

// When the preview fails, the prompt says so and shows the profile's limit.
func TestProfileDetail_PreviewFailureUsesProfileLimit(t *testing.T) {
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/sync/preview", 404, map[string]any{"detail": "Profile 'docs' is not running"})
	m := openDetail(t, b)
	m = detailStep(t, m, press("p"))
	v := view(m)
	if !strings.Contains(v, "Could not count the files") || !strings.Contains(v, "not running") || !strings.Contains(v, "Delete limit: 50 files") {
		t.Errorf("prompt:\n%s", v)
	}
}

// A failed or incomplete preview: 'y' sends nothing; only typing "force"
// and Enter starts the forced sync (the web UI asks for a checkbox).
func TestProfileDetail_FailedPreviewNeedsTypedForce(t *testing.T) {
	incomplete := previewJSON(2, 3, 25)
	incomplete["error"] = "partial listing"
	cases := map[string]func(b *backend){
		"request failed": func(b *backend) {
			b.json("POST", "/profiles/docs/sync/preview", 500, map[string]any{"detail": "Could not list the remote folder."})
		},
		"comparison problem": func(b *backend) { b.json("POST", "/profiles/docs/sync/preview", 200, incomplete) },
	}
	for name, setup := range cases {
		t.Run(name, func(t *testing.T) {
			b := detailBackend(t)
			setup(b)
			m := openDetail(t, b)
			b.reset()
			m = detailStep(t, m, press("p"))
			if v := view(m); !strings.Contains(v, `Type "force" and press Enter to sync without a preview`) {
				t.Errorf("prompt does not ask for the acknowledgement:\n%s", v)
			}
			for _, k := range []string{"y", "enter"} {
				m = detailStep(t, m, press(k))
			}
			if len(mutations(b)) != 0 || !m.CapturesInput() {
				t.Fatalf("'y' forced a sync without a preview: %v", mutations(b))
			}
			m = detailStep(t, m, press("backspace"))
			for _, k := range typeKeys(ui.ForceWord) {
				m = detailStep(t, m, k)
			}
			_ = detailStep(t, m, press("enter"))
			bodies := b.bodiesOf("POST /profiles/docs/sync/start")
			if len(bodies) != 1 || bodies[0]["direction"] != "push" || bodies[0]["force"] != true {
				t.Errorf("start bodies = %v, want one forced push", bodies)
			}
		})
	}
}

// Esc cancels the acknowledgement prompt without syncing.
func TestProfileDetail_FailedPreviewEscCancels(t *testing.T) {
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/sync/preview", 500, map[string]any{"detail": "boom"})
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, press("l"))
	for _, k := range typeKeys("for") {
		m = detailStep(t, m, k)
	}
	m = detailStep(t, m, press("esc"))
	if m.CapturesInput() || len(mutations(b)) != 0 {
		t.Errorf("Esc must cancel without syncing: %v", mutations(b))
	}
}

// Esc while the preview runs cancels; its late answer opens no prompt.
func TestProfileDetail_EscDuringPreviewCancels(t *testing.T) {
	b := detailBackend(t)
	m := openDetail(t, b)
	b.reset()
	updated, previewCmd := m.Update(press("p"))
	m = updated.(ui.ProfileDetailModel)
	if !m.CapturesInput() || !strings.Contains(view(m), "Esc: cancel") {
		t.Fatalf("no cancellable progress:\n%s", view(m))
	}
	m = detailStep(t, m, press("esc"))
	for _, msg := range runAll(previewCmd) {
		m = detailStep(t, m, msg)
	}
	if m.CapturesInput() {
		t.Errorf("prompt opened after cancel:\n%s", view(m))
	}
	m = detailStep(t, m, press("y"))
	if len(mutations(b)) != 0 {
		t.Errorf("requests after cancel: %v", mutations(b))
	}
	_ = m
}

func TestProfileDetail_PushThenYesStartsOneForcedSync(t *testing.T) {
	b := detailBackend(t)
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, press("p"))
	_ = detailStep(t, m, press("y"))
	bodies := b.bodiesOf("POST /profiles/docs/sync/start")
	if len(bodies) != 1 {
		t.Fatalf("expected one sync start, got %v", b.log())
	}
	if bodies[0]["direction"] != "push" || bodies[0]["force"] != true {
		t.Errorf("start body = %v, want direction push, force true", bodies[0])
	}
}

// A paused profile refuses a start without force (409). The confirmed push
// sends force=true and succeeds.
func TestProfileDetail_PausedProfileConfirmedPushForces(t *testing.T) {
	b := detailBackend(t)
	paused := profileJSON("docs", "Dokumente", "idle")
	paused["intervals_paused"] = true
	paused["pending_changes"] = 7
	b.json("GET", "/profiles/docs", 200, paused)
	var accepted, refused int
	var mu sync.Mutex
	b.handle("POST", "/profiles/docs/sync/start", func(w http.ResponseWriter, body map[string]any) {
		mu.Lock()
		defer mu.Unlock()
		if body["force"] != true {
			refused++
			w.WriteHeader(409)
			_ = json.NewEncoder(w).Encode(map[string]any{"detail": "Sync intervals paused due to 7 unresolved differences. Pass force=true to override."})
			return
		}
		accepted++
		_ = json.NewEncoder(w).Encode(map[string]any{"job_id": 11, "state": "idle"})
	})
	m := openDetail(t, b)
	b.reset()
	m = detailStep(t, m, press("p"))
	if v := view(m); !strings.Contains(v, "paused") || !strings.Contains(v, "runs anyway") {
		t.Errorf("prompt does not mention the pause:\n%s", v)
	}
	updated, cmd := m.Update(press("y"))
	m = updated.(ui.ProfileDetailModel)
	var outcome *ui.ActionResultMsg
	for _, msg := range runAll(cmd) {
		if confirm, ok := msg.(components.ConfirmResultMsg); ok {
			updated, cmd := m.Update(confirm)
			m = updated.(ui.ProfileDetailModel)
			if res, ok := find[ui.ActionResultMsg](runAll(cmd)); ok {
				outcome = &res
			}
		}
	}
	if outcome == nil || outcome.Action != "sync_done" || outcome.Err != nil {
		t.Fatalf("sync outcome = %+v", outcome)
	}
	mu.Lock()
	defer mu.Unlock()
	if accepted != 1 || refused != 0 {
		t.Errorf("accepted %d, refused %d", accepted, refused)
	}
	if got := mutations(b); len(got) != 1 || got[0] != "POST /profiles/docs/sync/start" {
		t.Errorf("mutations = %v", got)
	}
}

// Other keys do not answer the prompt.
func TestProfileDetail_PromptIgnoresOtherKeys(t *testing.T) {
	b := detailBackend(t)
	m := openDetail(t, b)
	m = detailStep(t, m, press("p"))
	for _, k := range []string{"l", "p", "1", "q", "enter"} {
		m = detailStep(t, m, press(k))
	}
	if !m.CapturesInput() || len(b.matching("POST /profiles/docs/sync/start")) != 0 {
		t.Error("prompt answered by a key other than y/n/Esc")
	}
}

// Opening another profile shows that profile and syncs it, not the
// first one.
func TestProfileDetail_OpenAnotherProfile(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/profiles/pics", 200, profileJSON("pics", "Bilder", "idle"))
	b.json("POST", "/profiles/pics/sync/preview", 200, previewJSON(0, 0, 50))
	m := openDetail(t, b)
	m = detailStep(t, m, ui.OpenProfileMsg{Slug: "pics"})
	for _, msg := range runAll(m.Init()) {
		m = detailStep(t, m, msg)
	}
	if m.Slug() != "pics" || !strings.Contains(view(m), "Bilder") {
		t.Fatalf("detail shows %q", view(m))
	}
	b.reset()
	m = detailStep(t, m, press("p"))
	_ = detailStep(t, m, press("y"))
	if len(b.matching("POST /profiles/pics/sync/start")) != 1 || len(b.matching("POST /profiles/docs/")) != 0 {
		t.Errorf("push went to the wrong profile: %v", b.log())
	}
}

// A slow answer for the previous profile must not show up for the new one.
func TestProfileDetail_DropsStaleData(t *testing.T) {
	b := detailBackend(t)
	m := openDetail(t, b)
	m = detailStep(t, m, ui.OpenProfileMsg{Slug: "pics"})
	stale := &api.ProfileStatusResponse{ProfileResponse: api.ProfileResponse{Slug: "docs", Name: "Dokumente"}}
	m = detailStep(t, m, ui.PollResultMsg{ViewID: ui.ViewProfileDetail, Data: ui.ProfileDetailData{Slug: "docs", Value: stale}})
	if strings.Contains(view(m), "Dokumente") {
		t.Error("stale profile data shown")
	}
}

// Item 8: the error state shows last_error, which GET /profiles/{slug}
// carries; no separate status lookup.
func TestProfileDetail_ShowsLastError(t *testing.T) {
	b := detailBackend(t)
	p := profileJSON("docs", "Dokumente", "error")
	p["last_error"] = "Sync stopped: it would delete 120 files"
	p["max_delete"] = 25
	b.json("GET", "/profiles/docs", 200, p)
	m := openDetail(t, b)
	v := view(m)
	if !strings.Contains(v, "Last error: Sync stopped: it would delete 120 files") {
		t.Errorf("last error missing:\n%s", v)
	}
	if !strings.Contains(v, "Delete limit: 25 files per sync") {
		t.Errorf("delete limit missing:\n%s", v)
	}
	if got := b.matching("GET /profiles/docs/sync/status"); len(got) != 0 {
		t.Errorf("extra status lookups: %v", got)
	}
}

func TestProfileDetail_DiffLoadsAllFilesWithPagination(t *testing.T) {
	b := detailBackend(t)
	files := []map[string]any{}
	for i := 0; i < 150; i++ {
		files = append(files, map[string]any{"path": "f" + string(rune('a'+i%26)) + "/" + strings.Repeat("x", i%5) + ".txt", "category": "local_only", "local_size": 10})
	}
	b.json("POST", "/profiles/docs/diff", 200, map[string]any{"files": files, "summary": map[string]any{"total": 150, "local_only": 150}})
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	if reqs := b.matching("POST /profiles/docs/diff"); len(reqs) != 1 || !strings.Contains(reqs[0], "limit=0") {
		t.Fatalf("diff request = %v", reqs)
	}
	if !strings.Contains(view(m), "Total: 150") || !strings.Contains(view(m), "Page 1 of 8") {
		t.Errorf("diff view:\n%s", view(m))
	}
}

func TestProfileDetail_BackupDeleteConfirmsFirst(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/profiles/docs/backups", 200, []any{
		map[string]any{"id": 3, "profile_id": 1, "name": "Nightly", "target_path": "/backups", "target_type": "local",
			"remote_name": nil, "retention_days": 7, "frequency_hours": 24, "backup_mode": "mirror", "enabled": true,
			"created_at": "x", "updated_at": "x"},
		map[string]any{"id": 4, "profile_id": 1, "name": "Weekly", "target_path": "/weekly", "target_type": "local",
			"remote_name": nil, "retention_days": 7, "frequency_hours": 168, "backup_mode": "archive", "enabled": true,
			"created_at": "x", "updated_at": "x"},
	})
	b.json("DELETE", "/profiles/docs/backups/4", 204, nil)
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	m = detailStep(t, m, press("right"))
	if !strings.Contains(view(m), "Nightly") || !strings.Contains(view(m), "mirror") {
		t.Fatalf("backups view:\n%s", view(m))
	}
	m = detailStep(t, m, press("down"))
	b.reset()
	m = detailStep(t, m, press("d"))
	if len(b.matching("DELETE")) != 0 {
		t.Fatal("deleted before confirmation")
	}
	// A refresh while the prompt is open must not change the target.
	m = detailStep(t, m, ui.PollResultMsg{ViewID: ui.ViewProfileDetail, Data: ui.ProfileDetailData{Slug: "docs", Value: []api.BackupTargetResponse{
		{ID: 4, Name: "Weekly"}, {ID: 3, Name: "Nightly"},
	}}})
	_ = detailStep(t, m, press("y"))
	if got := b.matching("DELETE"); len(got) != 1 || got[0] != "DELETE /profiles/docs/backups/4?confirm=true" {
		t.Errorf("delete requests = %v", got)
	}
}

func TestProfileDetail_OverdueBackupIsShown(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/profiles/docs/backups", 200, []any{
		map[string]any{"id": 3, "profile_id": 1, "name": "Nightly", "target_path": "/backups", "target_type": "local",
			"remote_name": nil, "retention_days": 7, "keep_last": 3, "frequency_hours": 24, "backup_mode": "mirror",
			"enabled": true, "overdue": true, "last_backup_status": "failed", "created_at": "x", "updated_at": "x"},
	})
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	m = detailStep(t, m, press("right"))
	if !strings.Contains(view(m), "OVERDUE") || !strings.Contains(view(m), "overdue, no backup completed") {
		t.Errorf("backups view:\n%s", view(m))
	}
}

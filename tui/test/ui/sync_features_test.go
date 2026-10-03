package ui_test

import (
	"net/http"
	"reflect"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

var progressJSON = map[string]any{
	"bytes": 52428800, "total_bytes": 209715200, "speed": 2097152.0, "eta_seconds": 75,
	"files_done": 3, "files_total": 10, "checks": 0, "total_checks": 0,
	"current_files": []any{map[string]any{"name": "Fotos/a.jpg", "size": 1048576, "bytes": 524288, "percentage": 50}},
}

// The dashboard shows a running sync's progress, says who paused a
// profile, and pauses / resumes all with z / u.
func TestDashboard_ProgressAndPauseAll(t *testing.T) {
	b := dashboardBackend(t)
	b.json("GET", "/sync/status/aggregate", 200, map[string]any{
		"overall_state": "pushing", "total_pending_changes": 0,
		"paused_profiles": []any{map[string]any{"slug": "pics", "name": "Bilder", "pending_changes": 0, "paused_at": nil, "user_paused": true}},
		"profiles_summary": []any{
			map[string]any{"slug": "docs", "name": "Dokumente", "state": "pushing", "last_sync": nil, "pending_changes": 0,
				"intervals_paused": false, "progress": progressJSON},
		},
	})
	b.json("POST", "/profiles/pause-all", 200, map[string]any{"changed": []string{"docs"}, "unchanged": []string{}, "still_paused": map[string]string{}})
	b.json("POST", "/profiles/resume-all", 200, map[string]any{"changed": []string{"pics"}, "unchanged": []string{}, "still_paused": map[string]string{"pics": "restore"}})
	m := open(t, ui.NewDashboardModel(b.client()))
	v := content(m)
	for _, want := range []string{"Dokumente:", "25%", "50.0 MB of 200.0 MB", "3/10 files", "2.0 MB/s", "1m15s left", "Bilder: paused by you"} {
		if !strings.Contains(v, want) {
			t.Errorf("dashboard lacks %q:\n%s", want, v)
		}
	}
	if !strings.Contains(m.KeyHints(), "z:pause all") {
		t.Errorf("hints: %s", m.KeyHints())
	}
	b.reset()
	m = dashStep(t, m, press("z"))
	m = dashStep(t, m, press("u"))
	if got := b.matching("POST /profiles/"); !reflect.DeepEqual(got, []string{"POST /profiles/pause-all", "POST /profiles/resume-all"}) {
		t.Errorf("requests = %v", got)
	}
	_ = m
}

func trashBackend(t *testing.T) *backend {
	b := detailBackend(t)
	p := profileJSON("docs", "Dokumente", "pushing")
	p["progress"] = progressJSON
	p["user_paused"] = true
	p["intervals_paused"] = true
	p["pending_changes"] = 0
	p["bwlimit"] = "08:00,512k 23:00,off"
	p["sync_window"] = map[string]any{"days": []int{0, 1, 2, 3, 4}, "start": "22:00", "end": "06:00"}
	p["outside_sync_window"] = true
	p["waiting_for_window"] = true
	p["next_window_start"] = "2026-10-02T22:00:00+02:00"
	b.json("GET", "/profiles/docs", 200, p)
	b.json("POST", "/profiles/docs/sync/pause", 200, map[string]any{"detail": "Automatic syncing paused."})
	b.handle("GET", "/profiles/docs/trash", func(w http.ResponseWriter, _ map[string]any) {
		_, _ = w.Write([]byte(`{"side":"local","total_files":2,"total_bytes":3072,"truncated":false,"entries":[
			{"id":"T1/a.txt","folder":"T1","path":"a.txt","size":1024,"modified":null,"trashed_at":"2026-10-01T10:00:00Z"},
			{"id":"T1/b.txt","folder":"T1","path":"b.txt","size":2048,"modified":null,"trashed_at":"2026-10-01T10:00:00Z"}]}`))
	})
	restores := 0
	b.handle("POST", "/profiles/docs/trash/restore", func(w http.ResponseWriter, body map[string]any) {
		restores++
		if restores == 1 {
			_, _ = w.Write([]byte(`{"done":["T1/b.txt"],"failed":[{"id":"T1/a.txt","code":"target_newer","message":"newer"}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"done":["T1/a.txt"],"failed":[]}`))
	})
	b.json("POST", "/profiles/docs/trash/delete", 200, map[string]any{"done": []string{"T1/a.txt"}, "failed": []any{}})
	return b
}

// The overview shows progress, the bandwidth limit, the sync window and the
// user's pause; z pauses.
func TestProfileDetail_ProgressWindowAndPause(t *testing.T) {
	b := trashBackend(t)
	m := openDetail(t, b)
	v := view(m)
	for _, want := range []string{"Progress:", "25%", "3/10 files", "Fotos/a.jpg", "Bandwidth limit: 08:00,512k 23:00,off",
		"Sync window: Mon,Tue,Wed,Thu,Fri 22:00-06:00", "Outside the sync window: a sync waits", "Paused by you", "z:pause"} {
		if !strings.Contains(v, want) {
			t.Errorf("overview lacks %q:\n%s", want, v)
		}
	}
	b.reset()
	_ = detailStep(t, m, press("z"))
	if got := b.matching("POST "); len(got) != 1 || got[0] != "POST /profiles/docs/sync/pause" {
		t.Errorf("requests = %v", got)
	}
}

// The Trash tab lists the trash, restores (asking before replacing a newer
// file) and deletes only after confirming.
func TestProfileDetail_TrashTab(t *testing.T) {
	b := trashBackend(t)
	b.json("GET", "/profiles/docs", 200, profileJSON("docs", "Dokumente", "idle"))
	m := openDetail(t, b)
	for i := 0; i < 4; i++ {
		m = detailStep(t, m, press("right"))
	}
	v := view(m)
	for _, want := range []string{"Trash of the local folder", "2 file(s), 3.0 KB", "a.txt", "b.txt", "u:restore"} {
		if !strings.Contains(v, want) {
			t.Fatalf("trash tab lacks %q:\n%s", want, v)
		}
	}
	b.reset()
	m = detailStep(t, m, press("a"))
	m = detailStep(t, m, press("u"))
	if !m.CapturesInput() || !strings.Contains(view(m), "newer at their original place") {
		t.Fatalf("no overwrite prompt:\n%s", view(m))
	}
	m = detailStep(t, m, press("y"))
	bodies := b.bodiesOf("POST /profiles/docs/trash/restore")
	if len(bodies) != 2 {
		t.Fatalf("restore bodies = %v", bodies)
	}
	if ids := bodies[0]["ids"].([]any); len(ids) != 2 || bodies[0]["overwrite"] != nil {
		t.Errorf("first restore = %v", bodies[0])
	}
	if ids := bodies[1]["ids"].([]any); len(ids) != 1 || ids[0] != "T1/a.txt" || bodies[1]["overwrite"] != true {
		t.Errorf("overwrite restore = %v", bodies[1])
	}

	m = detailStep(t, m, press("d"))
	if !strings.Contains(view(m), "for good") {
		t.Fatalf("no delete prompt:\n%s", view(m))
	}
	m = detailStep(t, m, press("n"))
	if len(b.matching("POST /profiles/docs/trash/delete")) != 0 {
		t.Fatal("deleted without confirmation")
	}
	m = detailStep(t, m, press("d"))
	m = detailStep(t, m, press("y"))
	if got := b.bodiesOf("POST /profiles/docs/trash/delete"); len(got) != 1 || got[0]["side"] != "local" {
		t.Errorf("delete = %v", got)
	}
	b.reset()
	_ = detailStep(t, m, press("v"))
	if got := b.matching("GET /profiles/docs/trash"); len(got) != 1 || got[0] != "GET /profiles/docs/trash?side=remote" {
		t.Errorf("side switch requests = %v", got)
	}
}

// The profile form sends the bandwidth limit and the sync window, and
// refuses a window it cannot read.
func TestProfileForm_BandwidthAndWindow(t *testing.T) {
	b := profilesBackend(t)
	m := open(t, ui.NewProfilesModel(b.client()))
	m = drive(t, m, press("c"))
	m = drive(t, m, typeKeys("Docs")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("/home/u/Docs")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("gdrive:Docs")...)
	for i := 0; i < 7; i++ {
		m = drive(t, m, press("tab"))
	}
	m = drive(t, m, typeKeys("1M")...)
	m = drive(t, m, press("tab"))
	m = drive(t, m, typeKeys("Mon-Fri 22:0-06:00")...)
	m = drive(t, m, press("enter"))
	if bodyOf(b, "POST /profiles") != nil || !strings.Contains(content(m), "HH:MM-HH:MM") {
		t.Fatalf("an unreadable window was sent:\n%s", content(m))
	}
	for i := 0; i < len("22:0-06:00"); i++ {
		m = drive(t, m, press("backspace"))
	}
	m = drive(t, m, typeKeys("22:00-06:00")...)
	_ = drive(t, m, press("enter"))
	body := bodyOf(b, "POST /profiles")
	if body == nil || body["bwlimit"] != "1M" {
		t.Fatalf("body = %v", body)
	}
	w, _ := body["sync_window"].(map[string]any)
	if w == nil || w["start"] != "22:00" || w["end"] != "06:00" || len(w["days"].([]any)) != 5 {
		t.Errorf("sync_window = %v", body["sync_window"])
	}
}

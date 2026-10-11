package ui_test

import (
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// idleTrashBackend is trashBackend with the profile idle (trashBackend's
// profile is pushing).
func idleTrashBackend(t *testing.T) *backend {
	t.Helper()
	b := trashBackend(t)
	b.json("GET", "/profiles/docs", 200, profileJSON("docs", "Dokumente", "idle"))
	return b
}

// openTrash opens Profile Detail on the Trash tab (the fifth).
func openTrash(t *testing.T, b *backend) ui.ProfileDetailModel {
	t.Helper()
	m := openDetail(t, b)
	for i := 0; i < 4; i++ {
		m = detailStep(t, m, press("right"))
	}
	return m
}

// Deleting from the trash cannot be undone: Esc on the prompt sends
// nothing and says so; y deletes and reports it.
func TestProfileDetailTrash_DeleteEscSendsNothing(t *testing.T) {
	b := idleTrashBackend(t)
	m := openTrash(t, b)
	m = detailStep(t, m, press("d"))
	if v := view(m); !strings.Contains(v, "Delete 1 file(s) from the local trash for good?") || !strings.Contains(v, "T1/a.txt") {
		t.Fatalf("delete prompt:\n%s", v)
	}
	if got := flashesAfter(m, "esc", time.Second); strings.Join(got, "|") != "Nothing was deleted" {
		t.Errorf("flashes after Esc = %q", got)
	}
	if got := b.matching("POST /profiles/docs/trash/delete"); len(got) != 0 {
		t.Fatalf("deleted after Esc: %v", got)
	}
	if got := flashesAfter(m, "y", time.Second); strings.Join(got, "|") != "deleted 1 file(s)" {
		t.Errorf("flashes after y = %q", got)
	}
}

// When the user declines to replace newer files, only the first restore is
// sent and the newer files stay.
func TestProfileDetailTrash_OverwriteDeclinedKeepsNewerFiles(t *testing.T) {
	b := idleTrashBackend(t)
	m := openTrash(t, b)
	m = detailStep(t, m, press("a"))
	m = detailStep(t, m, press("u"))
	if !m.CapturesInput() {
		t.Fatalf("no overwrite prompt:\n%s", view(m))
	}
	if got := flashesAfter(m, "n", time.Second); strings.Join(got, "|") != "The newer files were left as they are" {
		t.Errorf("flashes = %q", got)
	}
	if got := b.bodiesOf("POST /profiles/docs/trash/restore"); len(got) != 1 {
		t.Errorf("restores = %v", got)
	}
}

// A restore while the profile syncs would race the sync: refused locally,
// nothing sent.
func TestProfileDetailTrash_RestoreRefusedWhileSyncing(t *testing.T) {
	b := idleTrashBackend(t)
	m := openTrash(t, b)
	// A push starts (the spinner's ticks are not run).
	busy := &api.ProfileStatusResponse{ProfileResponse: api.ProfileResponse{Slug: "docs", Name: "Dokumente"}, State: "pushing"}
	updated, _ := m.Update(ui.PollResultMsg{ViewID: ui.ViewProfileDetail, Data: ui.ProfileDetailData{Slug: "docs", Value: busy}})
	m = updated.(ui.ProfileDetailModel)
	got := flashesAfter(m, "u", time.Second)
	if strings.Join(got, "|") != "A sync of this profile is running; restore when it is done" {
		t.Errorf("flashes = %q", got)
	}
	if posts := b.matching("POST /profiles/docs/trash/"); len(posts) != 0 {
		t.Errorf("requests = %v", posts)
	}
}

// An empty trash says so; u and d have nothing to act on and send nothing.
func TestProfileDetailTrash_EmptyTrash(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/profiles/docs/trash", 200, map[string]any{"side": "local", "entries": []any{}, "total_files": 0, "total_bytes": 0})
	m := openTrash(t, b)
	if v := view(m); !strings.Contains(v, "The trash is empty.") || !strings.Contains(v, "0 file(s), 0 B") {
		t.Errorf("empty trash view:\n%s", v)
	}
	if got := flashesAfter(m, "u", time.Second); strings.Join(got, "|") != "Nothing to restore" {
		t.Errorf("u flashes = %q", got)
	}
	if got := flashesAfter(m, "d", time.Second); strings.Join(got, "|") != "Nothing to delete" {
		t.Errorf("d flashes = %q", got)
	}
	if m.CapturesInput() || len(b.matching("POST /profiles/docs/trash/")) != 0 {
		t.Errorf("requests = %v", b.matching("POST /profiles/docs/trash/"))
	}
}

// A refused restore or delete shows the envelope's detail and reloads the
// trash (its content may have changed).
func TestProfileDetailTrash_ActionErrorEnvelopes(t *testing.T) {
	for _, tc := range []struct{ keys, path, want string }{
		{"u", "/profiles/docs/trash/restore", "Restore failed: API error 409: A sync of this profile is running"},
		{"dy", "/profiles/docs/trash/delete", "Delete failed: API error 409: A sync of this profile is running"},
	} {
		t.Run(tc.path, func(t *testing.T) {
			b := idleTrashBackend(t)
			b.json("POST", tc.path, 409, errorEnvelope("A sync of this profile is running", "sync_busy"))
			m := openTrash(t, b)
			for _, k := range tc.keys[:len(tc.keys)-1] {
				m = detailStep(t, m, press(string(k)))
			}
			b.reset()
			got := flashesAfter(m, tc.keys[len(tc.keys)-1:], time.Second)
			if strings.Join(got, "|") != tc.want {
				t.Errorf("flashes = %q, want %q", got, tc.want)
			}
			if reloads := b.matching("GET /profiles/docs/trash"); len(reloads) != 1 {
				t.Errorf("trash not reloaded: %v", b.log())
			}
		})
	}
}

// Files that failed for another reason than being newer are named with
// the backend's message; no overwrite prompt opens for them.
func TestProfileDetailTrash_RestorePartialFailure(t *testing.T) {
	b := idleTrashBackend(t)
	b.json("POST", "/profiles/docs/trash/restore", 200, map[string]any{"done": []string{"T1/b.txt"},
		"failed": []any{map[string]any{"id": "T1/a.txt", "code": "io_error", "message": "permission denied"}}})
	m := openTrash(t, b)
	m = detailStep(t, m, press("a"))
	got := flashesAfter(m, "u", time.Second)
	want := "Restored 1 file(s); the next sync carries them to the other side; failed: T1/a.txt: permission denied"
	if strings.Join(got, "|") != want {
		t.Errorf("flashes = %q", got)
	}
}

// A listing error is shown; the remote side and a truncated listing are
// labelled; a delete of many files lists the first ten and counts the rest.
func TestProfileDetailTrash_RemoteSideTruncatedAndErrors(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/profiles/docs/trash", 503, errorEnvelope("The remote cannot be reached", "remote_unreachable"))
	m := openTrash(t, b)
	if v := view(m); !strings.Contains(v, "API error 503: The remote cannot be reached") {
		t.Fatalf("listing error not shown:\n%s", v)
	}
	var entries []string
	for i := 0; i < 12; i++ {
		entries = append(entries, fmt.Sprintf(`{"id":"T/%02d.txt","folder":"T","path":"%02d.txt","size":null,"modified":"2026-10-01T10:00:00Z","trashed_at":null}`, i, i))
	}
	b.handle("GET", "/profiles/docs/trash", func(w http.ResponseWriter, _ map[string]any) {
		_, _ = w.Write([]byte(`{"side":"remote","total_files":40,"total_bytes":0,"truncated":true,"entries":[` +
			strings.Join(entries, ",") + `]}`))
	})
	m = detailStep(t, m, press("v"))
	v := view(m)
	for _, want := range []string{"Trash of the remote folder", "40 file(s)", "(the newest 12 are listed)", "00.txt"} {
		if !strings.Contains(v, want) {
			t.Errorf("remote trash lacks %q:\n%s", want, v)
		}
	}
	m = detailStep(t, m, press("a"))
	m = detailStep(t, m, press("d"))
	if v := view(m); !strings.Contains(v, "Delete 12 file(s) from the remote trash") || !strings.Contains(v, "... and 2 more") ||
		strings.Contains(v, "T/11.txt") {
		t.Errorf("delete prompt:\n%s", v)
	}
	_ = detailStep(t, m, press("n"))
	if got := b.matching("POST /profiles/docs/trash/"); len(got) != 0 {
		t.Errorf("requests = %v", got)
	}
}

// Switching sides while a listing loads drops the old side's late answer:
// the remote tab never shows local files.
func TestProfileDetailTrash_StaleSideAnswerDropped(t *testing.T) {
	b := idleTrashBackend(t)
	m := openDetail(t, b)
	for i := 0; i < 3; i++ {
		m = detailStep(t, m, press("right"))
	}
	updated, localFetch := m.Update(press("right")) // the local listing is not delivered yet
	m = updated.(ui.ProfileDetailModel)
	updated, _ = m.Update(press("v"))
	m = updated.(ui.ProfileDetailModel)
	for _, msg := range runAll(localFetch) {
		m = detailStep(t, m, msg)
	}
	v := view(m)
	if !strings.Contains(v, "Trash of the remote folder") || !strings.Contains(v, "Loading...") || strings.Contains(v, "a.txt") {
		t.Errorf("remote tab after the late local answer:\n%s", v)
	}
	// r reloads the side shown.
	b.reset()
	_ = detailStep(t, m, press("r"))
	if got := b.matching("GET /profiles/docs/trash"); len(got) != 1 || got[0] != "GET /profiles/docs/trash?side=remote" {
		t.Errorf("reload = %v", got)
	}
}

package ui_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

const snap = "2026-09-27T08-30-00"

func backupsBackend(t *testing.T, target map[string]any) *backend {
	t.Helper()
	b := detailBackend(t)
	base := map[string]any{"id": 3, "profile_id": 1, "name": "Nightly", "target_path": "/backups", "target_type": "local",
		"remote_name": nil, "retention_days": 7, "keep_last": 3, "frequency_hours": 24, "backup_mode": "mirror",
		"enabled": true, "created_at": "x", "updated_at": "x"}
	for k, v := range target {
		base[k] = v
	}
	b.json("GET", "/profiles/docs/backups", 200, []any{base})
	b.json("GET", "/profiles/docs/backups/3/snapshots", 200, []any{
		map[string]any{"snapshot_id": snap, "created_at": "2026-09-27T08:30:00Z", "size_bytes": 10, "status": "available",
			"latest": true},
	})
	return b
}

// openSnapshots shows the snapshots of the first backup target.
func openSnapshots(t *testing.T, b *backend) ui.ProfileDetailModel {
	t.Helper()
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	m = detailStep(t, m, press("right"))
	m = detailStep(t, m, press("enter"))
	if !strings.Contains(view(m), snap) {
		t.Fatalf("snapshots view:\n%s", view(m))
	}
	return m
}

func TestBackups_EncryptedAndUnverifiedTargetsAreShown(t *testing.T) {
	b := backupsBackend(t, map[string]any{"encrypted": true, "last_backup_status": "completed",
		"last_verify_status": "failed", "last_verify_message": "1 file(s) differ from the folder"})
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	m = detailStep(t, m, press("right"))
	v := view(m)
	if !strings.Contains(v, "UNVERIFIED") || !strings.Contains(v, "verification of the last backup failed: 1 file(s) differ") {
		t.Errorf("backups view:\n%s", v)
	}
}

// fillBackupForm creates target Vault at /vault with passphrase "correct
// horse", repeated as repeat, keeping every other default.
func fillBackupForm(t *testing.T, m ui.ProfileDetailModel, repeat string) ui.ProfileDetailModel {
	t.Helper()
	msgs := []tea.Msg{press("c")}
	msgs = append(msgs, typeKeys("Vault")...)
	msgs = append(msgs, press("tab"), press("tab"), press("tab"))
	msgs = append(msgs, typeKeys("/vault")...)
	for i := 0; i < 6; i++ { // mode, every, keep, keep at least, verify, passphrase
		msgs = append(msgs, press("tab"))
	}
	msgs = append(msgs, typeKeys("correct horse")...)
	msgs = append(msgs, press("tab"))
	msgs = append(msgs, typeKeys(repeat)...)
	msgs = append(msgs, press("enter"))
	for _, msg := range msgs {
		m = detailStep(t, m, msg)
	}
	return m
}

func TestBackups_CreateWithPassphraseAndVerification(t *testing.T) {
	b := backupsBackend(t, nil)
	b.json("POST", "/profiles/docs/backups", 201, map[string]any{"id": 4})
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	m = detailStep(t, m, press("right"))
	b.reset()
	m = fillBackupForm(t, m, "correct hors")
	if got := b.matching("POST"); len(got) != 0 {
		t.Fatalf("created despite differing passphrases: %v", got)
	}
	_ = fillBackupForm(t, m, "correct horse")
	bodies := b.bodiesOf("POST /profiles/docs/backups")
	if len(bodies) != 1 || bodies[0]["encryption_passphrase"] != "correct horse" || bodies[0]["verify_after_backup"] != true {
		t.Errorf("create bodies = %v", bodies)
	}
}

func TestBackups_FullRestoreShowsThePreviewFirst(t *testing.T) {
	b := backupsBackend(t, nil)
	b.json("POST", "/profiles/docs/backups/3/restore/preview", 200, map[string]any{
		"snapshot_id": snap, "restore_scope": "local_only",
		"sides": []any{map[string]any{"side": "local", "path": "/home/u/docs", "added": 2, "replaced": 3, "removed": 4,
			"unchanged": 5, "added_examples": []string{}, "replaced_examples": []string{}, "removed_examples": []string{}}},
	})
	b.json("POST", "/profiles/docs/backups/3/restore", 202, backupJobJSON(1, "running", nil, nil))
	b.json("GET", "/profiles/docs/backups/3/jobs/1", 200, backupJobJSON(1, "completed", nil, nil))
	m := openSnapshots(t, b)
	m = detailStep(t, m, press("r"))
	b.reset()
	m = detailStep(t, m, press("enter"))
	if got := b.matching("POST"); len(got) != 1 || !strings.HasSuffix(got[0], "/restore/preview") {
		t.Fatalf("requests before confirming = %v", got)
	}
	if v := view(m); !strings.Contains(v, "2 added, 3 replaced, 4 removed, 5 unchanged") {
		t.Fatalf("restore prompt:\n%s", v)
	}
	m = detailStep(t, m, press("n"))
	if got := b.matching("POST /profiles/docs/backups/3/restore"); len(got) != 1 {
		t.Fatalf("restored although cancelled: %v", got)
	}

	m = detailStep(t, m, press("r"))
	m = detailStep(t, m, press("enter"))
	flashes := strings.Join(flashesAfter(m, "y", time.Second), "|")
	if got := b.bodiesOf("POST /profiles/docs/backups/3/restore"); len(got) != 1 || got[0]["snapshot_id"] != snap {
		t.Errorf("restore bodies = %v", got)
	}
	// The restore answers at once with the job running; the view follows it.
	if !strings.Contains(flashes, "Restore of docs started (job 1)") || !strings.Contains(flashes, "|Restore of docs finished") {
		t.Errorf("flashes = %v", flashes)
	}
	if polls := b.matching("GET /profiles/docs/backups/3/jobs/1"); len(polls) != 1 {
		t.Errorf("job polls = %v", polls)
	}
}

func TestBackups_BrowseSelectAndRestoreFiles(t *testing.T) {
	b := backupsBackend(t, nil)
	files := "/profiles/docs/backups/3/snapshots/" + snap + "/files"
	b.json("GET", files, 200, map[string]any{
		"snapshot_id": snap, "path": "", "search": nil, "total": 2, "offset": 0, "limit": 1000, "snapshot_files": 3,
		"entries": []any{
			map[string]any{"path": "docs", "name": "docs", "is_dir": true, "size": 20, "file_count": 2},
			map[string]any{"path": "top.txt", "name": "top.txt", "is_dir": false, "size": 3,
				"mod_time": "2026-09-27T08:00:00Z"},
		},
	})
	b.json("POST", "/profiles/docs/backups/3/restore-files", 202, backupJobJSON(2, "running", nil, nil))
	b.json("GET", "/profiles/docs/backups/3/jobs/2", 200, backupJobJSON(2, "completed", nil, nil))
	m := openSnapshots(t, b)
	m = detailStep(t, m, press("f"))
	v := view(m)
	if !strings.Contains(v, "docs/") || !strings.Contains(v, "top.txt") || !strings.Contains(v, "3 file(s) in the snapshot") {
		t.Fatalf("browser:\n%s", v)
	}
	// Enter opens a folder; Backspace goes back up.
	b.reset()
	m = detailStep(t, m, press("enter"))
	if got := b.matching("GET " + files); len(got) != 1 || !strings.Contains(got[0], "path=docs") {
		t.Fatalf("folder request = %v", got)
	}
	m = detailStep(t, m, press("backspace"))
	m = detailStep(t, m, press(" "))
	m = detailStep(t, m, press("down"))
	m = detailStep(t, m, press(" "))
	if !strings.Contains(view(m), "2 selected") {
		t.Fatalf("selection:\n%s", view(m))
	}
	b.reset()
	m = detailStep(t, m, press("R"))
	if len(b.matching("POST")) != 0 {
		t.Fatal("restored before confirmation")
	}
	m = detailStep(t, m, press("y"))
	got := b.bodiesOf("POST /profiles/docs/backups/3/restore-files")
	if len(got) != 1 || got[0]["snapshot_id"] != snap || got[0]["target_dir"] != nil {
		t.Fatalf("restore-files bodies = %v", got)
	}
	if paths, _ := got[0]["paths"].([]any); len(paths) != 2 || paths[0] != "docs" || paths[1] != "top.txt" {
		t.Errorf("paths = %v", got[0]["paths"])
	}

	// O asks for a folder and restores there.
	m = detailStep(t, m, press("O"))
	for _, k := range typeKeys("/home/u/restored") {
		m = detailStep(t, m, k)
	}
	_ = detailStep(t, m, press("enter"))
	got = b.bodiesOf("POST /profiles/docs/backups/3/restore-files")
	if len(got) != 2 || got[1]["target_dir"] != "/home/u/restored" {
		t.Errorf("restore-files bodies = %v", got)
	}
}

func TestBackups_SnapshotTableMarksTheLatest(t *testing.T) {
	b := detailBackend(t)
	b.json("GET", "/profiles/docs/backups", 200, []any{map[string]any{"id": 3, "profile_id": 1, "name": "Nightly",
		"target_path": "/backups", "target_type": "local", "remote_name": nil, "retention_days": 7, "keep_last": 3,
		"frequency_hours": 24, "backup_mode": "mirror", "enabled": true, "created_at": "x", "updated_at": "x"}})
	b.json("GET", "/profiles/docs/backups/3/snapshots", 200, []any{
		map[string]any{"snapshot_id": snap, "created_at": "2026-09-27T08:30:00Z", "size_bytes": 10, "status": "available",
			"latest": true},
		map[string]any{"snapshot_id": "2026-09-26T08-30-00", "created_at": "2026-09-26T08:30:00Z", "size_bytes": 10,
			"status": "available", "latest": false},
	})
	m := openSnapshots(t, b)
	v := view(m)
	if !strings.Contains(v, "Latest") {
		t.Fatalf("snapshots view lacks the latest column:\n%s", v)
	}
	for _, line := range strings.Split(v, "\n") {
		if strings.Contains(line, "2026-09-26T08-30-00") && strings.Contains(line, "yes") {
			t.Errorf("only the newest snapshot is the latest:\n%s", v)
		}
		if strings.Contains(line, snap) && !strings.Contains(line, "yes") {
			t.Errorf("the newest snapshot is not marked:\n%s", v)
		}
	}
}

// backupJobJSON is a BackupJobResponse of target 3.
func backupJobJSON(id int, status string, code, message any) map[string]any {
	return map[string]any{"id": id, "target_id": 3, "started_at": "x", "finished_at": nil, "status": status,
		"direction": "backup", "size_bytes": nil, "snapshot_id": nil, "error_code": code, "error_message": message}
}

// backupsTab opens the backups tab of docs.
func backupsTab(t *testing.T, b *backend) ui.ProfileDetailModel {
	t.Helper()
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	return detailStep(t, m, press("right"))
}

// A backup run answers 202 with the job running; the view says it started,
// shows it as running, polls GET .../jobs/{id} until it ends and flashes the
// outcome with the job's public error message.
func TestBackups_RunFollowsTheJobUntilItEnds(t *testing.T) {
	b := backupsBackend(t, nil)
	b.json("POST", "/profiles/docs/backups/3/run", 202, backupJobJSON(12, "running", nil, nil))
	var polls atomic.Int32
	b.handle("GET", "/profiles/docs/backups/3/jobs/12", func(w http.ResponseWriter, _ map[string]any) {
		job := backupJobJSON(12, "failed", "target_unreachable", "The backup target cannot be reached.")
		if polls.Add(1) == 1 {
			job = backupJobJSON(12, "running", nil, nil)
		}
		_ = json.NewEncoder(w).Encode(job)
	})
	m := backupsTab(t, b)

	// The start alone: the job shows as running in the backups tab.
	updated, cmd := m.Update(press("b"))
	m = updated.(ui.ProfileDetailModel)
	for _, msg := range runAll(cmd) {
		if r, ok := msg.(ui.ActionResultMsg); ok && r.Action == "run_backup_started" {
			updated, _ = m.Update(r)
			m = updated.(ui.ProfileDetailModel)
		}
	}
	if v := view(m); !strings.Contains(v, "Running in the background: backup job 12") {
		t.Errorf("backups view while running:\n%s", v)
	}

	got := flashesAfter(backupsTab(t, b), "b", 3*ui.FastPollInterval)
	want := []string{"Backup of docs started (job 12); it runs in the background",
		"Backup of docs failed: The backup target cannot be reached."}
	for _, w := range want {
		if !strings.Contains(strings.Join(got, "|"), w) {
			t.Errorf("flashes = %v, want %q", got, w)
		}
	}
	if polls.Load() < 2 {
		t.Errorf("job polls = %d, want at least 2", polls.Load())
	}
}

// A busy profile refuses the start at once (409 sync_busy): the refusal is
// shown and nothing is polled.
func TestBackups_BusyProfileRefusesTheRun(t *testing.T) {
	b := backupsBackend(t, nil)
	b.json("POST", "/profiles/docs/backups/3/run", 409,
		map[string]any{"detail": "A sync of this profile is running; try again when it has finished."})
	got := flashesAfter(backupsTab(t, b), "b", time.Second)
	if !strings.Contains(strings.Join(got, "|"), "Backup of docs not started: API error 409: A sync of this profile is running") {
		t.Errorf("flashes = %v", got)
	}
	if polls := b.matching("GET /profiles/docs/backups/3/jobs/"); len(polls) != 0 {
		t.Errorf("polled a refused job: %v", polls)
	}
}

// A selective sync answers 202 running; the view polls GET
// /sync/selective/{id} and reports the per-file failures once it has ended.
func TestDiff_SelectiveSyncFollowsTheRun(t *testing.T) {
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/diff", 200, map[string]any{
		"files":   []any{map[string]any{"path": "x.txt", "category": "local_only", "local_size": 1}},
		"summary": map[string]any{"total": 1, "local_only": 1}})
	b.json("POST", "/profiles/docs/sync/selective", 202,
		map[string]any{"job_id": 4, "status": "running", "total": 1, "succeeded": 0, "failed": 0, "errors": []any{}})
	b.json("GET", "/profiles/docs/sync/selective/4", 200,
		map[string]any{"job_id": 4, "status": "completed", "total": 1, "succeeded": 0, "failed": 1,
			"errors": []any{map[string]any{"path": "x.txt", "error": "permission denied"}}})
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))

	got := strings.Join(flashesAfter(m, "s", time.Second), "|")
	if !strings.Contains(got, "Selective sync of 1 file(s) started (job 4)") ||
		!strings.Contains(got, "Selective sync: 0 succeeded, 1 failed; x.txt: permission denied") {
		t.Errorf("flashes = %v", got)
	}
	if polls := b.matching("GET /profiles/docs/sync/selective/4"); len(polls) != 1 {
		t.Errorf("polls = %v", polls)
	}
}

func TestDiff_SelectiveSyncRefusedWhenBusy(t *testing.T) {
	b := detailBackend(t)
	b.json("POST", "/profiles/docs/diff", 200, map[string]any{
		"files":   []any{map[string]any{"path": "x.txt", "category": "local_only", "local_size": 1}},
		"summary": map[string]any{"total": 1, "local_only": 1}})
	b.json("POST", "/profiles/docs/sync/selective", 409, map[string]any{"detail": "A backup of this profile is running."})
	m := openDetail(t, b)
	m = detailStep(t, m, press("right"))
	got := strings.Join(flashesAfter(m, "s", time.Second), "|")
	if !strings.Contains(got, "Selective sync not started: API error 409: A backup of this profile is running.") {
		t.Errorf("flashes = %v", got)
	}
	if polls := b.matching("GET /profiles/docs/sync/selective/"); len(polls) != 0 {
		t.Errorf("polled a refused run: %v", polls)
	}
}

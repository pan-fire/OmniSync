package api_test

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// Deletes of profiles and backup targets need ?confirm=true (TUI-2).
func TestDeletes_SendConfirm(t *testing.T) {
	f := newFakeBackend(t)
	f.on("DELETE", "/profiles/docs", 204, nil)
	f.on("DELETE", "/profiles/docs/backups/3", 204, nil)
	c := f.client("")
	ctx := context.Background()

	if err := c.DeleteProfile(ctx, "docs"); err != nil {
		t.Fatal(err)
	}
	if r := f.last(); r.Method != "DELETE" || r.Path != "/profiles/docs" || r.RawQuery != "confirm=true" {
		t.Errorf("profile delete sent %s %s?%s", r.Method, r.Path, r.RawQuery)
	}
	if err := c.DeleteBackupTarget(ctx, "docs", 3); err != nil {
		t.Fatal(err)
	}
	if r := f.last(); r.Path != "/profiles/docs/backups/3" || r.RawQuery != "confirm=true" {
		t.Errorf("backup delete sent %s?%s", r.Path, r.RawQuery)
	}
}

func TestDeleteRemote_Force(t *testing.T) {
	f := newFakeBackend(t)
	f.on("DELETE", "/remotes/gdrive", 200, map[string]any{"detail": "Remote 'gdrive' deleted"})
	c := f.client("")
	if err := c.DeleteRemote(context.Background(), "gdrive", false); err != nil {
		t.Fatal(err)
	}
	if q := f.last().RawQuery; q != "" {
		t.Errorf("unexpected query %q", q)
	}
	if err := c.DeleteRemote(context.Background(), "gdrive", true); err != nil {
		t.Fatal(err)
	}
	if q := f.last().RawQuery; q != "force=true" {
		t.Errorf("query = %q, want force=true", q)
	}
}

func TestProfileSync_Endpoints(t *testing.T) {
	f := newFakeBackend(t)
	f.on("POST", "/profiles/docs/sync/start", 200, map[string]any{"job_id": 3, "state": "error"})
	f.on("POST", "/profiles/docs/sync/preview", 200, map[string]any{
		"push":     map[string]any{"deletes": 2, "replaces": 1, "creates": 1, "exceeds_max_delete": false},
		"pull":     map[string]any{"deletes": 1, "replaces": 1, "creates": 2, "exceeds_max_delete": false},
		"excluded": 0, "max_delete": 25, "error": nil,
	})
	f.on("POST", "/profiles/docs/diff", 200, map[string]any{"files": []any{}, "summary": map[string]any{"total": 0}})
	f.on("POST", "/profiles/docs/sync/selective", 202, map[string]any{"job_id": 4, "status": "running", "total": 1, "succeeded": 0, "failed": 0, "errors": []any{}})
	c := f.client("")
	ctx := context.Background()

	start, err := c.StartProfileSync(ctx, "docs", api.SyncDirectionPull, false)
	if err != nil {
		t.Fatal(err)
	}
	if start.State != api.SyncStateError || f.last().Body["direction"] != "pull" {
		t.Errorf("start: %+v body %v", start, f.last().Body)
	}
	if _, ok := f.last().Body["force"]; ok {
		t.Error("force must not be sent unless set")
	}
	if _, err = c.StartProfileSync(ctx, "docs", api.SyncDirectionPush, true); err != nil {
		t.Fatal(err)
	}
	if b := f.last().Body; b["direction"] != "push" || b["force"] != true {
		t.Errorf("forced start body = %v", b)
	}

	preview, err := c.PreviewProfileSync(ctx, "docs")
	if err != nil {
		t.Fatal(err)
	}
	if r := f.last(); r.Method != "POST" || r.Path != "/profiles/docs/sync/preview" || r.Body != nil {
		t.Errorf("preview sent %s %s %v", r.Method, r.Path, r.Body)
	}
	if preview.Push.Deletes != 2 || preview.Pull.Creates != 2 || preview.MaxDelete == nil || *preview.MaxDelete != 25 {
		t.Errorf("preview: %+v", preview)
	}

	if _, err := c.ProfileDiff(ctx, "docs", 0, 0); err != nil {
		t.Fatal(err)
	}
	if q := f.last().RawQuery; q != "limit=0&offset=0" {
		t.Errorf("diff query = %q", q)
	}

	sel, selErr := c.ProfileSelectiveSync(ctx, "docs", []api.SelectiveSyncItem{{Path: "a b.txt", Action: api.FileActionPush}})
	if selErr != nil {
		t.Fatal(selErr)
	}
	if sel.JobID != 4 || sel.Status != api.JobStatusRunning || sel.Total != 1 {
		t.Errorf("selective start = %+v", sel)
	}
	items, _ := f.last().Body["items"].([]any)
	if len(items) != 1 || items[0].(map[string]any)["path"] != "a b.txt" || items[0].(map[string]any)["action"] != "push" {
		t.Errorf("selective body = %v", f.last().Body)
	}
}

// last_error and max_delete come with GET /profiles; no status lookups.
func TestListProfiles_CarriesLastErrorAndMaxDelete(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/profiles", 200, []any{
		map[string]any{"slug": "ok", "state": "idle", "last_error": nil, "max_delete": nil},
		map[string]any{"slug": "bad", "state": "error", "last_error": "remote unreachable", "max_delete": 25},
	})
	profiles, err := f.client("").ListProfiles(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(profiles) != 2 || profiles[0].LastError != nil || profiles[0].MaxDelete != nil {
		t.Fatalf("profiles = %+v", profiles)
	}
	if profiles[1].LastError == nil || *profiles[1].LastError != "remote unreachable" || profiles[1].MaxDelete == nil || *profiles[1].MaxDelete != 25 {
		t.Errorf("bad profile = %+v", profiles[1])
	}
	if n := len(f.all()); n != 1 {
		t.Errorf("expected only GET /profiles, got %d requests", n)
	}
}

func TestResolveConflict_SendsResolutionAndReportsDetail(t *testing.T) {
	f := newFakeBackend(t)
	f.on("POST", "/conflicts/5/resolve", 200, map[string]any{"id": 5, "job_id": nil, "file_path": "a.txt", "resolved": true, "resolution": "dismiss"})
	f.on("POST", "/conflicts/6/resolve", 409, map[string]any{"detail": "The remote file changed since the conflict was found; not overwriting it unseen."})
	f.on("POST", "/conflicts/7/resolve", 502, map[string]any{"detail": "Could not resolve the conflict: rclone failed"})
	c := f.client("")
	ctx := context.Background()

	for _, res := range []api.ConflictResolution{api.ConflictKeepLocal, api.ConflictKeepRemote, api.ConflictKeepBoth, api.ConflictDismiss} {
		got, err := c.ResolveConflict(ctx, 5, res)
		if err != nil {
			t.Fatal(err)
		}
		if b := f.last().Body; b["resolution"] != string(res) {
			t.Errorf("body = %v, want resolution %s", b, res)
		}
		if got.JobID != nil || !got.Resolved {
			t.Errorf("answer = %+v", got)
		}
	}

	_, err := c.ResolveConflict(ctx, 6, api.ConflictKeepLocal)
	var apiErr *api.ApiError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != 409 || !strings.Contains(apiErr.Detail, "changed since the conflict was found") {
		t.Errorf("409: %v", err)
	}
	_, err = c.ResolveConflict(ctx, 7, api.ConflictKeepRemote)
	if !errors.As(err, &apiErr) || apiErr.StatusCode != 502 || !strings.Contains(apiErr.Detail, "rclone failed") {
		t.Errorf("502: %v", err)
	}
}

func TestBackupRequests(t *testing.T) {
	f := newFakeBackend(t)
	f.on("POST", "/profiles/docs/backups", 201, map[string]any{"id": 1, "name": "n", "backup_mode": "mirror"})
	f.on("POST", "/profiles/docs/backups/1/restore", 202, map[string]any{"id": 9, "status": "running", "error_code": nil})
	c := f.client("")
	ctx := context.Background()

	if _, err := c.CreateBackupTarget(ctx, "docs", api.BackupTargetCreateRequest{
		Name: "n", TargetPath: "/b", TargetType: api.BackupTargetLocal, BackupMode: api.BackupModeMirror,
	}); err != nil {
		t.Fatal(err)
	}
	body := f.last().Body
	if body["backup_mode"] != "mirror" || body["target_type"] != "local" {
		t.Errorf("create body = %v", body)
	}
	for _, k := range []string{"mode", "frequency_hours", "retention_days", "keep_last"} {
		if _, ok := body[k]; ok {
			t.Errorf("create body must not contain %q (zero values fail validation): %v", k, body)
		}
	}

	job, err := c.RestoreSnapshot(ctx, "docs", 1, api.RestoreRequest{SnapshotID: "s1", RestoreScope: api.RestoreScopeBoth})
	if err != nil {
		t.Fatal(err)
	}
	if job.ID != 9 || job.Status != api.BackupJobRunning || job.ErrorCode != nil {
		t.Errorf("restore start = %+v", job)
	}
	if b := f.last().Body; b["snapshot_id"] != "s1" || b["restore_scope"] != "both" {
		t.Errorf("restore body = %v", b)
	}
}

func TestWizardCreate_SendsParamsAndSessionNoToken(t *testing.T) {
	f := newFakeBackend(t)
	f.on("POST", "/wizard/create", 200, map[string]any{"detail": "ok"})
	c := f.client("")
	sid := "sess-1"
	if err := c.CreateRemote(context.Background(), api.CreateRemoteRequest{
		Name: "gd", ProviderID: "gdrive", Params: map[string]string{"scope": "drive"}, SessionID: &sid,
	}); err != nil {
		t.Fatal(err)
	}
	b := f.last().Body
	if b["name"] != "gd" || b["provider_id"] != "gdrive" || b["session_id"] != "sess-1" {
		t.Errorf("body = %v", b)
	}
	if p, _ := b["params"].(map[string]any); p["scope"] != "drive" {
		t.Errorf("params = %v", b["params"])
	}
	if _, ok := b["token"]; ok {
		t.Error("token must not be sent")
	}

	// Key-based providers: params, no session id, and params is never null.
	if err := c.CreateRemote(context.Background(), api.CreateRemoteRequest{Name: "s3", ProviderID: "s3"}); err != nil {
		t.Fatal(err)
	}
	b = f.last().Body
	if _, ok := b["session_id"]; ok {
		t.Error("session_id must be omitted for key-based providers")
	}
	if _, ok := b["params"].(map[string]any); !ok {
		t.Errorf("params must be an object, got %v", b["params"])
	}
}

func TestRemoteDependencies_Objects(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/remotes/gdrive/dependencies", 200, map[string]any{
		"profiles":       []any{map[string]any{"slug": "docs", "name": "Docs"}},
		"backup_targets": []any{map[string]any{"profile_slug": "docs", "target_name": "Nightly", "target_id": 3}},
	})
	deps, err := f.client("").RemoteDependencies(context.Background(), "gdrive")
	if err != nil {
		t.Fatal(err)
	}
	if deps.Empty() || deps.Profiles[0].Name != "Docs" || deps.BackupTargets[0].TargetID != 3 {
		t.Errorf("deps = %+v", deps)
	}
}

func TestSimpleReads(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/sync/status/aggregate", 200, map[string]any{"overall_state": "pushing"})
	f.on("GET", "/logs", 200, []any{map[string]any{"timestamp": "t", "level": "INFO", "message": "m"}})
	f.on("GET", "/jobs/5/files", 200, []any{map[string]any{"id": 1, "job_id": 5, "file_path": "a", "action": "created", "size_bytes": 3}})
	f.on("GET", "/notifications/history", 200, map[string]any{"items": []any{}, "total": 0})
	c := f.client("")
	ctx := context.Background()

	agg, err := c.AggregateStatus(ctx)
	if err != nil || !agg.OverallState.Busy() {
		t.Fatalf("aggregate: %+v %v", agg, err)
	}
	logs, err := c.GetLogs(ctx, 0, 200)
	if err != nil || len(logs) != 1 {
		t.Fatalf("logs: %v %v", logs, err)
	}
	if q := f.last().RawQuery; q != "limit=200&skip=0" {
		t.Errorf("logs query = %q", q)
	}
	files, err := c.GetJobFiles(ctx, 5)
	if err != nil || len(files) != 1 || files[0].FilePath != "a" || *files[0].SizeBytes != 3 {
		t.Fatalf("files: %+v %v", files, err)
	}
	if _, err := c.NotificationHistory(ctx, 50, 0, "a b"); err != nil {
		t.Fatal(err)
	}
	if q := f.last().RawQuery; q != "limit=50&offset=0&profile=a+b" {
		t.Errorf("history query = %q", q)
	}
}

// "Sync now" of a two-way profile is sync/start with direction two_way;
// force is sent only when set (after the user confirmed a paused profile).
func TestTwoWaySync_StartAndResync(t *testing.T) {
	f := newFakeBackend(t)
	f.on("POST", "/profiles/docs/sync/start", 200, map[string]any{"job_id": 5, "state": "syncing"})
	f.on("POST", "/profiles/docs/sync/resync", 200, map[string]any{"job_id": 6, "state": "idle"})
	f.on("POST", "/profiles/mirror/sync/resync", 409, map[string]any{"detail": "Profile 'mirror' is not a two-way profile"})
	c := f.client("")
	ctx := context.Background()

	start, err := c.StartProfileSync(ctx, "docs", api.SyncDirectionTwoWay, false)
	if err != nil {
		t.Fatal(err)
	}
	r := f.last()
	if r.Method != "POST" || r.Path != "/profiles/docs/sync/start" || r.Body["direction"] != "two_way" {
		t.Errorf("sync now sent %s %s %v", r.Method, r.Path, r.Body)
	}
	if _, ok := r.Body["force"]; ok {
		t.Errorf("force must not be sent unless set: %v", r.Body)
	}
	if start.JobID != 5 || start.State != api.SyncStateSyncing || !start.State.Busy() {
		t.Errorf("start = %+v", start)
	}
	if _, err = c.StartProfileSync(ctx, "docs", api.SyncDirectionTwoWay, true); err != nil {
		t.Fatal(err)
	}
	if b := f.last().Body; b["direction"] != "two_way" || b["force"] != true {
		t.Errorf("forced sync now body = %v", b)
	}

	resync, err := c.ResyncProfile(ctx, "docs")
	if err != nil {
		t.Fatal(err)
	}
	r = f.last()
	if r.Method != "POST" || r.Path != "/profiles/docs/sync/resync" || len(r.Body) != 1 || r.Body["confirm"] != true {
		t.Errorf("resync sent %s %s %v", r.Method, r.Path, r.Body)
	}
	if resync.JobID != 6 {
		t.Errorf("resync = %+v", resync)
	}

	_, err = c.ResyncProfile(ctx, "mirror")
	if !api.IsStatus(err, 409) || !strings.Contains(err.Error(), "not a two-way profile") {
		t.Errorf("409: %v", err)
	}
}

// Profiles are created with the chosen mode; an update sends sync_mode only
// when it is set.
func TestProfileSyncMode_CreateAndUpdate(t *testing.T) {
	f := newFakeBackend(t)
	f.on("POST", "/profiles", 201, map[string]any{"slug": "docs", "sync_mode": "two_way"})
	f.on("PUT", "/profiles/docs", 200, map[string]any{"slug": "docs", "sync_mode": "mirror"})
	c := f.client("")
	ctx := context.Background()

	created, err := c.CreateProfile(ctx, api.ProfileCreateRequest{Name: "Docs", LocalDir: "/l", RemoteDir: "r:x", SyncMode: api.SyncModeTwoWay})
	if err != nil {
		t.Fatal(err)
	}
	if f.last().Body["sync_mode"] != "two_way" || created.SyncMode != api.SyncModeTwoWay {
		t.Errorf("create body %v, answer %+v", f.last().Body, created)
	}

	mirror := api.SyncModeMirror
	updated, err := c.UpdateProfile(ctx, "docs", api.ProfileUpdateRequest{SyncMode: &mirror})
	if err != nil {
		t.Fatal(err)
	}
	if b := f.last().Body; len(b) != 1 || b["sync_mode"] != "mirror" || updated.SyncMode != api.SyncModeMirror {
		t.Errorf("update body %v, answer %+v", b, updated)
	}
	name := "Docs"
	if _, err := c.UpdateProfile(ctx, "docs", api.ProfileUpdateRequest{Name: &name}); err != nil {
		t.Fatal(err)
	}
	if _, ok := f.last().Body["sync_mode"]; ok {
		t.Errorf("an update without a mode must leave it out: %v", f.last().Body)
	}
}

func TestWizardOAuthRedirectURI(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/wizard/oauth/redirect-uri", 200, map[string]any{
		"redirect_uri": "http://127.0.0.1:8000/wizard/oauth/callback",
	})
	c := f.client("")
	resp, err := c.OAuthRedirectURI(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if resp.RedirectURI != "http://127.0.0.1:8000/wizard/oauth/callback" {
		t.Errorf("resp = %+v", resp)
	}
	if last := f.last(); last.Method != "GET" || last.Path != "/wizard/oauth/redirect-uri" {
		t.Errorf("sent %s %s", last.Method, last.Path)
	}
}

// R5.7 / R5.9: the directory browsers send the path as a query value.
func TestBrowse_Endpoints(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/browse/local", 200, map[string]any{"current": "/home/u", "parent": nil,
		"entries": []any{map[string]any{"name": "Meine Dateien", "path": "/home/u/Meine Dateien"}}})
	f.on("GET", "/browse/remote", 200, map[string]any{"current": "gdrive:A b", "parent": "gdrive:", "entries": []any{}})
	c := f.client("")
	ctx := context.Background()

	got, err := c.BrowseLocal(ctx, "")
	if err != nil {
		t.Fatal(err)
	}
	if r := f.last(); r.Path != "/browse/local" || r.RawQuery != "" {
		t.Errorf("sent %s?%s", r.Path, r.RawQuery)
	}
	if got.Current != "/home/u" || got.Parent != nil || len(got.Entries) != 1 || got.Entries[0].Name != "Meine Dateien" {
		t.Errorf("decoded %+v", got)
	}
	if _, err = c.BrowseLocal(ctx, "/home/u/Meine Dateien"); err != nil {
		t.Fatal(err)
	}
	if q := f.last().RawQuery; q != "path=%2Fhome%2Fu%2FMeine+Dateien" {
		t.Errorf("query = %q", q)
	}
	rem, err := c.BrowseRemote(ctx, "gdrive:A b")
	if err != nil {
		t.Fatal(err)
	}
	if r := f.last(); r.Path != "/browse/remote" || r.RawQuery != "path=gdrive%3AA+b" {
		t.Errorf("sent %s?%s", r.Path, r.RawQuery)
	}
	if rem.Parent == nil || *rem.Parent != "gdrive:" {
		t.Errorf("decoded %+v", rem)
	}
}

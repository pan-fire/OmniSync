// Package contract_test checks the TUI's API types against fixtures generated
// from the backend's own Pydantic models (see ../fixtures/gen_fixtures.py).
//
// Response fixtures are the backend's model_dump_json() output, so decoding
// them proves the Go json tags, nesting and types match what the backend
// sends. Request schemas are the backend's model_json_schema(), so the check
// proves every key the TUI sends is one the backend accepts.
package contract_test

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// optionalKeys lists Go json keys the backend may not send yet, as
// "TypeName.key". Each entry needs a reason.
var optionalKeys = map[string]string{}

// extraRequestKeys lists request keys the TUI sends that the backend model in
// this tree does not declare yet (Pydantic ignores unknown keys).
var extraRequestKeys = map[string]string{}

func readFixture(t *testing.T, name string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "fixtures", name))
	if err != nil {
		t.Fatalf("read fixture %s: %v (run tui/test/fixtures/gen_fixtures.py)", name, err)
	}
	return data
}

// decodeFixture decodes responses/<model>.json into T and checks that every
// json key T declares is present in the fixture.
func decodeFixture[T any](t *testing.T, model string) T {
	t.Helper()
	data := readFixture(t, filepath.Join("responses", model+".json"))
	var out T
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatalf("%s: decode into %T: %v", model, out, err)
	}
	var generic any
	if err := json.Unmarshal(data, &generic); err != nil {
		t.Fatalf("%s: %v", model, err)
	}
	requireKeys(t, reflect.TypeOf(out), generic, model)
	return out
}

// requireKeys walks the Go type and fails for every json key it declares that
// is missing from the fixture value.
func requireKeys(t *testing.T, typ reflect.Type, value any, path string) {
	t.Helper()
	for typ.Kind() == reflect.Pointer {
		typ = typ.Elem()
	}
	switch typ.Kind() {
	case reflect.Struct:
		obj, ok := value.(map[string]any)
		if !ok {
			t.Errorf("%s: expected a JSON object, got %T", path, value)
			return
		}
		checkStructKeys(t, typ, obj, path, typ.Name())
	case reflect.Slice:
		items, ok := value.([]any)
		if !ok {
			if value != nil {
				t.Errorf("%s: expected a JSON array, got %T", path, value)
			}
			return
		}
		for _, item := range items {
			requireKeys(t, typ.Elem(), item, path+"[]")
		}
	case reflect.Map:
		obj, ok := value.(map[string]any)
		if !ok {
			if value != nil {
				t.Errorf("%s: expected a JSON object, got %T", path, value)
			}
			return
		}
		for k, v := range obj {
			requireKeys(t, typ.Elem(), v, path+"."+k)
		}
	}
}

func checkStructKeys(t *testing.T, typ reflect.Type, obj map[string]any, path, typeName string) {
	t.Helper()
	for i := 0; i < typ.NumField(); i++ {
		f := typ.Field(i)
		if f.Anonymous {
			checkStructKeys(t, f.Type, obj, path, typeName)
			continue
		}
		key := strings.Split(f.Tag.Get("json"), ",")[0]
		if key == "" || key == "-" || !f.IsExported() {
			continue
		}
		v, present := obj[key]
		if !present {
			if _, ok := optionalKeys[typeName+"."+key]; ok {
				continue
			}
			t.Errorf("%s: Go field %s.%s expects key %q, which the backend does not send", path, typeName, f.Name, key)
			continue
		}
		if v != nil {
			requireKeys(t, f.Type, v, path+"."+key)
		}
	}
}

func TestContract_ErrorResponse(t *testing.T) {
	e := decodeFixture[api.ErrorResponse](t, "ErrorResponse")
	if e.Detail == "" || e.Code != "name_clash" || e.Details["names"] == nil || e.RequestID == "" {
		t.Errorf("ErrorResponse = %+v", e)
	}
}

func TestContract_Health(t *testing.T) {
	h := decodeFixture[api.HealthResponse](t, "HealthResponse")
	if h.Status != "ok" || !h.Healthy() || !h.RcloneInstalled || !h.DatabaseOK || h.UptimeSeconds == 0 {
		t.Errorf("unexpected %+v", h)
	}
	if h.Version != "0.12.0" {
		t.Errorf("version = %q", h.Version)
	}
}

func TestContract_RemotesHealth(t *testing.T) {
	r := decodeFixture[api.RemoteHealthResponse](t, "RemoteHealthResponse")
	if len(r.Remotes) != 1 || r.Remotes[0].Remote != "gdrive" || r.Remotes[0].Accessible ||
		len(r.Remotes[0].Profiles) != 1 || r.Remotes[0].Profiles[0] != "docs" {
		t.Errorf("unexpected %+v", r)
	}
}

func TestContract_Browse(t *testing.T) {
	b := decodeFixture[api.BrowseResponse](t, "BrowseResponse")
	if b.Current != "gdrive:Backup" || b.Parent == nil || *b.Parent != "gdrive:" ||
		len(b.Entries) != 1 || b.Entries[0].Name != "Dokumente Übersicht" || b.Entries[0].Path != "gdrive:Backup/Dokumente Übersicht" {
		t.Errorf("unexpected %+v", b)
	}
}

func TestContract_SyncStatus(t *testing.T) {
	s := decodeFixture[api.SyncStatusResponse](t, "SyncStatusResponse")
	if s.State != api.SyncStateError || s.LastSync == nil || s.CurrentJobID == nil || *s.CurrentJobID != 42 ||
		s.FilesProcessed != 12 || s.Errors != 3 || s.PendingChanges != 5 || !s.IntervalsPaused || s.PausedAt == nil {
		t.Errorf("unexpected %+v", s)
	}
	if s.LastError == nil || !strings.Contains(*s.LastError, "limit 50") {
		t.Errorf("last_error not decoded: %v", s.LastError)
	}
	if !s.ResyncRequired {
		t.Error("resync_required not decoded")
	}
}

func TestContract_SyncStartStop(t *testing.T) {
	start := decodeFixture[api.SyncStartResponse](t, "SyncStartResponse")
	if start.JobID != 42 || start.State != api.SyncStateSyncing || !start.State.Busy() {
		t.Errorf("unexpected %+v", start)
	}
	stop := decodeFixture[api.SyncStopResponse](t, "SyncStopResponse")
	if stop.State != api.SyncStateIdle || stop.Message == "" {
		t.Errorf("unexpected %+v", stop)
	}
}

func TestContract_SyncPreview(t *testing.T) {
	p := decodeFixture[api.SyncPreviewResponse](t, "SyncPreviewResponse")
	if p.Push.Deletes != 120 || p.Push.Replaces != 3 || p.Push.Creates != 4 || !p.Push.ExceedsMaxDelete {
		t.Errorf("unexpected push %+v", p.Push)
	}
	if p.Pull.Deletes != 2 || p.Pull.Replaces != 3 || p.Pull.Creates != 120 || p.Pull.ExceedsMaxDelete {
		t.Errorf("unexpected pull %+v", p.Pull)
	}
	if p.Excluded != 5 || p.MaxDelete == nil || *p.MaxDelete != 25 || p.Error == nil || *p.Error == "" {
		t.Errorf("unexpected %+v", p)
	}
	if p.Counts(api.SyncDirectionPush) != p.Push || p.Counts(api.SyncDirectionPull) != p.Pull {
		t.Error("Counts picks the wrong direction")
	}
	if p.SyncMode != api.SyncModeTwoWay || p.TwoWay == nil {
		t.Fatalf("two-way preview not decoded: mode %q, two_way %+v", p.SyncMode, p.TwoWay)
	}
	tw := p.TwoWay
	if tw.Local != (api.SyncPreviewCounts{Deletes: 1, Replaces: 2, Creates: 3}) {
		t.Errorf("unexpected local %+v", tw.Local)
	}
	if tw.Remote != (api.SyncPreviewCounts{Deletes: 60, Replaces: 4, Creates: 5, ExceedsMaxDelete: true}) {
		t.Errorf("unexpected remote %+v", tw.Remote)
	}
	if tw.Conflicts != 2 || !tw.Resync || !tw.ResyncRequired || tw.Error == nil || *tw.Error != "bisync dry run failed" {
		t.Errorf("unexpected two_way %+v", tw)
	}
	// A mirror profile's preview has no two-way part.
	var mirror api.SyncPreviewResponse
	if err := json.Unmarshal([]byte(`{"sync_mode":"mirror","two_way":null}`), &mirror); err != nil ||
		mirror.SyncMode != api.SyncModeMirror || mirror.TwoWay != nil {
		t.Errorf("mirror preview: %v %+v", err, mirror)
	}
}

func TestContract_Diff(t *testing.T) {
	d := decodeFixture[api.DiffResponse](t, "DiffResponse")
	if len(d.Files) != 1 {
		t.Fatalf("expected 1 file, got %d", len(d.Files))
	}
	f := d.Files[0]
	if f.Path != "docs/ä.txt" || f.Category != api.ChangeCategoryModifiedBoth || f.LocalSize == nil || *f.LocalSize != 1024 ||
		f.RemoteSize == nil || *f.RemoteSize != 2048 || f.LocalModTime == nil || f.RemoteModTime == nil || !f.IsConflict || !f.ManualFlag {
		t.Errorf("unexpected file %+v", f)
	}
	if d.Summary.Total != 21 || d.Summary.ModifiedBoth != 5 || d.Summary.Manual != 6 {
		t.Errorf("unexpected summary %+v", d.Summary)
	}
	if d.Pagination == nil || d.Pagination.Total != 21 || d.Pagination.Offset != 10 || d.Pagination.Limit != 5 || !d.Pagination.HasMore {
		t.Errorf("pagination not decoded: %+v", d.Pagination)
	}
	if d.Error == nil {
		t.Error("error not decoded")
	}
}

func TestContract_SelectiveSyncAndResume(t *testing.T) {
	s := decodeFixture[api.SelectiveSyncResponse](t, "SelectiveSyncResponse")
	if s.JobID != 43 || s.Status != api.JobStatusCompleted || s.Total != 3 || s.Succeeded != 2 || s.Failed != 1 || len(s.Errors) != 1 || s.Errors[0].Path == "" || s.Errors[0].Error == "" {
		t.Errorf("unexpected %+v", s)
	}
	r := decodeFixture[api.ResumeIntervalsResponse](t, "ResumeIntervalsResponse")
	if r.Detail == "" {
		t.Error("detail not decoded")
	}
}

func TestContract_Jobs(t *testing.T) {
	j := decodeFixture[api.SyncJobResponse](t, "SyncJobResponse")
	if j.ID != 42 || j.Direction != api.JobDirectionTwoWay || j.StartedAt == "" || j.FinishedAt == nil || j.Status != api.JobStatusCompleted ||
		j.FilesChanged != 17 || j.Conflicts != 1 || j.Errors != 2 || j.ProfileSlug == nil || *j.ProfileSlug != "docs" || j.ProfileName == nil {
		t.Errorf("unexpected %+v", j)
	}
	f := decodeFixture[api.FileChangeResponse](t, "FileChangeResponse")
	if f.ID != 9 || f.JobID != 42 || f.FilePath != "docs/report.pdf" || f.Action != "modified" || f.SizeBytes == nil || *f.SizeBytes != 4096 {
		t.Errorf("unexpected %+v", f)
	}
	if f.Side == nil || *f.Side != api.FileSideRemote {
		t.Errorf("side not decoded: %v", f.Side)
	}
	// Changes recorded before the side was known carry null.
	var old api.FileChangeResponse
	if err := json.Unmarshal([]byte(`{"id":1,"job_id":2,"file_path":"a","action":"created","size_bytes":null,"side":null}`), &old); err != nil || old.Side != nil {
		t.Errorf("null side: %v %+v", err, old)
	}
	// Every job direction the backend sends decodes into its constant.
	for _, dir := range []api.JobDirection{api.JobDirectionPush, api.JobDirectionPull, api.JobDirectionSelective,
		api.JobDirectionTwoWay, api.JobDirectionResync} {
		var job api.SyncJobResponse
		if err := json.Unmarshal([]byte(`{"direction":"`+string(dir)+`"}`), &job); err != nil || job.Direction != dir {
			t.Errorf("direction %s: %v %+v", dir, err, job)
		}
	}
}

func TestContract_Conflict(t *testing.T) {
	c := decodeFixture[api.ConflictResponse](t, "ConflictResponse")
	if c.ID != 5 || c.JobID == nil || *c.JobID != 42 || c.FilePath == "" || c.LocalModified == nil || c.RemoteModified == nil || !c.Resolved ||
		c.Resolution == nil || *c.Resolution != api.ConflictKeepBoth {
		t.Errorf("unexpected %+v", c)
	}
	if c.ProfileSlug == nil || *c.ProfileSlug != "docs" || c.ProfileName == nil || *c.ProfileName != "Dokumente" {
		t.Errorf("profile not decoded: %v %v", c.ProfileSlug, c.ProfileName)
	}
	if c.LocalKeptAs == nil || *c.LocalKeptAs != "docs/plan.local-conflict1.odt" || c.RemoteKeptAs == nil || *c.RemoteKeptAs != "docs/plan.odt" {
		t.Errorf("kept_as not decoded: %v %v", c.LocalKeptAs, c.RemoteKeptAs)
	}
	if !c.TwoWay() {
		t.Error("a conflict with kept_as names is a two-way conflict")
	}
	// A conflict found by a diff has no job and no kept_as names.
	var noJob api.ConflictResponse
	if err := json.Unmarshal([]byte(`{"id":1,"job_id":null,"file_path":"a","profile_slug":null,"profile_name":null,"local_kept_as":null,"remote_kept_as":null}`), &noJob); err != nil ||
		noJob.JobID != nil || noJob.LocalKeptAs != nil || noJob.RemoteKeptAs != nil || noJob.TwoWay() {
		t.Errorf("null job_id/kept_as: %v %+v", err, noJob)
	}
}

func TestContract_Remotes(t *testing.T) {
	r := decodeFixture[api.RemoteResponse](t, "RemoteResponse")
	if r.Name != "gdrive" || r.Type != "drive" || r.LastVerified == nil {
		t.Errorf("unexpected %+v", r)
	}
	if r.ProviderID == nil || *r.ProviderID != "drive" || r.Editable || !r.Reconnectable || !r.AuthError {
		t.Errorf("capabilities not decoded: %+v", r)
	}
	rt := decodeFixture[api.RemoteTestResponse](t, "RemoteTestResponse")
	if rt.LatencyMs == nil || *rt.LatencyMs != 87 || rt.Error == nil || !rt.AuthError {
		t.Errorf("unexpected %+v", rt)
	}
	rc := decodeFixture[api.RemoteConfigResponse](t, "RemoteConfigResponse")
	if rc.Name != "box" || rc.ProviderID != "sftp" || len(rc.Fields) != 2 || rc.Fields[1].Value != "" ||
		!rc.Fields[1].Secret || !rc.Fields[1].IsSet || len(rc.OtherKeys) != 1 {
		t.Errorf("unexpected %+v", rc)
	}
	ip := decodeFixture[api.ImportPreviewResponse](t, "ImportPreviewResponse")
	if len(ip.Remotes) != 2 || !ip.Remotes[0].Exists || len(ip.Remotes[1].Problems) != 1 || len(ip.Errors) != 1 {
		t.Errorf("unexpected %+v", ip)
	}
	ir := decodeFixture[api.ImportRemotesResponse](t, "ImportRemotesResponse")
	if len(ir.Imported) != 1 {
		t.Errorf("unexpected %+v", ir)
	}
	si := decodeFixture[api.RemoteStorageInfoResponse](t, "RemoteStorageInfoResponse")
	if si.TotalBytes == nil || *si.TotalBytes != 1000 || si.UsedBytes == nil || si.FreeBytes == nil || si.TrashedBytes == nil || !si.Supported {
		t.Errorf("unexpected %+v", si)
	}
	deps := decodeFixture[api.RemoteDependenciesResponse](t, "RemoteDependenciesResponse")
	if len(deps.Profiles) != 1 || deps.Profiles[0].Slug != "docs" || deps.Profiles[0].Name == "" {
		t.Errorf("profiles not decoded as objects: %+v", deps.Profiles)
	}
	if len(deps.BackupTargets) != 1 || deps.BackupTargets[0].TargetID != 3 || deps.BackupTargets[0].TargetName != "Nightly" ||
		deps.BackupTargets[0].ProfileSlug != "docs" {
		t.Errorf("backup targets not decoded as objects: %+v", deps.BackupTargets)
	}
	if deps.Empty() {
		t.Error("Empty() should be false")
	}
}

func TestContract_Logs(t *testing.T) {
	l := decodeFixture[api.LogEntryResponse](t, "LogEntryResponse")
	if l.Timestamp == "" || l.Level != "WARNING" || l.Message == "" || l.Logger == "" || l.RequestID == "" || l.Exc == "" {
		t.Errorf("unexpected %+v", l)
	}
}

func TestContract_Wizard(t *testing.T) {
	p := decodeFixture[api.ProviderResponse](t, "ProviderResponse")
	if p.ID != "s3" || p.DisplayName == "" || p.Icon == "" || p.AuthType != api.AuthTypeKey || p.DefaultName == "" || p.SetupGuide == "" {
		t.Errorf("unexpected %+v", p)
	}
	if len(p.Fields) != 1 || p.Fields[0].Name != "secret_access_key" || p.Fields[0].Label == "" ||
		p.Fields[0].FieldType != api.FieldTypePassword || !p.Fields[0].Required || p.Fields[0].HelpText == "" ||
		len(p.Fields[0].Options) != 2 || p.Fields[0].Default != "standard" {
		t.Errorf("unexpected fields %+v", p.Fields)
	}
	a := decodeFixture[api.AuthorizeResponse](t, "AuthorizeResponse")
	if a.SessionID != "sess-123" || !strings.HasPrefix(a.AuthURL, "https://") ||
		a.RedirectURI != "http://127.0.0.1:8000/wizard/oauth/callback" {
		t.Errorf("unexpected %+v", a)
	}
	r := decodeFixture[api.OAuthRedirectResponse](t, "OAuthRedirectResponse")
	if r.RedirectURI != "http://127.0.0.1:8000/wizard/oauth/callback" {
		t.Errorf("unexpected %+v", r)
	}
	s := decodeFixture[api.WizardSessionResponse](t, "WizardSessionResponse")
	if s.SessionID != "sess-123" || s.Status != api.WizardFailed || s.AuthURL == nil || s.Error == nil {
		t.Errorf("unexpected %+v", s)
	}
	tr := decodeFixture[api.TestRemoteResponse](t, "TestRemoteResponse")
	if tr.Success || tr.Error == nil || !tr.AuthError {
		t.Errorf("unexpected %+v", tr)
	}
}

func TestContract_TestSync(t *testing.T) {
	ts := decodeFixture[api.TestSyncResponse](t, "TestSyncResponse")
	if ts.Success || ts.Error == nil || len(ts.Steps) != 1 {
		t.Errorf("unexpected %+v", ts)
	}
}

func TestContract_Notifications(t *testing.T) {
	cfg := decodeFixture[api.NotificationConfigResponse](t, "NotificationConfigResponse")
	if c, ok := cfg.Channels["desktop"]; !ok || c.Enabled || c.MinSeverity != api.SeverityError || c.Webhook != nil {
		t.Errorf("unexpected %+v", cfg)
	}
	if w := cfg.Channels["webhook"].Webhook; w == nil || w.URL == "" || !w.AllowHTTP || len(w.Headers) != 1 ||
		w.Headers[0].Name != "Authorization" || !w.Headers[0].ValueSet {
		t.Errorf("webhook %+v", w)
	}
	if n := cfg.Channels["ntfy"].Ntfy; n == nil || n.Server == "" || n.Topic == "" || n.Username == "" || n.TokenSet || !n.PasswordSet {
		t.Errorf("ntfy %+v", n)
	}
	if e := cfg.Channels["email"].Email; e == nil || e.Host == "" || e.Port != 465 || e.Security != "tls" || e.Username == "" ||
		!e.PasswordSet || e.FromAddr == "" || len(e.To) != 2 {
		t.Errorf("email %+v", e)
	}
	st := decodeFixture[api.ChannelStatusResponse](t, "ChannelStatusResponse")
	info, ok := st.Channels["desktop"]
	if !ok || !info.Available || info.DetectionMethod == nil || info.HostOS == nil || len(info.MissingDependencies) != 1 || info.PermissionStatus == nil {
		t.Errorf("unexpected %+v", st)
	}
	h := decodeFixture[api.NotificationHistoryResponse](t, "NotificationHistoryResponse")
	if h.Total != 31 || len(h.Items) != 1 || h.Items[0].ID != 11 || h.Items[0].Title == "" || h.Items[0].Severity != "error" ||
		h.Items[0].Timestamp == "" || len(h.Items[0].ChannelsDelivered) != 1 || h.Items[0].EventType == "" || h.Items[0].Body == "" {
		t.Errorf("unexpected %+v", h)
	}
	tn := decodeFixture[api.TestNotificationResponse](t, "TestNotificationResponse")
	if !tn.Success || len(tn.ChannelsDelivered) != 1 || tn.Errors["webpush"] == "" {
		t.Errorf("unexpected %+v", tn)
	}
}

func checkProfileBase(t *testing.T, p api.ProfileResponse) {
	t.Helper()
	if p.ID != 7 || p.Slug != "docs" || p.Name != "Dokumente Übersicht" || p.LocalDir == "" || p.RemoteDir == "" ||
		p.DebounceSeconds != 9 || p.PullIntervalMinutes != 15 || len(p.RcloneFilter) != 1 || len(p.RcloneArgs) != 2 ||
		p.MaxRetries != 4 || !p.Enabled || p.CreatedAt == "" || p.UpdatedAt == "" {
		t.Errorf("unexpected profile %+v", p)
	}
	if p.SyncMode != api.SyncModeTwoWay || !p.TwoWay() {
		t.Errorf("sync_mode not decoded: %q", p.SyncMode)
	}
}

func TestContract_Profiles(t *testing.T) {
	checkProfileBase(t, decodeFixture[api.ProfileResponse](t, "ProfileResponse"))

	ps := decodeFixture[api.ProfileStatusResponse](t, "ProfileStatusResponse")
	checkProfileBase(t, ps.ProfileResponse)
	if ps.State != api.SyncStatePulling || ps.LastSync == nil || ps.CurrentJobID == nil || ps.FilesProcessed != 12 ||
		ps.Errors != 1 || ps.PendingChanges != 5 || !ps.IntervalsPaused || ps.PausedAt == nil {
		t.Errorf("unexpected status %+v", ps)
	}
	if ps.LastError == nil || !strings.Contains(*ps.LastError, "would delete 120 files") {
		t.Errorf("last_error not decoded: %v", ps.LastError)
	}
	if ps.MaxDelete == nil || *ps.MaxDelete != 25 {
		t.Errorf("max_delete not decoded: %v", ps.MaxDelete)
	}
	if !ps.ResyncRequired {
		t.Error("resync_required not decoded")
	}
	var mirror api.ProfileStatusResponse
	if err := json.Unmarshal([]byte(`{"slug":"x","sync_mode":"mirror","resync_required":false}`), &mirror); err != nil ||
		mirror.SyncMode != api.SyncModeMirror || mirror.TwoWay() || mirror.ResyncRequired {
		t.Errorf("mirror profile: %v %+v", err, mirror)
	}
	if ps.MirrorNoticeDismissed || ps.ShowMirrorNotice() || !mirror.ShowMirrorNotice() {
		t.Errorf("mirror notice: two-way %+v, mirror %+v", ps.ProfileResponse, mirror.ProfileResponse)
	}
	var dismissed api.ProfileStatusResponse
	if err := json.Unmarshal([]byte(`{"slug":"x","sync_mode":"mirror","mirror_notice_dismissed":true}`), &dismissed); err != nil ||
		!dismissed.MirrorNoticeDismissed || dismissed.ShowMirrorNotice() {
		t.Errorf("dismissed mirror notice: %v %+v", err, dismissed)
	}
	var unlimited api.ProfileStatusResponse
	if err := json.Unmarshal([]byte(`{"slug":"x","max_delete":null,"last_error":null}`), &unlimited); err != nil ||
		unlimited.MaxDelete != nil || unlimited.LastError != nil {
		t.Errorf("null max_delete/last_error: %v %+v", err, unlimited)
	}
}

func TestContract_GlobalConfig(t *testing.T) {
	c := decodeFixture[api.GlobalConfigResponse](t, "GlobalConfigResponse")
	if c.LogLevel != "DEBUG" || c.HistoryDays != 30 {
		t.Errorf("unexpected %+v", c)
	}
}

func TestContract_AggregateStatus(t *testing.T) {
	a := decodeFixture[api.AggregateStatusResponse](t, "AggregateStatusResponse")
	if a.OverallState != api.SyncStatePushing || a.TotalPendingChanges != 8 {
		t.Errorf("unexpected %+v", a)
	}
	if len(a.PausedProfiles) != 1 || a.PausedProfiles[0].Slug != "docs" || a.PausedProfiles[0].PausedAt == nil {
		t.Errorf("unexpected paused %+v", a.PausedProfiles)
	}
	if len(a.ProfilesSummary) != 1 || a.ProfilesSummary[0].State != api.SyncStatePushing || a.ProfilesSummary[0].LastSync == nil ||
		a.ProfilesSummary[0].PendingChanges != 8 || !a.ProfilesSummary[0].IntervalsPaused {
		t.Errorf("unexpected summary %+v", a.ProfilesSummary)
	}
	if a.ProfilesSummary[0].LastError == nil || !strings.Contains(*a.ProfilesSummary[0].LastError, "missing or not mounted") {
		t.Errorf("summary last_error not decoded: %v", a.ProfilesSummary[0].LastError)
	}
	if !a.ProfilesSummary[0].ResyncRequired {
		t.Error("summary resync_required not decoded")
	}
}

func TestContract_Backups(t *testing.T) {
	b := decodeFixture[api.BackupTargetResponse](t, "BackupTargetResponse")
	if b.ID != 3 || b.ProfileID != 7 || b.Name != "Nightly" || b.TargetPath == "" || b.TargetType != api.BackupTargetCustomRemote ||
		b.RemoteName == nil || b.RetentionDays != 30 || b.FrequencyHours != 24 || b.BackupMode != api.BackupModeArchive ||
		!b.Enabled || b.LastLivenessOK == nil || b.LastLivenessError == nil || b.LastBackupAt == nil ||
		b.LastBackupStatus == nil || *b.LastBackupStatus != "failed" || b.NextScheduledAt == nil || b.CreatedAt == "" {
		t.Errorf("unexpected %+v", b)
	}
	j := decodeFixture[api.BackupJobResponse](t, "BackupJobResponse")
	if j.ID != 77 || j.TargetID != 3 || j.StartedAt == "" || j.FinishedAt == nil || j.Status != api.BackupJobCompleted ||
		j.Direction != "backup" || j.SizeBytes == nil || j.SnapshotID == nil || j.ErrorMessage == nil || j.ErrorCode != nil {
		t.Errorf("unexpected %+v", j)
	}
	if !b.Encrypted || !b.VerifyAfterBackup || b.LastVerifyStatus == nil || *b.LastVerifyStatus != "failed" ||
		b.LastVerifyMessage == nil {
		t.Errorf("encryption/verification not decoded: %+v", b)
	}
	if j.VerifyStatus == nil || *j.VerifyStatus != "verified" || j.VerifyMessage == nil {
		t.Errorf("job verification not decoded: %+v", j)
	}
	s := decodeFixture[api.SnapshotResponse](t, "SnapshotResponse")
	if s.SnapshotID == "" || s.CreatedAt == "" || s.SizeBytes == nil || *s.SizeBytes != 123456 || s.Status != "completed" ||
		!s.Latest {
		t.Errorf("unexpected %+v", s)
	}
	f := decodeFixture[api.SnapshotFilesResponse](t, "SnapshotFilesResponse")
	if f.Total != 42 || f.Offset != 10 || f.Limit != 2 || f.SnapshotFiles != 99 || f.Path != "Berichte" || len(f.Entries) != 2 {
		t.Errorf("unexpected %+v", f)
	}
	if d := f.Entries[0]; !d.IsDir || d.FileCount == nil || *d.FileCount != 3 || d.Size == nil || d.Name != "Übersicht" {
		t.Errorf("unexpected folder entry %+v", d)
	}
	if e := f.Entries[1]; e.IsDir || e.ModTime == nil || e.Size == nil || *e.Size != 12 {
		t.Errorf("unexpected file entry %+v", e)
	}
	p := decodeFixture[api.RestorePreviewResponse](t, "RestorePreviewResponse")
	if p.RestoreScope != api.RestoreScopeBoth || len(p.Sides) != 1 {
		t.Fatalf("unexpected %+v", p)
	}
	if side := p.Sides[0]; side.Side != "local" || side.Added != 1 || side.Replaced != 2 || side.Removed != 3 ||
		side.Unchanged != 4 || len(side.RemovedExamples) != 1 {
		t.Errorf("unexpected side %+v", side)
	}
}

// --- Requests ---

type jsonSchema struct {
	Properties map[string]json.RawMessage `json:"properties"`
	Required   []string                   `json:"required"`
	Defs       map[string]json.RawMessage `json:"$defs"`
}

type propSchema struct {
	Ref   string            `json:"$ref"`
	Enum  []any             `json:"enum"`
	AnyOf []json.RawMessage `json:"anyOf"`
	Items json.RawMessage   `json:"items"`
	// Nested object schemas (resolved $defs) carry properties too.
	Properties map[string]json.RawMessage `json:"properties"`
	Required   []string                   `json:"required"`
}

func loadSchema(t *testing.T, model string) jsonSchema {
	t.Helper()
	var s jsonSchema
	if err := json.Unmarshal(readFixture(t, filepath.Join("requests", model+".schema.json")), &s); err != nil {
		t.Fatalf("%s: %v", model, err)
	}
	return s
}

func resolve(raw json.RawMessage, defs map[string]json.RawMessage) (propSchema, error) {
	var p propSchema
	if err := json.Unmarshal(raw, &p); err != nil {
		return p, err
	}
	if p.Ref != "" {
		name := p.Ref[strings.LastIndex(p.Ref, "/")+1:]
		def, ok := defs[name]
		if !ok {
			return p, fmt.Errorf("unknown $ref %s", p.Ref)
		}
		return resolve(def, defs)
	}
	for _, alt := range p.AnyOf {
		r, err := resolve(alt, defs)
		if err != nil {
			return p, err
		}
		if r.Enum != nil || r.Properties != nil || r.Items != nil {
			return r, nil
		}
	}
	return p, nil
}

// requestChecker compares a marshaled request with the backend's schema and
// collects every mismatch.
type requestChecker struct {
	model    string
	defs     map[string]json.RawMessage
	problems []string
}

func (c *requestChecker) addf(format string, args ...any) {
	c.problems = append(c.problems, fmt.Sprintf(format, args...))
}

func (c *requestChecker) object(path string, obj map[string]any, props map[string]json.RawMessage, required []string) {
	for key, value := range obj {
		raw, ok := props[key]
		if !ok {
			if _, allowed := extraRequestKeys[c.model+"."+key]; allowed && path == "" {
				continue
			}
			c.addf("%s%s: the TUI sends key %q, which the backend model does not accept", c.model, path, key)
			continue
		}
		schema, err := resolve(raw, c.defs)
		if err != nil {
			c.addf("%s%s.%s: %v", c.model, path, key, err)
			continue
		}
		c.value(path+"."+key, value, schema)
	}
	for _, key := range required {
		if _, ok := obj[key]; !ok {
			c.addf("%s%s: required key %q is missing from the TUI's request", c.model, path, key)
		}
	}
}

func (c *requestChecker) value(path string, value any, schema propSchema) {
	switch v := value.(type) {
	case string:
		if schema.Enum != nil {
			for _, e := range schema.Enum {
				if e == v {
					return
				}
			}
			c.addf("%s%s: value %q is not one of the backend's %v", c.model, path, v, schema.Enum)
		}
	case map[string]any:
		if schema.Properties != nil {
			c.object(path, v, schema.Properties, schema.Required)
		}
	case []any:
		if schema.Items != nil {
			item, err := resolve(schema.Items, c.defs)
			if err != nil {
				c.addf("%s%s: %v", c.model, path, err)
				return
			}
			for _, elem := range v {
				c.value(path+"[]", elem, item)
			}
		}
	}
}

func requestProblems(t *testing.T, model string, req any) []string {
	t.Helper()
	data, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	var obj map[string]any
	if err := json.Unmarshal(data, &obj); err != nil {
		t.Fatal(err)
	}
	s := loadSchema(t, model)
	c := &requestChecker{model: model, defs: s.Defs}
	c.object("", obj, s.Properties, s.Required)
	return c.problems
}

func checkRequest(t *testing.T, model string, req any) {
	t.Helper()
	for _, p := range requestProblems(t, model, req) {
		t.Error(p)
	}
}

func ptr[T any](v T) *T { return &v }

func TestContract_Requests(t *testing.T) {
	filters := []string{"- *.tmp"}
	args := []string{"--transfers", "4"}
	enabled := true
	cases := map[string]any{
		"ProfileCreateRequest": api.ProfileCreateRequest{
			Name: "Docs", LocalDir: "/home/u/Docs", RemoteDir: "gdrive:Docs", DebounceSeconds: 5,
			PullIntervalMinutes: 5, RcloneFilter: filters, RcloneArgs: args, MaxRetries: 3,
			SyncMode: api.SyncModeTwoWay,
		},
		"ProfileUpdateRequest": api.ProfileUpdateRequest{
			Name: ptr("Docs"), LocalDir: ptr("/home/u/Docs"), RemoteDir: ptr("gdrive:Docs"), DebounceSeconds: ptr(5),
			PullIntervalMinutes: ptr(5), RcloneFilter: &filters, RcloneArgs: &args, MaxRetries: ptr(3),
			SyncMode: ptr(api.SyncModeMirror), MirrorNoticeDismissed: ptr(true),
		},
		"SyncStartRequest": api.SyncStartRequest{Direction: api.SyncDirectionPull, Force: true},
		"ResyncRequest":    api.ResyncRequest{Confirm: true},
		"SelectiveSyncRequest": api.SelectiveSyncRequest{Items: []api.SelectiveSyncItem{
			{Path: "a", Action: api.FileActionPush}, {Path: "b", Action: api.FileActionPull},
			{Path: "c", Action: api.FileActionSkip}, {Path: "d", Action: api.FileActionManual},
			{Path: "e", Action: api.FileActionKeepBoth},
		}},
		"ConflictResolveRequest": api.ConflictResolveRequest{Resolution: api.ConflictKeepRemote},
		"AuthorizeRequest": api.AuthorizeRequest{
			ProviderID: "gdrive", ClientID: ptr("id"), ClientSecret: ptr("secret"), RemoteName: ptr("gdrive"),
		},
		"ReconnectRemoteRequest": api.ReconnectRemoteRequest{Name: "gdrive", SessionID: "sess-1"},
		"UpdateRemoteRequest": api.UpdateRemoteRequest{
			Params: map[string]string{"host": "h"}, Clear: []string{"pass"},
		},
		"ImportConfigRequest": api.ImportConfigRequest{Content: "[a]\ntype = drive\n"},
		"ImportRemotesRequest": api.ImportRemotesRequest{
			Content: "[a]\ntype = drive\n", Remotes: []api.ImportRemoteSelection{{Source: "a", Name: ptr("b")}},
		},
		"CreateRemoteRequest": api.CreateRemoteRequest{
			Name: "mys3", ProviderID: "s3", Params: map[string]string{"access_key_id": "AK"}, SessionID: ptr("sess-1"),
		},
		"TestRemoteRequest": api.TestRemoteRequest{Name: "mys3"},
		"TestSyncRequest":   api.TestSyncRequest{LocalDir: "/l", RemoteDir: "r:x"},
		"BackupTargetCreateRequest": api.BackupTargetCreateRequest{
			Name: "Nightly", TargetPath: "/backups", TargetType: api.BackupTargetCustomRemote, RemoteName: ptr("b2"),
			RetentionDays: 7, FrequencyHours: 24, BackupMode: api.BackupModeArchive, Enabled: &enabled,
			EncryptionPassphrase: ptr("correct horse"), VerifyAfterBackup: &enabled,
		},
		"BackupTargetUpdateRequest": api.BackupTargetUpdateRequest{
			Name: ptr("Nightly"), TargetPath: ptr("/backups"), TargetType: ptr(api.BackupTargetRemote), RemoteName: ptr("b2"),
			RetentionDays: ptr(7), FrequencyHours: ptr(24), BackupMode: ptr(api.BackupModeMirror), Enabled: &enabled,
			VerifyAfterBackup: &enabled,
		},
		"RestoreRequest": api.RestoreRequest{SnapshotID: "snap", RestoreScope: api.RestoreScopeLocalOnly},
		"RestoreFilesRequest": api.RestoreFilesRequest{
			SnapshotID: "snap", Paths: []string{"a.txt", "docs"}, TargetDir: ptr("/restored"),
		},
		"NotificationConfigUpdateRequest": api.NotificationConfigUpdateRequest{
			Channels: map[string]api.ChannelConfigUpdate{
				"desktop": {Enabled: &enabled, MinSeverity: ptr(api.SeverityInfo)},
				"webhook": {Webhook: &api.WebhookSettingsUpdate{
					URL: ptr("https://hooks.example.com/x"), AllowHTTP: ptr(false),
					Headers: &[]api.WebhookHeaderUpdate{{Name: "Authorization", Value: "Bearer t"}},
				}},
				"ntfy": {Ntfy: &api.NtfySettingsUpdate{
					Server: ptr("https://ntfy.sh"), Topic: ptr("nas"), AllowHTTP: ptr(false), Username: ptr("u"),
					Token: ptr("tk"), Password: ptr("pw"), Clear: []string{"token", "password"},
				}},
				"email": {Email: &api.EmailSettingsUpdate{
					Host: ptr("smtp.example.com"), Port: ptr(587), Security: ptr("starttls"), Username: ptr("u"),
					Password: ptr("pw"), FromAddr: ptr("a@b.c"), To: &[]string{"d@e.f"}, Clear: []string{"password"},
				}},
			},
		},
		"GlobalConfigUpdateRequest": api.GlobalConfigUpdateRequest{LogLevel: ptr("INFO"), HistoryDays: ptr(0)},
		"TestNotificationRequest":   api.TestNotificationRequest{Channel: ptr("webhook")},
	}
	for model, req := range cases {
		t.Run(model, func(t *testing.T) { checkRequest(t, model, req) })
	}

	// The other enum constants must be accepted too.
	checkRequest(t, "RestoreRequest", api.RestoreRequest{SnapshotID: "s", RestoreScope: api.RestoreScopeRemoteOnly})
	checkRequest(t, "RestoreRequest", api.RestoreRequest{SnapshotID: "s", RestoreScope: api.RestoreScopeBoth})
	checkRequest(t, "SyncStartRequest", api.SyncStartRequest{Direction: api.SyncDirectionPush})
	checkRequest(t, "SyncStartRequest", api.SyncStartRequest{Direction: api.SyncDirectionTwoWay, Force: true})
	checkRequest(t, "ProfileCreateRequest", api.ProfileCreateRequest{Name: "n", LocalDir: "/l", RemoteDir: "r:x", SyncMode: api.SyncModeMirror})
	checkRequest(t, "ProfileUpdateRequest", api.ProfileUpdateRequest{SyncMode: ptr(api.SyncModeTwoWay)})
	checkRequest(t, "ConflictResolveRequest", api.ConflictResolveRequest{Resolution: api.ConflictKeepLocal})
	checkRequest(t, "ConflictResolveRequest", api.ConflictResolveRequest{Resolution: api.ConflictKeepBoth})
	checkRequest(t, "ConflictResolveRequest", api.ConflictResolveRequest{Resolution: api.ConflictDismiss})
	checkRequest(t, "BackupTargetCreateRequest", api.BackupTargetCreateRequest{
		Name: "n", TargetPath: "/p", TargetType: api.BackupTargetLocal, BackupMode: api.BackupModeMirror,
	})
}

// TestContract_RequestChecksCatchDrift proves the request check is not
// vacuous: old field names and a bad enum value must be reported.
func TestContract_RequestChecksCatchDrift(t *testing.T) {
	old := map[string]any{"snapshot_id": "s", "scope": "both"}
	if p := requestProblems(t, "RestoreRequest", old); len(p) != 2 {
		t.Errorf("expected unknown 'scope' and missing 'restore_scope', got %v", p)
	}
	bad := map[string]any{"snapshot_id": "s", "restore_scope": "everything"}
	if p := requestProblems(t, "RestoreRequest", bad); len(p) != 1 {
		t.Errorf("expected the bad enum value to be reported, got %v", p)
	}
	oldBackup := map[string]any{"name": "n", "target_path": "/p", "target_type": "local", "mode": "archive"}
	if p := requestProblems(t, "BackupTargetCreateRequest", oldBackup); len(p) != 1 {
		t.Errorf("expected the old 'mode' key to be reported, got %v", p)
	}
	badMode := map[string]any{"name": "n", "local_dir": "/l", "remote_dir": "r:x", "sync_mode": "bisync"}
	if p := requestProblems(t, "ProfileCreateRequest", badMode); len(p) != 1 {
		t.Errorf("expected the bad sync_mode to be reported, got %v", p)
	}
	badDir := map[string]any{"direction": "resync"}
	if p := requestProblems(t, "SyncStartRequest", badDir); len(p) != 1 {
		t.Errorf("expected direction resync (a job direction, not a start direction) to be reported, got %v", p)
	}
}

// The resync body is exactly {"confirm": true}: the backend refuses a resync
// without it.
func TestContract_ResyncRequestConfirms(t *testing.T) {
	data, err := json.Marshal(api.ResyncRequest{Confirm: true})
	if err != nil || string(data) != `{"confirm":true}` {
		t.Errorf("body = %s, %v", data, err)
	}
	// An unset mode is left out so the backend applies its default.
	data, err = json.Marshal(api.ProfileCreateRequest{Name: "n", LocalDir: "/l", RemoteDir: "r:x"})
	if err != nil || strings.Contains(string(data), "sync_mode") {
		t.Errorf("create body = %s, %v", data, err)
	}
	data, err = json.Marshal(api.ProfileUpdateRequest{})
	if err != nil || string(data) != `{}` {
		t.Errorf("empty update body = %s, %v", data, err)
	}
	// Showing the mirror notice again sends an explicit false.
	data, err = json.Marshal(api.ProfileUpdateRequest{MirrorNoticeDismissed: ptr(false)})
	if err != nil || string(data) != `{"mirror_notice_dismissed":false}` {
		t.Errorf("notice update body = %s, %v", data, err)
	}
}

package contract_test

import (
	"encoding/json"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// Live progress, the user's pause, the sync window, the bandwidth limit and
// the trash: decoded from the backend's own models.

func checkProgress(t *testing.T, where string, p *api.SyncProgress) {
	t.Helper()
	if p == nil || p.Bytes != 21188608 || p.TotalBytes != 90000000 || p.Speed <= 0 || p.EtaSeconds == nil ||
		*p.EtaSeconds != 65 || p.FilesDone != 1 || p.FilesTotal != 3 || p.Checks != 4 || p.TotalChecks != 9 {
		t.Fatalf("%s: progress not decoded: %+v", where, p)
	}
	if len(p.CurrentFiles) != 1 || p.CurrentFiles[0].Name != "Fotos/Urlaub ä.jpg" || p.CurrentFiles[0].Size == nil ||
		p.CurrentFiles[0].Percentage == nil || *p.CurrentFiles[0].Percentage != 23 {
		t.Errorf("%s: current files not decoded: %+v", where, p.CurrentFiles)
	}
	if p.Percent() != 23 {
		t.Errorf("%s: percent = %d", where, p.Percent())
	}
}

func TestContract_SyncFeaturesStatus(t *testing.T) {
	s := decodeFixture[api.SyncStatusResponse](t, "SyncStatusResponse")
	checkProgress(t, "SyncStatusResponse", s.Progress)
	if !s.UserPaused || !s.OutsideSyncWindow || s.NextWindowStart == nil || !s.WaitingForWindow {
		t.Errorf("pause/window not decoded: %+v", s)
	}
	ps := decodeFixture[api.ProfileStatusResponse](t, "ProfileStatusResponse")
	checkProgress(t, "ProfileStatusResponse", ps.Progress)
	if !ps.UserPaused || !ps.OutsideSyncWindow || ps.NextWindowStart == nil || !ps.WaitingForWindow {
		t.Errorf("pause/window not decoded: %+v", ps)
	}
	if ps.Bwlimit == nil || *ps.Bwlimit != "08:00,512k 19:00,10M 23:00,off" {
		t.Errorf("bwlimit not decoded: %v", ps.Bwlimit)
	}
	if w := ps.SyncWindow; w == nil || len(w.Days) != 5 || w.Start != "22:00" || w.End != "06:00" {
		t.Errorf("sync_window not decoded: %+v", w)
	}
	a := decodeFixture[api.AggregateStatusResponse](t, "AggregateStatusResponse")
	checkProgress(t, "ProfileSummary", a.ProfilesSummary[0].Progress)
	if !a.ProfilesSummary[0].UserPaused || !a.PausedProfiles[0].UserPaused {
		t.Errorf("user_paused not decoded: %+v", a)
	}
	start := decodeFixture[api.SyncStartResponse](t, "SyncStartResponse")
	if start.Note == nil || *start.Note == "" {
		t.Errorf("note not decoded: %+v", start)
	}
	var idle api.SyncProgress
	if idle.Percent() != -1 {
		t.Error("a progress without totals has no percentage")
	}
}

func TestContract_PauseAllAndTrash(t *testing.T) {
	p := decodeFixture[api.PauseAllResponse](t, "PauseAllResponse")
	if len(p.Changed) != 1 || len(p.Unchanged) != 1 || p.StillPaused["docs"] == "" {
		t.Errorf("unexpected %+v", p)
	}
	l := decodeFixture[api.TrashListResponse](t, "TrashListResponse")
	if l.Side != api.TrashSideRemote || l.TotalFiles != 7 || l.TotalBytes != 123456 || !l.Truncated || len(l.Entries) != 1 {
		t.Fatalf("unexpected %+v", l)
	}
	e := l.Entries[0]
	if e.ID != e.Folder+"/"+e.Path || e.Size == nil || e.Modified == nil || e.TrashedAt == nil {
		t.Errorf("unexpected entry %+v", e)
	}
	r := decodeFixture[api.TrashActionResponse](t, "TrashActionResponse")
	if len(r.Done) != 1 || len(r.Failed) != 1 || r.Failed[0].Code != "target_newer" || r.Failed[0].Message == "" {
		t.Errorf("unexpected %+v", r)
	}
}

func TestContract_SyncFeaturesRequests(t *testing.T) {
	window := &api.SyncWindow{Days: []int{0, 6}, Start: "22:00", End: "06:00"}
	checkRequest(t, "ProfileCreateRequest", api.ProfileCreateRequest{
		Name: "n", LocalDir: "/l", RemoteDir: "r:x", Bwlimit: ptr("1M"), SyncWindow: window,
	})
	checkRequest(t, "ProfileUpdateRequest", api.ProfileUpdateRequest{
		Bwlimit: ptr("08:00,512k 23:00,off"), SyncWindow: &api.WindowUpdate{Window: window},
	})
	checkRequest(t, "TrashActionRequest", api.TrashActionRequest{Side: api.TrashSideLocal, IDs: []string{"T/a"}, Overwrite: true})
	checkRequest(t, "TrashActionRequest", api.TrashActionRequest{Side: api.TrashSideRemote, IDs: []string{"T/a"}})

	// Clearing: "" for the limit, null for the window; omitted: unchanged.
	data, err := json.Marshal(api.ProfileUpdateRequest{Bwlimit: ptr(""), SyncWindow: &api.WindowUpdate{}})
	if err != nil || string(data) != `{"bwlimit":"","sync_window":null}` {
		t.Errorf("clearing body = %s, %v", data, err)
	}
	data, err = json.Marshal(api.ProfileUpdateRequest{})
	if err != nil || string(data) != `{}` {
		t.Errorf("unchanged body = %s, %v", data, err)
	}
}

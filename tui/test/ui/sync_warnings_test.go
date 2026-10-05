package ui_test

import (
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// A path as hostile as a file name can be: a bell, a terminal title
// sequence and a newline. The backend escapes such paths; the view must
// not trust that.
const hostilePath = "evil\x07\x1b]0;owned\x07\nname.txt"

func warnedJob() map[string]any {
	job := jobsJSON(1, 100)[0].(map[string]any)
	job["warnings"] = []any{
		map[string]any{"code": "symlink_kept", "count": 23, "paths": []any{"clash.txt", hostilePath}},
		map[string]any{"code": "name_not_utf8", "count": 1, "paths": []any{"bad\\xff.txt"}},
		// A kind this client does not know yet still shows.
		map[string]any{"code": "new_kind", "count": 2, "paths": []any{}},
	}
	return job
}

// rawSafe fails when the raw frame carries the hostile path's control characters.
func rawSafe(t *testing.T, raw string) {
	t.Helper()
	if strings.Contains(raw, "\x07") || strings.Contains(raw, "\x1b]0;") || strings.Contains(raw, "owned\nname") {
		t.Errorf("server text reached the terminal unsanitised: %q", raw)
	}
}

// A completed job with warnings reads "warnings" in the history and
// "completed with warnings" in its detail, which lists each kind with its
// paths and how many more there are.
func TestProfileDetail_HistoryShowsJobWarnings(t *testing.T) {
	b := historyBackend(t)
	b.json("GET", "/jobs", 200, []any{warnedJob(), jobsJSON(1, 99)[0]})
	b.json("GET", "/jobs/100", 200, warnedJob())
	m := openHistory(t, b)
	if v := view(m); !strings.Contains(v, "warnings") || !strings.Contains(v, "completed") {
		t.Errorf("history:\n%s", v)
	}

	m = detailStep(t, m, press("enter"))
	v := view(m)
	for _, want := range []string{
		"[COMPLETED WITH WARNINGS]", "Warnings",
		"23 remote file(s) or folder(s) not synced: a local symbolic link has their name",
		"clash.txt", "evil\uFFFD\uFFFD]0;owned\uFFFD\uFFFDname.txt", "... and 21 more",
		"1 local name(s) are not valid UTF-8", `bad\xff.txt`, "2 file(s) need attention (new_kind)",
	} {
		if !strings.Contains(v, want) {
			t.Errorf("job detail lacks %q:\n%s", want, v)
		}
	}
	rawSafe(t, m.View().Content)
}

// The Jobs view says the same.
func TestJobs_ShowWarnings(t *testing.T) {
	b := newBackend(t)
	b.json("GET", "/jobs", 200, []any{warnedJob()})
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "D", "idle")})
	b.json("GET", "/jobs/100", 200, warnedJob())
	b.json("GET", "/jobs/100/files", 200, []any{})
	m := open(t, ui.NewJobsModel(b.client()))
	if !strings.Contains(content(m), "warnings") {
		t.Errorf("jobs list:\n%s", content(m))
	}
	m = drive(t, m, press("enter"))
	if v := content(m); !strings.Contains(v, "[COMPLETED WITH WARNINGS]") || !strings.Contains(v, "clash.txt") {
		t.Errorf("job detail:\n%s", v)
	}
	rawSafe(t, m.View().Content)
}

// The push confirmation and the diff list the warnings of the preview and
// the diff before anything runs.
func TestProfileDetail_PreviewAndDiffShowWarnings(t *testing.T) {
	b := detailBackend(t)
	preview := previewJSON(0, 0, 25)
	preview["warnings"] = []any{map[string]any{"code": "name_collision", "count": 1, "paths": []any{"café.txt"}}}
	b.json("POST", "/profiles/docs/sync/preview", 200, preview)
	b.json("POST", "/profiles/docs/diff", 200, map[string]any{
		"files": []any{}, "summary": map[string]any{"total": 0},
		"warnings": []any{map[string]any{"code": "symlink_shadow", "count": 1, "paths": []any{hostilePath}}},
	})
	m := openDetail(t, b)

	p := detailStep(t, m, press("p"))
	if v := view(p); !strings.Contains(v, "Not everything can be synced as it is") ||
		!strings.Contains(v, "1 local name(s) exist in two spellings") || !strings.Contains(v, "café.txt") {
		t.Errorf("push prompt:\n%s", v)
	}

	d := detailStep(t, m, press("right"))
	if v := view(d); !strings.Contains(v, "1 local symbolic link(s) have the name of a remote file or folder") {
		t.Errorf("diff:\n%s", v)
	}
	rawSafe(t, d.View().Content)
}

// HasWarnings: only a completed job with warnings has them.
func TestSyncJobHasWarnings(t *testing.T) {
	w := []api.SyncWarning{{Code: api.SyncWarningNameCollision, Count: 1}}
	cases := []struct {
		job  api.SyncJobResponse
		want bool
	}{
		{api.SyncJobResponse{Status: api.JobStatusCompleted, Warnings: w}, true},
		{api.SyncJobResponse{Status: api.JobStatusCompleted}, false},
		{api.SyncJobResponse{Status: api.JobStatusFailed, Warnings: w}, false},
	}
	for _, c := range cases {
		if got := c.job.HasWarnings(); got != c.want {
			t.Errorf("%+v: HasWarnings = %v", c.job, got)
		}
	}
}

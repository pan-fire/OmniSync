package ui_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"

	tea "charm.land/bubbletea/v2"
	uv "github.com/charmbracelet/ultraviolet"
	"github.com/charmbracelet/x/ansi"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// hostileRoutes answers every read a view makes with the backend's own
// contract fixture (test/fixtures/responses), so each view gets every
// field it can show.
var hostileRoutes = []struct {
	method  string
	path    *regexp.Regexp
	fixture string
	list    bool
}{
	{"GET", regexp.MustCompile(`^/profiles$`), "ProfileStatusResponse", true},
	{"GET", regexp.MustCompile(`^/profiles/[^/]+$`), "ProfileStatusResponse", false},
	{"GET", regexp.MustCompile(`^/profiles/[^/]+/sync/status$`), "SyncStatusResponse", false},
	{"POST", regexp.MustCompile(`^/profiles/[^/]+/sync/preview$`), "SyncPreviewResponse", false},
	{"POST", regexp.MustCompile(`^/profiles/[^/]+/diff$`), "DiffResponse", false},
	{"GET", regexp.MustCompile(`^/profiles/[^/]+/trash$`), "TrashListResponse", false},
	{"GET", regexp.MustCompile(`^/profiles/[^/]+/backups$`), "BackupTargetResponse", true},
	{"GET", regexp.MustCompile(`^/profiles/[^/]+/backups/\d+/snapshots$`), "SnapshotResponse", true},
	{"GET", regexp.MustCompile(`^/profiles/[^/]+/backups/\d+/snapshots/[^/]+/files$`), "SnapshotFilesResponse", false},
	{"GET", regexp.MustCompile(`^/profiles/[^/]+/backups/\d+/jobs/\d+$`), "BackupJobResponse", false},
	{"GET", regexp.MustCompile(`^/sync/status/aggregate$`), "AggregateStatusResponse", false},
	{"GET", regexp.MustCompile(`^/health$`), "HealthResponse", false},
	{"GET", regexp.MustCompile(`^/health/remotes$`), "RemoteHealthResponse", false},
	{"GET", regexp.MustCompile(`^/jobs$`), "SyncJobResponse", true},
	{"GET", regexp.MustCompile(`^/jobs/\d+$`), "SyncJobResponse", false},
	{"GET", regexp.MustCompile(`^/jobs/\d+/files$`), "FileChangeResponse", true},
	{"GET", regexp.MustCompile(`^/logs$`), "LogEntryResponse", true},
	{"GET", regexp.MustCompile(`^/conflicts$`), "ConflictResponse", true},
	{"GET", regexp.MustCompile(`^/remotes$`), "RemoteResponse", true},
	{"GET", regexp.MustCompile(`^/remotes/[^/]+/config$`), "RemoteConfigResponse", false},
	{"GET", regexp.MustCompile(`^/remotes/[^/]+/dependencies$`), "RemoteDependenciesResponse", false},
	{"GET", regexp.MustCompile(`^/remotes/[^/]+/about$`), "RemoteStorageInfoResponse", false},
	{"GET", regexp.MustCompile(`^/notifications/config$`), "NotificationConfigResponse", false},
	{"GET", regexp.MustCompile(`^/notifications/channels/status$`), "ChannelStatusResponse", false},
	{"GET", regexp.MustCompile(`^/notifications/history$`), "NotificationHistoryResponse", false},
	{"GET", regexp.MustCompile(`^/config$`), "GlobalConfigResponse", false},
	{"GET", regexp.MustCompile(`^/wizard/providers$`), "ProviderResponse", true},
	{"GET", regexp.MustCompile(`^/wizard/oauth/redirect-uri$`), "OAuthRedirectResponse", false},
	{"GET", regexp.MustCompile(`^/browse/(local|remote)$`), "BrowseResponse", false},
}

// enumKeys are the fields that hold one of a fixed set of values (a state,
// a mode): they keep the fixture's value, so the view takes the branch
// that shows the most text.
var enumKeys = map[string]bool{
	"action": true, "auth_type": true, "backup_mode": true, "category": true, "direction": true,
	"field_type": true, "min_severity": true, "overall_state": true, "resolution": true,
	"restore_scope": true, "side": true, "state": true, "status": true, "sync_mode": true,
	"target_type": true,
}

// poison appends s to every string in v except the enum fields, and puts
// a profile in the error state (which shows its last error).
func poison(v any, key, s string) any {
	switch x := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(x))
		for k, e := range x {
			out[k] = poison(e, k, s)
		}
		if _, ok := out["last_error"]; ok && out["state"] != nil {
			out["state"] = "error"
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, e := range x {
			out[i] = poison(e, key, s)
		}
		return out
	case string:
		if enumKeys[key] {
			return x
		}
		return x + s
	}
	return v
}

var (
	fixturesOnce sync.Once
	fixtures     map[string]any
)

func loadFixtures(tb testing.TB) map[string]any {
	fixturesOnce.Do(func() {
		fixtures = map[string]any{}
		paths, _ := filepath.Glob(filepath.Join("..", "fixtures", "responses", "*.json"))
		for _, p := range paths {
			data, err := os.ReadFile(p)
			if err != nil {
				continue
			}
			var v any
			if json.Unmarshal(data, &v) == nil {
				fixtures[strings.TrimSuffix(filepath.Base(p), ".json")] = v
			}
		}
	})
	if len(fixtures) == 0 {
		tb.Fatal("no contract fixtures in test/fixtures/responses")
	}
	return fixtures
}

// hostileBackend answers with the fixtures, every string carrying the
// text under test; with failing set, every request is refused with an
// error envelope that carries it.
type hostileBackend struct {
	mu      sync.Mutex
	text    string
	failing bool
	srv     *httptest.Server
}

func newHostileBackend(tb testing.TB) *hostileBackend {
	fx := loadFixtures(tb)
	h := &hostileBackend{}
	h.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h.mu.Lock()
		text, failing := h.text, h.failing
		h.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if failing {
			w.WriteHeader(http.StatusConflict)
			_ = json.NewEncoder(w).Encode(poison(fx["ErrorResponse"], "", text))
			return
		}
		for _, route := range hostileRoutes {
			if route.method != r.Method || !route.path.MatchString(r.URL.EscapedPath()) {
				continue
			}
			body := poison(fx[route.fixture], "", text)
			if route.list {
				body = []any{body}
			}
			_ = json.NewEncoder(w).Encode(body)
			return
		}
		w.WriteHeader(http.StatusNotFound)
		_ = json.NewEncoder(w).Encode(poison(fx["ErrorResponse"], "", text))
	}))
	tb.Cleanup(h.srv.Close)
	return h
}

func (h *hostileBackend) set(text string, failing bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.text, h.failing = text, failing
}

// serverTextScene opens one view against the backend and walks through the
// screens of it that show server text, returning each frame.
type serverTextScene struct {
	name string
	run  func(t *testing.T, client *api.Client) []string
}

// walk opens m and returns its frame after opening and after each key.
func walk[M tea.Model](t *testing.T, m M, keys ...string) []string {
	m = open(t, m)
	frames := []string{m.View().Content}
	for _, k := range keys {
		m = drive(t, m, press(k))
		frames = append(frames, m.View().Content)
	}
	return frames
}

var serverTextScenes = []serverTextScene{
	{"dashboard", func(t *testing.T, c *api.Client) []string { return walk(t, ui.NewDashboardModel(c)) }},
	{"profiles", func(t *testing.T, c *api.Client) []string { return walk(t, ui.NewProfilesModel(c), "d", "esc") }},
	{"profile detail", func(t *testing.T, c *api.Client) []string {
		m := open(t, ui.NewProfileDetailModel(c))
		m = drive(t, m, ui.OpenProfileMsg{Slug: "docs"})
		frames := []string{m.View().Content}
		for _, k := range []string{"right", "right", "enter", "esc", "right", "enter", "esc", "right", "p"} {
			m = drive(t, m, press(k))
			frames = append(frames, m.View().Content)
		}
		return frames
	}},
	{"jobs", func(t *testing.T, c *api.Client) []string { return walk(t, ui.NewJobsModel(c), "enter") }},
	{"conflicts", func(t *testing.T, c *api.Client) []string { return walk(t, ui.NewConflictsModel(c), "enter") }},
	{"remotes", func(t *testing.T, c *api.Client) []string { return walk(t, ui.NewRemotesModel(c), "d") }},
	{"notifications", func(t *testing.T, c *api.Client) []string {
		return walk(t, ui.NewNotificationsModel(c), "tab", "right")
	}},
	{"config", func(t *testing.T, c *api.Client) []string { return walk(t, ui.NewConfigModel(c)) }},
	{"wizard", func(t *testing.T, c *api.Client) []string { return walk(t, ui.NewWizardModel(c), "enter") }},
	{"logs", func(t *testing.T, c *api.Client) []string { return walk(t, ui.NewLogsModel(c)) }},
}

// checkFrame fails when server text reached the frame as more than text:
// every escape sequence in it must be an SGR sequence (the views' styles),
// and no cell the renderer draws may hold a control character.
func checkFrame(t *testing.T, where, frame string) {
	t.Helper()
	if safe := components.SafeFrame(frame); safe != frame {
		i := 0
		for i < len(safe) && i < len(frame) && safe[i] == frame[i] {
			i++
		}
		t.Errorf("%s: control character or sequence in the frame at %q", where, frame[max(0, i-20):min(len(frame), i+20)])
	}
	buf := uv.NewScreenBuffer(200, 80)
	uv.NewStyledString(frame).Draw(buf, buf.Bounds())
	for y := 0; y < buf.Height(); y++ {
		for x := 0; x < buf.Width(); x++ {
			if c := buf.CellAt(x, y); c != nil && strings.ContainsFunc(c.Content, isControlRune) {
				t.Errorf("%s: cell %d,%d holds %q", where, x, y, c.Content)
			}
		}
	}
}

func isControlRune(r rune) bool { return r < 0x20 || (r >= 0x7f && r <= 0x9f) }

// sgrSequences returns the distinct SGR sequences in frame.
func sgrSequences(frame string) map[string]bool {
	out := map[string]bool{}
	p := ansi.NewParser()
	var state byte
	for len(frame) > 0 {
		seq, _, n, newState := ansi.DecodeSequence(frame, state, p)
		if n <= 0 {
			break
		}
		if strings.HasPrefix(seq, "\x1b[") && p.Command() == 'm' {
			out[seq] = true
		}
		state, frame = newState, frame[n:]
	}
	return out
}

// checkServerText runs scene with text in every string of the server's
// answers, and again with each escape character in text replaced by
// U+FFFD (what SafeLine shows for it). Each frame must pass checkFrame,
// and the first run's frames must not hold an SGR sequence the second's
// do not: one would be server text restyling the view.
func checkServerText(t *testing.T, h *hostileBackend, scene serverTextScene, text string, failing bool) {
	t.Helper()
	client := api.NewClient(h.srv.URL, "", "fuzz")
	h.set(text, failing)
	frames := scene.run(t, client)
	h.set(strings.NewReplacer("\x1b", "�", "\u009b", "�").Replace(text), failing)
	inert := scene.run(t, client)
	for i, frame := range frames {
		where := scene.name
		if failing {
			where += " (errors)"
		}
		checkFrame(t, where, frame)
		if i >= len(inert) {
			continue
		}
		allowed := sgrSequences(inert[i])
		for seq := range sgrSequences(frame) {
			if !allowed[seq] {
				t.Errorf("%s: server text restyles the view with %q", where, seq)
			}
		}
	}
}

// hostileSeeds are the shapes of server text that used to draw over or
// restyle a view: a carriage return, a backspace, SGR and cursor sequences,
// OSC and C1 controls, and a multi-line message.
var hostileSeeds = []string{
	"evil\rFAKE",
	"abc\b\b\bXYZ",
	"\x1b[8mhidden\x1b[0m",
	"\x1b[2K\x1b[1Gspoof",
	"\x1b[31;1mred",
	"\x1b]52;c;cm0gLXJmIH4=\x07",
	"\u009b31m8bit",
	"line one\nline two\r\nline three",
	"tab\there\x7f\x00",
	"\xff\xfe invalid",
}

// Every view that shows server text: whatever a file name, a log message
// or an error detail holds, the view draws it as text. No control
// character or sequence from it reaches the cells, and it restyles nothing.
func FuzzViewsServerText(f *testing.F) {
	for i := range serverTextScenes {
		for _, s := range hostileSeeds {
			f.Add(s, uint8(i), false)
		}
		f.Add(hostileSeeds[0], uint8(i), true)
		f.Add(hostileSeeds[2], uint8(i), true)
	}
	h := newHostileBackend(f)
	f.Fuzz(func(t *testing.T, text string, scene uint8, failing bool) {
		checkServerText(t, h, serverTextScenes[int(scene)%len(serverTextScenes)], text, failing)
	})
}

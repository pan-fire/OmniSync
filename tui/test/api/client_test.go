package api_test

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

func TestHeaders_UserAgentAndBearerOnEveryRequest(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/health", 200, map[string]any{"status": "ok"})
	f.on("GET", "/profiles", 200, []any{})
	f.on("POST", "/profiles/docs/sync/start", 200, map[string]any{"job_id": 1, "state": "idle"})
	f.on("DELETE", "/profiles/docs", 204, nil)

	var ua string
	srvUA := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ua = r.Header.Get("User-Agent")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	}))
	defer srvUA.Close()
	if _, err := api.NewClient(srvUA.URL, "", "1.2.3").Health(context.Background()); err != nil {
		t.Fatal(err)
	}
	if ua != "osync-tui/1.2.3" {
		t.Errorf("User-Agent = %q", ua)
	}

	c := f.client("my-secret")
	ctx := context.Background()
	if _, err := c.Health(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := c.ListProfiles(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := c.StartProfileSync(ctx, "docs", api.SyncDirectionPush, false); err != nil {
		t.Fatal(err)
	}
	if err := c.DeleteProfile(ctx, "docs"); err != nil {
		t.Fatal(err)
	}
	for _, r := range f.all() {
		if r.Auth != "Bearer my-secret" {
			t.Errorf("%s %s: Authorization = %q, want Bearer my-secret", r.Method, r.Path, r.Auth)
		}
	}
}

func TestHeaders_NoKeyNoAuthorization(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/health", 200, map[string]any{"status": "ok"})
	if _, err := f.client("").Health(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := f.last().Auth; got != "" {
		t.Errorf("Authorization = %q, want none", got)
	}
}

func TestUnauthorized_ClearMessage(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/profiles", 401, map[string]any{"detail": "Not authenticated"})
	_, err := f.client("").ListProfiles(context.Background())
	if err == nil {
		t.Fatal("expected an error")
	}
	if !api.IsUnauthorized(err) {
		t.Errorf("IsUnauthorized = false for %v", err)
	}
	for _, want := range []string{"401", "--api-key", "OMNISYNC_API_KEY"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("error %q does not mention %q", err.Error(), want)
		}
	}
}

func TestErrorEnvelope(t *testing.T) {
	cases := []struct {
		name    string
		status  int
		body    any
		want    string
		code    string
		details map[string]any
	}{
		{"message only", 400, map[string]any{"detail": "Pass ?confirm=true", "code": "confirmation_required"},
			"Pass ?confirm=true", "confirmation_required", nil},
		{"with details", 422, map[string]any{
			"detail": "local_dir: must be absolute", "code": "validation_failed",
			"details": map[string]any{"errors": []any{map[string]any{"loc": []any{"body", "local_dir"}, "msg": "must be absolute", "type": "value_error"}}},
		}, "local_dir: must be absolute", "validation_failed", map[string]any{
			"errors": []any{map[string]any{"loc": []any{"body", "local_dir"}, "msg": "must be absolute", "type": "value_error"}},
		}},
		{"not an envelope", 503, map[string]any{"detail": map[string]any{"message": "old shape"}},
			`{"detail":{"message":"old shape"}}`, "", nil},
		{"a list", 502, []any{"x"}, `["x"]`, "", nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f := newFakeBackend(t)
			f.on("GET", "/config", tc.status, tc.body)
			_, err := f.client("").GetConfig(context.Background())
			var apiErr *api.ApiError
			if !errors.As(err, &apiErr) {
				t.Fatalf("expected *ApiError, got %T %v", err, err)
			}
			if apiErr.StatusCode != tc.status || apiErr.Detail != tc.want || apiErr.Code != tc.code {
				t.Errorf("got %d %q %q, want %d %q %q", apiErr.StatusCode, apiErr.Detail, apiErr.Code, tc.status, tc.want, tc.code)
			}
			if !reflect.DeepEqual(apiErr.Details, tc.details) {
				t.Errorf("details = %#v, want %#v", apiErr.Details, tc.details)
			}
			if tc.code != "" && !api.HasCode(err, tc.code) {
				t.Errorf("HasCode(%q) = false", tc.code)
			}
		})
	}
}

func TestMalformedJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("not json"))
	}))
	defer srv.Close()
	_, err := api.NewClient(srv.URL, "", "t").Health(context.Background())
	if err == nil || !strings.Contains(err.Error(), "unexpected response") {
		t.Errorf("expected 'unexpected response' error, got %v", err)
	}
}

func TestConnectionRefused(t *testing.T) {
	if _, err := api.NewClient("http://127.0.0.1:1", "", "t").Health(context.Background()); err == nil {
		t.Fatal("expected an error")
	}
}

func slowServer(t *testing.T, delay time.Duration, body string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Reading the body lets the server notice a client that hangs up.
		_, _ = io.Copy(io.Discard, r.Body)
		select {
		case <-time.After(delay):
		case <-r.Context().Done():
			return
		}
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return srv
}

// Reads get a short deadline.
func TestReadTimeout_AppliesToReads(t *testing.T) {
	srv := slowServer(t, 300*time.Millisecond, `[]`)
	c := api.NewClient(srv.URL, "", "t")
	c.SetReadTimeout(50 * time.Millisecond)
	_, err := c.ListProfiles(context.Background())
	if err == nil {
		t.Fatal("expected the read to time out")
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("expected DeadlineExceeded, got %v", err)
	}
}

// A sync start is answered only when the sync is done; the read timeout
// must not cut it off.
func TestLongCalls_NotBoundByReadTimeout(t *testing.T) {
	srv := slowServer(t, 300*time.Millisecond, `{"job_id":7,"state":"idle","has_changes":false,"files":[],"summary":{},"id":1,"target_id":1,"started_at":"x","status":"completed","direction":"backup"}`)
	c := api.NewClient(srv.URL, "", "t")
	c.SetReadTimeout(50 * time.Millisecond)
	ctx := context.Background()

	resp, err := c.StartProfileSync(ctx, "docs", api.SyncDirectionPush, false)
	if err != nil {
		t.Fatalf("sync start timed out: %v", err)
	}
	if resp.JobID != 7 {
		t.Errorf("JobID = %d", resp.JobID)
	}
	if _, err := c.PreviewProfileSync(ctx, "docs"); err != nil {
		t.Errorf("preview timed out: %v", err)
	}
	if _, err := c.ResolveConflict(ctx, 1, api.ConflictKeepLocal); err != nil {
		t.Errorf("conflict resolve timed out: %v", err)
	}
	if _, err := c.ProfileDiff(ctx, "docs", 0, 0); err != nil {
		t.Errorf("diff timed out: %v", err)
	}
	if _, err := c.NetworkHealth(ctx); err != nil {
		t.Errorf("network health timed out: %v", err)
	}
}

// Backup runs, restores and selective syncs are answered at once (202, the
// job runs in the background), so they get the read deadline.
func TestBackgroundStarts_AreBoundByReadTimeout(t *testing.T) {
	srv := slowServer(t, 300*time.Millisecond, `{}`)
	c := api.NewClient(srv.URL, "", "t")
	c.SetReadTimeout(50 * time.Millisecond)
	ctx := context.Background()
	calls := map[string]func() error{
		"run": func() error { _, err := c.RunBackup(ctx, "docs", 1); return err },
		"restore": func() error {
			_, err := c.RestoreSnapshot(ctx, "docs", 1, api.RestoreRequest{SnapshotID: "s", RestoreScope: api.RestoreScopeBoth})
			return err
		},
		"restore-files": func() error {
			_, err := c.RestoreFiles(ctx, "docs", 1, api.RestoreFilesRequest{SnapshotID: "s", Paths: []string{"a"}})
			return err
		},
		"selective": func() error {
			_, err := c.ProfileSelectiveSync(ctx, "docs", []api.SelectiveSyncItem{{Path: "a", Action: api.FileActionPush}})
			return err
		},
	}
	for name, call := range calls {
		if err := call(); !errors.Is(err, context.DeadlineExceeded) {
			t.Errorf("%s: expected DeadlineExceeded, got %v", name, err)
		}
	}
}

// Long calls stop when their context is cancelled.
func TestLongCalls_Cancellable(t *testing.T) {
	srv := slowServer(t, 5*time.Second, `{}`)
	c := api.NewClient(srv.URL, "", "t")
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	start := time.Now()
	if _, err := c.StartProfileSync(ctx, "docs", api.SyncDirectionPush, false); err == nil {
		t.Fatal("expected cancellation")
	}
	if time.Since(start) > 2*time.Second {
		t.Error("cancellation did not stop the request")
	}
}

func TestSetBaseURL(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/health", 200, map[string]any{"status": "ok"})
	c := api.NewClient("http://localhost:1/", "", "t")
	c.SetBaseURL(f.srv.URL + "/")
	if _, err := c.Health(context.Background()); err != nil {
		t.Fatal(err)
	}
	if f.last().Path != "/health" {
		t.Errorf("path = %s", f.last().Path)
	}
}

func TestEscaping_PathSegmentsAndQuery(t *testing.T) {
	f := newFakeBackend(t)
	f.on("GET", "/remotes/my remote/about", 200, map[string]any{"supported": true})
	f.on("GET", "/jobs", 200, []any{})
	f.on("DELETE", "/remotes/a/b", 200, map[string]any{"detail": "x"})
	c := f.client("")
	ctx := context.Background()

	if _, err := c.RemoteStorageInfo(ctx, "my remote"); err != nil {
		t.Fatal(err)
	}
	if got := f.last().RawPath; got != "/remotes/my%20remote/about" {
		t.Errorf("raw path = %q", got)
	}

	if _, err := c.ListJobs(ctx, 0, 20, "docs&limit=1"); err != nil {
		t.Fatal(err)
	}
	if got := f.last().RawQuery; got != "limit=20&profile=docs%26limit%3D1&skip=0" {
		t.Errorf("query = %q", got)
	}

	// A slash in a name must not create another path segment.
	_ = c.DeleteRemote(ctx, "a/b", false)
	if got := f.last().RawPath; got != "/remotes/a%2Fb" {
		t.Errorf("raw path = %q", got)
	}
}

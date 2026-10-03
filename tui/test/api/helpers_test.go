package api_test

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// recorded is one request the fake backend received.
type recorded struct {
	Method   string
	Path     string // decoded path
	RawPath  string // path as sent on the wire (escaped)
	RawQuery string
	Auth     string
	Body     map[string]any
}

// fakeBackend answers every request with the reply registered for
// "METHOD /path" (decoded path), or 404.
type fakeBackend struct {
	t        *testing.T
	mu       sync.Mutex
	requests []recorded
	replies  map[string]reply
	srv      *httptest.Server
}

type reply struct {
	status int
	body   any
}

func newFakeBackend(t *testing.T) *fakeBackend {
	t.Helper()
	f := &fakeBackend{t: t, replies: map[string]reply{}}
	f.srv = httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeBackend) on(method, path string, status int, body any) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.replies[method+" "+path] = reply{status: status, body: body}
}

func (f *fakeBackend) serve(w http.ResponseWriter, r *http.Request) {
	data, _ := io.ReadAll(r.Body)
	var body map[string]any
	if len(data) > 0 {
		_ = json.Unmarshal(data, &body)
	}
	f.mu.Lock()
	f.requests = append(f.requests, recorded{
		Method: r.Method, Path: r.URL.Path, RawPath: r.URL.EscapedPath(), RawQuery: r.URL.RawQuery,
		Auth: r.Header.Get("Authorization"), Body: body,
	})
	rep, ok := f.replies[r.Method+" "+r.URL.Path]
	f.mu.Unlock()
	if !ok {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(rep.status)
	if rep.body != nil {
		_ = json.NewEncoder(w).Encode(rep.body)
	}
}

func (f *fakeBackend) client(key string) *api.Client {
	return api.NewClient(f.srv.URL, key, "test")
}

func (f *fakeBackend) last() recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.requests) == 0 {
		f.t.Fatal("no request recorded")
	}
	return f.requests[len(f.requests)-1]
}

func (f *fakeBackend) all() []recorded {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]recorded(nil), f.requests...)
}

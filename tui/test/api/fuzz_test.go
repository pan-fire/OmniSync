package api_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// fuzzServer answers every request with the status and body last set.
type fuzzServer struct {
	mu     sync.Mutex
	status int
	body   []byte
}

func (s *fuzzServer) set(status int, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.status, s.body = status, body
}

func newFuzzServer(f *testing.F) (*fuzzServer, *api.Client) {
	s := &fuzzServer{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		status, body := s.status, s.body
		s.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("X-Request-ID", "req-from-header")
		w.WriteHeader(status)
		_, _ = w.Write(body)
	}))
	f.Cleanup(srv.Close)
	return s, api.NewClient(srv.URL, "", "fuzz")
}

// Any error answer, whatever its body (an envelope, an older backend's
// object detail, a proxy's HTML page, garbage), becomes an *api.ApiError
// with the status, and the envelope's detail and code when there is one.
// The UI and the CLI's exit codes rely on that.
func FuzzErrorEnvelope(f *testing.F) {
	// Seeds: testdata/fuzz/FuzzErrorEnvelope.
	s, client := newFuzzServer(f)
	f.Fuzz(func(t *testing.T, status int, body []byte) {
		status = 400 + int(uint(status)%200)
		s.set(status, body)
		_, err := client.GetProfile(context.Background(), "docs")
		var apiErr *api.ApiError
		if !errors.As(err, &apiErr) {
			t.Fatalf("status %d: error %T %v is not an *api.ApiError", status, err, err)
		}
		if apiErr.StatusCode != status || !api.IsStatus(err, status) {
			t.Errorf("StatusCode = %d, want %d", apiErr.StatusCode, status)
		}
		if apiErr.Error() == "" {
			t.Error("empty error message")
		}
		var envelope struct {
			Detail    any    `json:"detail"`
			Code      any    `json:"code"`
			RequestID string `json:"request_id"`
		}
		if json.Unmarshal(body, &envelope) != nil {
			return
		}
		if d, ok := envelope.Detail.(string); ok && d != "" && apiErr.Detail != d {
			t.Errorf("Detail = %q, want the envelope's %q", apiErr.Detail, d)
		}
		if c, ok := envelope.Code.(string); ok && apiErr.Code != c {
			t.Errorf("Code = %q, want %q", apiErr.Code, c)
		}
		if envelope.RequestID == "" && apiErr.RequestID != "req-from-header" {
			t.Errorf("RequestID = %q, want the header's", apiErr.RequestID)
		}
	})
}

// A success answer that does not decode is an error naming the request,
// never a panic or a half-filled value passed on as if it were valid.
func FuzzResponseDecoding(f *testing.F) {
	// Seeds: testdata/fuzz/FuzzResponseDecoding.
	s, client := newFuzzServer(f)
	f.Fuzz(func(t *testing.T, body []byte) {
		s.set(200, body)
		ctx := context.Background()
		check := func(what string, err error) {
			t.Helper()
			if err != nil && !strings.Contains(err.Error(), "unexpected response from ") {
				t.Errorf("%s: %v", what, err)
			}
		}
		_, err := client.ListProfiles(ctx)
		check("ListProfiles", err)
		_, err = client.AggregateStatus(ctx)
		check("AggregateStatus", err)
		_, err = client.GetLogsFiltered(ctx, 0, 10, "", "")
		check("GetLogsFiltered", err)
		_, err = client.ProfileDiff(ctx, "docs", 0, 0)
		check("ProfileDiff", err)
	})
}

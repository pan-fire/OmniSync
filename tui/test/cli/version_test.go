package cli_test

import (
	"bytes"
	"context"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

func TestVersionFlag_PrintsVersionAndCommit(t *testing.T) {
	isolate(t)
	cmd := cli.NewRootCommand("osync", "0.9.0", "abc1234")
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetArgs([]string{"--version"})
	if err := cmd.ExecuteContext(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got, want := strings.TrimSpace(out.String()), "osync version 0.9.0 (commit abc1234)"; got != want {
		t.Errorf("--version = %q, want %q", got, want)
	}
}

func TestHealth_ShowsBackendVersion(t *testing.T) {
	isolate(t)
	srv := httptest.NewServer(jsonHandler(200, map[string]any{
		"status": "ok", "rclone_installed": true, "remote_accessible": nil, "uptime_seconds": 1, "database_ok": true,
		"version": "0.9.0",
	}))
	defer srv.Close()
	out, _, err := runCLI(t, "health", "--url", srv.URL)
	if err != nil || !strings.Contains(out, "Version") || !strings.Contains(out, "0.9.0") {
		t.Errorf("err = %v, output:\n%s", err, out)
	}
}

func TestHealth_OlderBackendWithoutVersion(t *testing.T) {
	isolate(t)
	srv := httptest.NewServer(jsonHandler(200, map[string]any{
		"status": "ok", "rclone_installed": true, "remote_accessible": nil, "uptime_seconds": 1, "database_ok": true,
	}))
	defer srv.Close()
	out, _, err := runCLI(t, "health", "--url", srv.URL)
	if err != nil || strings.Contains(out, "Version") {
		t.Errorf("err = %v, output:\n%s", err, out)
	}
}

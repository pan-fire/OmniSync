package ui_test

import (
	"os"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/config"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// TestScreenshot_Dashboard writes the dashboard of `osync`, with its ANSI
// colours, to the file OMNISYNC_TUI_SCREENSHOT names, for the README
// screenshot (frontend: `pnpm screenshots` renders it to a PNG). Without
// the variable it is skipped. The data is the made-up install of the web
// UI screenshots (frontend/e2e/mock-backend.ts).
func TestScreenshot_Dashboard(t *testing.T) {
	out := os.Getenv("OMNISYNC_TUI_SCREENSHOT")
	if out == "" {
		t.Skip("set OMNISYNC_TUI_SCREENSHOT to write the dashboard capture")
	}
	theme.Set("dark")
	t.Cleanup(func() { theme.Set("dark") })

	now := time.Now().UTC()
	ago := func(minutes int) string {
		return now.Add(-time.Duration(minutes) * time.Minute).Format(time.RFC3339)
	}
	profile := func(id int, slug, name, local, remote, mode, state string, lastSync int) map[string]any {
		p := profileJSON(slug, name, state)
		p["id"], p["local_dir"], p["remote_dir"], p["sync_mode"] = id, local, remote, mode
		p["last_sync"], p["pending_changes"] = ago(lastSync), 0
		p["mirror_notice_dismissed"] = true
		return p
	}
	progress := map[string]any{
		"bytes": 432013312, "total_bytes": 1181116006, "speed": 19503513, "eta_seconds": 38,
		"files_done": 37, "files_total": 96, "checks": 1284, "total_checks": 1284,
		"current_files": []any{map[string]any{"name": "Taxes/2025/receipts.pdf", "size": 25165824, "bytes": 17825792, "percentage": 71}},
	}
	documents := profile(1, "documents", "Documents", "/home/alice/Documents", "gdrive:Documents", "two_way", "syncing", 14)
	documents["progress"] = progress
	profiles := []any{
		documents,
		profile(2, "photos", "Photos", "/home/alice/Pictures", "s3:alice-photos/Pictures", "mirror", "idle", 52),
		profile(3, "projects", "Projects", "/home/alice/Projects", "nas:backup/Projects", "two_way", "idle", 6),
		profile(4, "music", "Music", "/home/alice/Music", "gdrive:Music", "mirror", "idle", 200),
	}
	summary := make([]any, 0, len(profiles))
	for _, raw := range profiles {
		p := raw.(map[string]any)
		summary = append(summary, map[string]any{
			"slug": p["slug"], "name": p["name"], "state": p["state"], "last_sync": p["last_sync"],
			"pending_changes": 0, "intervals_paused": false, "progress": p["progress"],
		})
	}

	b := newBackend(t)
	b.json("GET", "/sync/status/aggregate", 200, map[string]any{
		"overall_state": "syncing", "total_pending_changes": 0, "paused_profiles": []any{}, "profiles_summary": summary,
	})
	b.json("GET", "/health", 200, map[string]any{
		"status": "ok", "rclone_installed": true, "remote_accessible": nil, "database_ok": true, "uptime_seconds": 532800,
	})
	b.json("GET", "/health/remotes", 200, map[string]any{"remotes": []any{
		map[string]any{"remote": "gdrive", "accessible": true, "profiles": []any{"documents", "music"}},
		map[string]any{"remote": "s3", "accessible": true, "profiles": []any{"photos"}},
		map[string]any{"remote": "nas", "accessible": true, "profiles": []any{"projects"}},
	}})
	b.json("GET", "/health/network", 200, map[string]any{
		"dns_google": map[string]any{"ok": true}, "httpx_cloudflare": map[string]any{"ok": true}, "rclone_network": map[string]any{"ok": true},
	})
	b.json("GET", "/profiles", 200, profiles)

	// The top bar names the backend: a fixed address, not the test server's.
	app := ui.NewApp(b.client(), &config.Config{URL: "http://localhost:8000"})
	app.RegisterView(ui.NewDashboardModel(b.client()))
	pump := func(msgs ...tea.Msg) {
		queue := msgs
		for i := 0; i < 200 && len(queue) > 0; i++ {
			next := queue[0]
			queue = queue[1:]
			updated, cmd := app.Update(next)
			app = updated.(ui.App)
			queue = append(queue, runAll(cmd)...)
		}
	}
	pump(append([]tea.Msg{tea.WindowSizeMsg{Width: 110, Height: 22}}, runAll(app.Init())...)...)
	pump(ui.ConnectionStatusMsg{Connected: true, Healthy: true, LatencyMs: 4, OverallState: "syncing"})
	pump(ui.TickMsg{Time: now.Local()})

	if err := os.WriteFile(out, []byte(app.View().Content), 0o600); err != nil {
		t.Fatal(err)
	}
}

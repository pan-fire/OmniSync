package cli_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

// Whatever text the backend sends (a log message carrying a file name from
// a synced folder, an error detail), `osync logs` prints no character a
// terminal would act on, and `osync logs --json` gives a script exactly the
// text the backend sent.
func FuzzTerminalSafeOutput(f *testing.F) {
	f.Setenv("XDG_CONFIG_HOME", f.TempDir())
	f.Setenv("HOME", f.TempDir())
	f.Setenv("AppData", f.TempDir())
	var mu sync.Mutex
	var reply []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		body := reply
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	}))
	f.Cleanup(srv.Close)
	run := func(t *testing.T, args ...string) (string, string, error) {
		cmd := cli.NewRootCommand("osync", "fuzz", "none")
		var out, errOut bytes.Buffer
		cmd.SetOut(&out)
		cmd.SetErr(&errOut)
		cmd.SetArgs(append(args, "--url", srv.URL))
		err := cmd.ExecuteContext(context.Background())
		return out.String(), errOut.String(), err
	}
	f.Fuzz(func(t *testing.T, message, exc string) {
		body, err := json.Marshal([]map[string]string{{"timestamp": "2026-10-05T10:00:00", "level": "INFO", "message": message, "exc": exc}})
		if err != nil {
			t.Skip()
		}
		mu.Lock()
		reply = body
		mu.Unlock()
		// What the backend's text is after JSON: invalid UTF-8 became U+FFFD.
		var sent []struct{ Message, Exc string }
		if err := json.Unmarshal(body, &sent); err != nil {
			t.Fatal(err)
		}

		out, errOut, err := run(t, "logs")
		if err != nil {
			t.Fatalf("logs: %v (%s)", err, errOut)
		}
		if around, found := controlBytes(out); found {
			t.Errorf("logs printed a control character near %q", around)
		}

		out, errOut, err = run(t, "logs", "--json")
		if err != nil {
			t.Fatalf("logs --json: %v (%s)", err, errOut)
		}
		if around, found := controlBytes(out); found {
			t.Errorf("--json printed a control character near %q", around)
		}
		var got []struct{ Message, Exc string }
		if err := json.Unmarshal([]byte(out), &got); err != nil || len(got) != 1 {
			t.Fatalf("--json is not one entry: %v\n%q", err, out)
		}
		if got[0] != sent[0] {
			t.Errorf("--json changed the text: got %q, sent %q", got[0], sent[0])
		}
	})
}

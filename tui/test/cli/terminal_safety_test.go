package cli_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

// hostile is text a remote can put in a file name, and from there into the
// backend's log, a conflict or an error message: it sets the clipboard
// (OSC 52), the window title, clears the screen, uses an 8-bit CSI (U+009B),
// moves the cursor back over what was printed and rings the bell.
const hostile = "a\x1b]52;c;cm0gLXJmIH4=\x07b\x1b]0;owned\x1b\\c\x1b[2Jd\u009b31me\rf\bg\x7fh"

// controlBytes reports the first byte of out a terminal would act on: C0
// controls other than newline and tab, DEL, and the UTF-8 encoded C1 range.
func controlBytes(out string) (string, bool) {
	for i, r := range out {
		if (r < 0x20 && r != '\n' && r != '\t') || (r >= 0x7f && r <= 0x9f) {
			return out[max(0, i-10):min(len(out), i+10)], true
		}
	}
	return "", false
}

// Server text in human output reaches the terminal with every control
// character shown as a \u escape, never acted on; the rest of the text
// stays readable.
func TestTerminalSafety_HumanOutputNeutralisesControlSequences(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /logs", 200, []any{map[string]any{
		"timestamp": "2026-10-05T10:00:00", "level": "INFO", "message": "upload " + hostile, "exc": "Traceback\n  " + hostile,
	}})
	f.on("GET /profiles/docs", 404, map[string]any{"detail": "Profile " + hostile + " not found", "code": "profile_not_found"})

	out, errOut, code := f.run("logs")
	if code != cli.ExitOK {
		t.Fatalf("exit %d, stderr %q", code, errOut)
	}
	if around, found := controlBytes(out); found {
		t.Errorf("logs printed a control character near %q", around)
	}
	if !strings.Contains(out, `upload a\u001b]52;c;cm0gLXJmIH4=\u0007b`) || !strings.Contains(out, `\u009b31me\u000df\u0008g\u007fh`) {
		t.Errorf("hostile text not shown escaped:\n%s", out)
	}

	_, errOut, code = f.run("profile", "show", "docs")
	if code != cli.ExitNotFound {
		t.Errorf("exit %d, want %d", code, cli.ExitNotFound)
	}
	if around, found := controlBytes(errOut); found {
		t.Errorf("the error message printed a control character near %q", around)
	}
	if !strings.Contains(errOut, `Profile a\u001b]52;`) {
		t.Errorf("stderr = %q", errOut)
	}
}

// --json output stays exact: a script decoding it gets the server's text
// back unchanged, while the raw bytes still hold no control characters
// (encoding/json leaves DEL and C1 unescaped; the output escapes them).
func TestTerminalSafety_JSONOutputIsLosslessAndInert(t *testing.T) {
	f := newFakeAPI(t)
	f.on("GET /logs", 200, []any{map[string]any{"timestamp": "t", "level": "INFO", "message": hostile}})
	out, _, code := f.run("logs", "--json")
	if code != cli.ExitOK {
		t.Fatalf("exit %d", code)
	}
	if around, found := controlBytes(out); found {
		t.Errorf("--json printed a control character near %q", around)
	}
	var entries []struct{ Message string }
	if err := json.Unmarshal([]byte(out), &entries); err != nil || len(entries) != 1 {
		t.Fatalf("not a JSON array of one entry: %v\n%s", err, out)
	}
	if entries[0].Message != hostile {
		t.Errorf("message = %q, want %q", entries[0].Message, hostile)
	}
}

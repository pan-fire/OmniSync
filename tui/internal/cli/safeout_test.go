package cli

import (
	"bytes"
	"errors"
	"testing"
)

// The writer is tested here, inside the package, for what the commands
// cannot easily produce: a character split across two writes, bytes that
// are not UTF-8, and a failing destination.
func TestTerminalSafeWriter(t *testing.T) {
	var out bytes.Buffer
	w := newTerminalSafeWriter(&out)
	// "ä" is 0xC3 0xA4; U+009B (8-bit CSI) is 0xC2 0x9B. Split both.
	for _, chunk := range [][]byte{{'a', 0xC3}, {0xA4, 0xC2}, {0x9B, '['}, {'2', 'J', '\n', '\t', 0xFF, 0x1b}} {
		n, err := w.Write(chunk)
		if err != nil || n != len(chunk) {
			t.Fatalf("Write(%q) = %d, %v", chunk, n, err)
		}
	}
	if got, want := out.String(), "aä\\u009b[2J\n\t\\xff\\u001b"; got != want {
		t.Errorf("wrote %q, want %q", got, want)
	}

	failing := newTerminalSafeWriter(errWriter{})
	if n, err := failing.Write([]byte("x")); err == nil || n != 0 {
		t.Errorf("Write to a failing writer = %d, %v", n, err)
	}
}

type errWriter struct{}

func (errWriter) Write([]byte) (int, error) { return 0, errors.New("closed") }

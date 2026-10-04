package cli

import (
	"fmt"
	"io"
	"unicode/utf8"
)

// terminalSafeWriter shows every control character written through it as a
// \u escape (an invalid UTF-8 byte as \x..) instead of passing it on.
//
// The commands print server text: log messages, file and remote names,
// error details. A file name comes from whoever can write to a synced
// folder, and a control sequence in it would otherwise reach the user's
// terminal and act there: set the clipboard (OSC 52), the window title,
// clear the screen or move the cursor over what was printed. Newline and
// tab pass, for the output's own layout.
//
// For --json output the escapes are exact: a control character can only
// stand inside a JSON string there, and encoding/json already escapes C0
// but writes DEL and C1 (U+0080-U+009F) raw. \u007f is the same string to
// a JSON reader, so scripts get the server's text back unchanged.
type terminalSafeWriter struct {
	w io.Writer
	// pending holds an incomplete UTF-8 sequence from the end of the last
	// Write, completed by the next one.
	pending []byte
}

func newTerminalSafeWriter(w io.Writer) *terminalSafeWriter {
	if s, ok := w.(*terminalSafeWriter); ok {
		return s
	}
	return &terminalSafeWriter{w: w}
}

// isTerminalControl reports whether a terminal would act on r instead of
// printing it.
func isTerminalControl(r rune) bool {
	return (r < 0x20 && r != '\n' && r != '\t') || (r >= 0x7f && r <= 0x9f)
}

func (s *terminalSafeWriter) Write(p []byte) (int, error) {
	data := append(s.pending, p...)
	s.pending = nil
	out := make([]byte, 0, len(data))
	for len(data) > 0 {
		r, size := utf8.DecodeRune(data)
		if r == utf8.RuneError && size == 1 && !utf8.FullRune(data) {
			s.pending = append([]byte(nil), data...)
			break
		}
		switch {
		case r == utf8.RuneError && size == 1:
			out = fmt.Appendf(out, `\x%02x`, data[0])
		case isTerminalControl(r):
			out = fmt.Appendf(out, `\u%04x`, r)
		default:
			out = append(out, data[:size]...)
		}
		data = data[size:]
	}
	if _, err := s.w.Write(out); err != nil {
		return 0, err
	}
	return len(p), nil
}

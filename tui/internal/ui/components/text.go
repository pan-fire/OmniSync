package components

import (
	"strings"
	"unicode/utf8"

	"github.com/charmbracelet/x/ansi"
)

// Server text in the views: log messages, file, remote and profile names,
// error details and paths. A file name comes from whoever can write to a
// synced folder, so it can hold anything. The renderer parses a frame into
// cells: a carriage return there moves back to the start of the line, an
// SGR sequence restyles the cells after it, and other control characters
// end up inside a cell and reach the terminal as they are (a backspace or
// a cursor movement draws over what the view put there).
//
// Every string from the server or a file goes through SafeLine or
// SafeText before a view draws it; the table, the flash message and the
// confirmation dialog do that themselves. A control character shows as
// U+FFFD, so the text cannot pass for the view's own text or style, and
// the user still sees that something is there.

// replacement shows a control character.
const replacement = utf8.RuneError

// isControl reports whether a terminal or the renderer acts on r instead of
// drawing it: C0, DEL and C1.
func isControl(r rune) bool {
	return r < 0x20 || (r >= 0x7f && r <= 0x9f)
}

// SafeText returns s as plain text that keeps its lines: a newline (also
// CR LF) stays, a tab becomes a space, and every other control character,
// a lone carriage return or an invalid UTF-8 byte shows as U+FFFD. Use it
// for text that is meant to span lines, such as a traceback or a dialog.
func SafeText(s string) string {
	if isPlain(s, true) {
		return s
	}
	s = strings.ReplaceAll(s, "\r\n", "\n")
	return strings.Map(func(r rune) rune {
		switch {
		case r == '\n':
			return r
		case r == '\t':
			return ' '
		case isControl(r):
			return replacement
		}
		return r
	}, s)
}

// SafeLine returns s as one line of plain text: like SafeText, but a
// newline shows as U+FFFD too, so the text cannot start a line of its own
// in a list, a field or the status bar.
func SafeLine(s string) string {
	s = SafeText(s)
	if !strings.Contains(s, "\n") {
		return s
	}
	return strings.ReplaceAll(s, "\n", string(replacement))
}

// isPlain reports whether s is valid UTF-8 without control characters
// (newlines allowed when keepNewlines), the common case.
func isPlain(s string, keepNewlines bool) bool {
	for _, r := range s {
		if r == utf8.RuneError || (isControl(r) && (r != '\n' || !keepNewlines)) {
			return false
		}
	}
	return true
}

// SafeFrame is the last line of defence for a whole frame, after the views
// composed it. It keeps the text, the newlines and the SGR sequences the
// views' styles produce, and shows every other control character or escape
// sequence as U+FFFD. A view that forgot SafeLine can then still not move
// the cursor, go back to the start of a line or send a sequence to the
// terminal. (A frame cannot tell an SGR sequence from server text from a
// view's own; SafeLine and SafeText in the views keep those out.)
func SafeFrame(frame string) string {
	if isPlain(frame, true) {
		return frame
	}
	var b strings.Builder
	b.Grow(len(frame))
	p := ansi.NewParser()
	var state byte
	for len(frame) > 0 {
		seq, _, n, newState := ansi.DecodeSequence(frame, state, p)
		switch {
		case n <= 0:
			// Never loop forever on a parser surprise.
			b.WriteRune(replacement)
			n = 1
		case seq == "\n":
			b.WriteString(seq)
		case seq[0] == ansi.ESC && ansi.HasCsiPrefix(seq) && p.Command() == 'm':
			b.WriteString(seq) // SGR from the views' styles
		case strings.ContainsFunc(seq, isControl) || !utf8.ValidString(seq):
			b.WriteRune(replacement)
		default:
			b.WriteString(seq)
		}
		state = newState
		frame = frame[n:]
	}
	return b.String()
}

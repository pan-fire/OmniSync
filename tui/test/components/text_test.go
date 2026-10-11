package components_test

import (
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func hasControl(s string, allowNewline bool) bool {
	return strings.ContainsFunc(s, func(r rune) bool {
		if r == '\n' && allowNewline {
			return false
		}
		return r < 0x20 || (r >= 0x7f && r <= 0x9f)
	})
}

// Server text keeps what it says and loses what a terminal would act on: a
// carriage return or backspace (drawing over the line), an escape sequence
// (restyling it) or a C1 control. A multi-line text keeps its lines.
func TestSafeText(t *testing.T) {
	cases := []struct{ in, line, text string }{
		{"plain name.txt", "plain name.txt", "plain name.txt"},
		{"evil\rFAKE", "evil�FAKE", "evil�FAKE"},
		{"abc\b\bX", "abc��X", "abc��X"},
		{"\x1b[8mhidden", "�[8mhidden", "�[8mhidden"},
		{"one\ntwo\r\nthree", "one�two�three", "one\ntwo\nthree"},
		{"a\tb", "a b", "a b"},
		{"\u009b31m\x7f", "�31m�", "�31m�"},
		{"bad \xff byte", "bad � byte", "bad � byte"},
		{"日本語 ä", "日本語 ä", "日本語 ä"},
	}
	for _, c := range cases {
		if got := components.SafeLine(c.in); got != c.line {
			t.Errorf("SafeLine(%q) = %q, want %q", c.in, got, c.line)
		}
		if got := components.SafeText(c.in); got != c.text {
			t.Errorf("SafeText(%q) = %q, want %q", c.in, got, c.text)
		}
	}
}

// The frame filter keeps the views' own styles and the text, and turns
// everything else a terminal acts on into U+FFFD.
func TestSafeFrame(t *testing.T) {
	cases := []struct{ in, want string }{
		{"\x1b[1;38;2;1;2;3mtitle\x1b[m\nline", "\x1b[1;38;2;1;2;3mtitle\x1b[m\nline"},
		{"a\rb", "a�b"},
		{"a\x1b[2Kb", "a�b"},
		{"a\x1b]52;c;eA==\x07b", "a�b"},
		{"a\x1b[?25lb", "a�b"},
		{"a\bb\x00", "a�b�"},
		{"\u009b31mx", "�31mx"},
		{"日本語\n", "日本語\n"},
	}
	for _, c := range cases {
		if got := components.SafeFrame(c.in); got != c.want {
			t.Errorf("SafeFrame(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// Whatever the server sends: SafeLine is one line, SafeText keeps only
// newlines, neither holds a control character or invalid UTF-8, applying
// them again changes nothing, and SafeLine of SafeText is SafeLine (a view
// may hold either). A frame passed through SafeFrame passes unchanged.
func FuzzSafeText(f *testing.F) {
	for _, s := range []string{"evil\rFAKE", "\x1b[8mx", "a\nb\r\nc\td", "\u009b1m", "\xff", "\x1b]8;;http://x\x1b\\link"} {
		f.Add(s)
	}
	f.Fuzz(func(t *testing.T, s string) {
		line, text := components.SafeLine(s), components.SafeText(s)
		if hasControl(line, false) || !utf8.ValidString(line) {
			t.Errorf("SafeLine(%q) = %q", s, line)
		}
		if hasControl(text, true) || !utf8.ValidString(text) {
			t.Errorf("SafeText(%q) = %q", s, text)
		}
		if components.SafeLine(line) != line || components.SafeText(text) != text {
			t.Errorf("not idempotent for %q", s)
		}
		if components.SafeLine(text) != line {
			t.Errorf("SafeLine(SafeText(%q)) = %q, want %q", s, components.SafeLine(text), line)
		}
		frame := components.SafeFrame(s)
		if components.SafeFrame(frame) != frame {
			t.Errorf("SafeFrame(%q) = %q is not stable", s, frame)
		}
		if strings.Contains(frame, "\r") || strings.Contains(frame, "\b") || strings.Contains(frame, "\x07") {
			t.Errorf("SafeFrame(%q) = %q", s, frame)
		}
	})
}

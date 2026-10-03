package logging

import (
	"errors"
	"fmt"
	"io"
	"runtime/debug"
	"sync"

	tea "charm.land/bubbletea/v2"
)

// BugReportURL is where users report crashes.
const BugReportURL = "https://github.com/pan-fire/OmniSync/issues"

// PanicGuard wraps the root model. A panic in Init, Update, View or in a
// command the model returned is written to the log with its stack trace,
// remembered, and then re-raised, so Bubble Tea still restores the terminal
// and Program.Run returns. The caller then asks Panicked and prints the
// fatal-error message on the restored terminal.
//
// Bubble Tea's own recovery stays on (it is the only thing that can restore
// the terminal after a panic in a command goroutine); WithoutCatchPanics
// would leave the terminal in raw mode on such a panic.
type PanicGuard struct {
	inner tea.Model
	state *panicState
}

type panicState struct {
	mu    sync.Mutex
	value any
	stack []byte
	hit   bool
}

// NewPanicGuard wraps m.
func NewPanicGuard(m tea.Model) PanicGuard {
	return PanicGuard{inner: m, state: &panicState{}}
}

// Panicked returns the first recorded panic value and its stack trace.
func (g PanicGuard) Panicked() (value any, stack []byte, ok bool) {
	g.state.mu.Lock()
	defer g.state.mu.Unlock()
	return g.state.value, g.state.stack, g.state.hit
}

// record logs a recovered panic and keeps the first one.
func (s *panicState) record(r any) {
	stack := debug.Stack()
	L().Error("panic", "value", fmt.Sprint(r), "stack", string(stack))
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.hit {
		s.value, s.stack, s.hit = r, stack, true
	}
}

// catch records a panic in progress and re-raises it. Use it deferred.
func (s *panicState) catch() {
	if r := recover(); r != nil {
		s.record(r)
		panic(r)
	}
}

// Init implements tea.Model.
func (g PanicGuard) Init() tea.Cmd {
	defer g.state.catch()
	return g.guard(g.inner.Init())
}

// Update implements tea.Model.
func (g PanicGuard) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	defer g.state.catch()
	m, cmd := g.inner.Update(msg)
	g.inner = m
	return g, g.guard(cmd)
}

// View implements tea.Model.
func (g PanicGuard) View() tea.View {
	defer g.state.catch()
	return g.inner.View()
}

// Inner returns the wrapped model.
func (g PanicGuard) Inner() tea.Model { return g.inner }

// guard wraps a command so a panic in it is recorded too. The commands of a
// batch are wrapped one by one when the batch runs.
func (g PanicGuard) guard(cmd tea.Cmd) tea.Cmd {
	if cmd == nil {
		return nil
	}
	return func() tea.Msg {
		defer g.state.catch()
		msg := cmd()
		if batch, ok := msg.(tea.BatchMsg); ok {
			out := make(tea.BatchMsg, len(batch))
			for i, c := range batch {
				out[i] = g.guard(c)
			}
			return out
		}
		return msg
	}
}

// FatalMessage is printed after the terminal is restored from a panic.
func FatalMessage(value any, logFile string) string {
	where := "Set OMNISYNC_LOG_FILE to a file path to record the stack trace next time."
	if logFile != "" {
		where = "The stack trace is in " + logFile + "."
	}
	return fmt.Sprintf("\nosync: fatal error: %v\n\nThis is a bug in osync. %s\nPlease report it at %s\n", value, where, BugReportURL)
}

// ErrFatal is what Report returns after a crash; the message is already
// printed.
var ErrFatal = errors.New("osync crashed")

// Report is called with the error of Program.Run. After a panic it prints
// the fatal-error message to w (Bubble Tea has restored the terminal and
// printed the stack trace by then) and returns ErrFatal; otherwise it
// returns err unchanged.
func (g PanicGuard) Report(err error, logFile string, w io.Writer) error {
	value, _, panicked := g.Panicked()
	if !panicked && !errors.Is(err, tea.ErrProgramPanic) {
		return err
	}
	if !panicked {
		value = err
	}
	_, _ = fmt.Fprint(w, FatalMessage(value, logFile))
	return ErrFatal
}

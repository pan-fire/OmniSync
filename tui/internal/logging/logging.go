// Package logging is the TUI's debug log. It writes only to the file named
// by OMNISYNC_LOG_FILE (config key log_file): while the TUI runs, the
// terminal belongs to the alternate screen, and anything written to stderr
// would corrupt it. Without a log file every record is discarded.
package logging

import (
	"io"
	"log"
	"log/slog"
	"os"
	"sync/atomic"
)

var current atomic.Pointer[slog.Logger]

func init() {
	current.Store(slog.New(slog.DiscardHandler))
}

// L returns the debug logger.
func L() *slog.Logger {
	return current.Load()
}

// SetOutput sends debug-level records (and the standard log package) to w;
// nil discards them.
func SetOutput(w io.Writer) {
	if w == nil {
		current.Store(slog.New(slog.DiscardHandler))
		log.SetOutput(io.Discard)
		return
	}
	current.Store(slog.New(slog.NewTextHandler(w, &slog.HandlerOptions{Level: slog.LevelDebug})))
	log.SetOutput(w)
}

// Open starts logging to the file at path (appending, created with mode
// 0600). An empty path turns logging off. The returned function closes the
// file and turns logging off again.
func Open(path string) (func() error, error) {
	if path == "" {
		SetOutput(nil)
		return func() error { return nil }, nil
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return nil, err
	}
	SetOutput(f)
	return func() error {
		SetOutput(nil)
		return f.Close()
	}, nil
}

// Truncate shortens s to at most n bytes for a log record, marking the cut.
func Truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "...(truncated)"
}

package cli

import (
	"context"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/spf13/cobra"
)

// LogsPollInterval is how often 'logs --follow' asks for new entries.
var LogsPollInterval = 2 * time.Second

// logLevels are the levels the backend filters by.
var logLevels = []string{"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

// logCategories are the categories the backend filters by.
var logCategories = []string{"audit", "errors"}

// maxLogPage is the backend's largest page of log entries.
const maxLogPage = 200

func logsCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "logs [--level LEVEL] [--category audit|errors] [--limit N] [--skip N] [--follow]",
		Short: "Show the backend's log (newest last); --follow keeps printing new entries",
		Long: "Show the last entries of the backend's log, oldest first. --level shows only one level\n" +
			"(DEBUG, INFO, WARNING, ERROR or CRITICAL); --category audit shows the audit trail of user\n" +
			"actions, --category errors the ERROR and CRITICAL entries. Tracebacks are printed indented\n" +
			"under their entry. --skip leaves out that many of the newest entries, to page back\n" +
			"through older ones (the rotated log files included). --follow keeps asking for new entries every few\n" +
			"seconds until Ctrl+C. With --json the entries are a JSON array, or with --follow one JSON\n" +
			"object per line.",
		Args: usageArgs(cobra.NoArgs),
		RunE: func(cmd *cobra.Command, args []string) error {
			level, _ := cmd.Flags().GetString("level")
			level = strings.ToUpper(strings.TrimSpace(level))
			if level != "" && !contains(logLevels, level) {
				return usagef("--level must be one of %s", strings.Join(logLevels, ", "))
			}
			category, _ := cmd.Flags().GetString("category")
			category = strings.ToLower(strings.TrimSpace(category))
			if category != "" && !contains(logCategories, category) {
				return usagef("--category must be one of %s", strings.Join(logCategories, ", "))
			}
			limit, _ := cmd.Flags().GetInt("limit")
			if limit < 1 || limit > maxLogPage {
				return usagef("--limit must be between 1 and %d", maxLogPage)
			}
			skip, _ := cmd.Flags().GetInt("skip")
			if skip < 0 {
				return usagef("--skip must be 0 or more")
			}
			follow, _ := cmd.Flags().GetBool("follow")
			if follow && skip > 0 {
				return usagef("--skip cannot be combined with --follow")
			}
			client := newClientFromFlags(cmd)
			entries, err := client.GetLogsFiltered(cmd.Context(), skip, limit, level, category)
			if err != nil {
				return fmt.Errorf("failed to read the log: %w", err)
			}
			reverse(entries)
			out := cmd.OutOrStdout()
			if !follow {
				if isJSON(cmd) {
					if entries == nil {
						entries = []api.LogEntryResponse{}
					}
					return printJSON(out, entries)
				}
				for _, e := range entries {
					printLogEntry(out, e)
				}
				return nil
			}
			return followLogs(cmd, client, level, category, entries)
		},
	}
	cmd.Flags().String("level", "", "Only entries of this level: DEBUG, INFO, WARNING, ERROR or CRITICAL")
	cmd.Flags().String("category", "", "Only the audit trail (audit) or errors (errors)")
	cmd.Flags().Int("limit", 50, "Number of entries to show (1-200)")
	cmd.Flags().Int("skip", 0, "Leave out this many of the newest entries (page back through older ones)")
	cmd.Flags().BoolP("follow", "f", false, "Keep printing new entries until Ctrl+C")
	_ = cmd.RegisterFlagCompletionFunc("level", fixedCompletion(logLevels...))
	_ = cmd.RegisterFlagCompletionFunc("category", fixedCompletion(logCategories...))
	return cmd
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

func reverse[T any](s []T) {
	for i, j := 0, len(s)-1; i < j; i, j = i+1, j-1 {
		s[i], s[j] = s[j], s[i]
	}
}

func printLogEntry(w io.Writer, e api.LogEntryResponse) {
	_, _ = fmt.Fprintf(w, "%s  %-8s %s\n", e.Timestamp, e.Level, e.Message)
	if e.Exc != "" {
		for _, line := range strings.Split(e.Exc, "\n") {
			_, _ = fmt.Fprintf(w, "    %s\n", line)
		}
	}
}

// logCursor remembers what 'logs --follow' printed: the newest timestamp and
// how many entries with exactly that timestamp, by content. Entries have no
// ID, so a new page is compared against it.
type logCursor struct {
	last  string
	at    time.Time
	known bool
	seen  map[api.LogEntryResponse]int
}

func parseLogTime(s string) (time.Time, bool) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999999"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t, true
		}
	}
	return time.Time{}, false
}

// compare orders timestamps: by time when both parse, else as strings.
func (c *logCursor) compare(ts string) int {
	if t, ok := parseLogTime(ts); ok && c.known {
		return t.Compare(c.at)
	}
	return strings.Compare(ts, c.last)
}

// advance records printed entries (oldest first).
func (c *logCursor) advance(entries []api.LogEntryResponse) {
	for _, e := range entries {
		if c.seen == nil || c.last == "" || c.compare(e.Timestamp) > 0 {
			c.last = e.Timestamp
			c.at, c.known = parseLogTime(e.Timestamp)
			c.seen = map[api.LogEntryResponse]int{}
		}
		if e.Timestamp == c.last {
			c.seen[e]++
		}
	}
}

// fresh returns the entries of page (oldest first) not printed yet.
func (c *logCursor) fresh(page []api.LogEntryResponse) []api.LogEntryResponse {
	if c.seen == nil {
		return page
	}
	counts := map[api.LogEntryResponse]int{}
	var out []api.LogEntryResponse
	for _, e := range page {
		switch cmp := c.compare(e.Timestamp); {
		case cmp > 0:
			out = append(out, e)
		case cmp == 0:
			counts[e]++
			if counts[e] > c.seen[e] {
				out = append(out, e)
			}
		}
	}
	return out
}

// followLogs prints first, then polls for new entries until interrupted.
func followLogs(cmd *cobra.Command, client *api.Client, level, category string, first []api.LogEntryResponse) error {
	parent := cmd.Context()
	if parent == nil {
		parent = context.Background()
	}
	ctx, stop := signal.NotifyContext(parent, os.Interrupt, syscall.SIGTERM)
	defer stop()

	out := cmd.OutOrStdout()
	write := func(entries []api.LogEntryResponse) error {
		for _, e := range entries {
			if isJSON(cmd) {
				// One compact object per line, for line-by-line readers.
				if err := writeJSONLine(out, e); err != nil {
					return err
				}
				continue
			}
			printLogEntry(out, e)
		}
		return nil
	}
	cursor := &logCursor{}
	if err := write(first); err != nil {
		return err
	}
	cursor.advance(first)

	ticker := time.NewTicker(LogsPollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			page, err := client.GetLogsFiltered(ctx, 0, maxLogPage, level, category)
			if err != nil {
				if ctx.Err() != nil {
					return nil
				}
				continue // the backend may be restarting; keep trying until Ctrl+C
			}
			reverse(page)
			fresh := cursor.fresh(page)
			if err := write(fresh); err != nil {
				return err
			}
			cursor.advance(fresh)
		}
	}
}

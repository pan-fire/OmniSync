package cli

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"text/tabwriter"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/spf13/cobra"
)

// SyncPollInterval is how often push/pull poll the status while waiting.
var SyncPollInterval = 2 * time.Second

func newClientFromFlags(cmd *cobra.Command) *api.Client {
	cfg := loadConfig(cmd)
	return api.NewClient(cfg.URL, cfg.APIKey, version)
}

func isJSON(cmd *cobra.Command) bool {
	j, _ := cmd.Flags().GetBool("json")
	return j
}

func printJSON(w io.Writer, v interface{}) error {
	enc := json.NewEncoder(w)
	enc.SetIndent("", "  ")
	return enc.Encode(v)
}

func mark(ok bool) string {
	if ok {
		return theme.Glyphs().Check
	}
	return theme.Glyphs().Cross
}

func statusCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Args:  usageArgs(cobra.NoArgs),
		Short: "Show aggregate sync status",
		RunE: func(cmd *cobra.Command, args []string) error {
			client := newClientFromFlags(cmd)
			resp, err := client.AggregateStatus(cmd.Context())
			if err != nil {
				return fmt.Errorf("failed to fetch status: %w", err)
			}
			out := cmd.OutOrStdout()
			if isJSON(cmd) {
				return printJSON(out, resp)
			}
			w := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
			active := 0
			for _, ps := range resp.ProfilesSummary {
				if ps.State.Busy() {
					active++
				}
			}
			_, _ = fmt.Fprintf(w, "State\t%s\n", resp.OverallState)
			_, _ = fmt.Fprintf(w, "Profiles\t%d total, %d syncing\n", len(resp.ProfilesSummary), active)
			_, _ = fmt.Fprintf(w, "Pending\t%d changes\n", resp.TotalPendingChanges)
			for _, p := range resp.PausedProfiles {
				_, _ = fmt.Fprintf(w, "Paused\t%s (%d unresolved differences)\n", p.Slug, p.PendingChanges)
			}
			return w.Flush()
		},
	}
}

var errUnhealthy = errors.New("backend is unhealthy")

func healthCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "health",
		Args:  usageArgs(cobra.NoArgs),
		Short: "Check backend health (exit status 1 when unreachable or unhealthy)",
		RunE: func(cmd *cobra.Command, args []string) error {
			client := newClientFromFlags(cmd)
			resp, err := client.Health(cmd.Context())
			if err != nil {
				return fmt.Errorf("health check failed: %w", err)
			}
			out := cmd.OutOrStdout()
			if isJSON(cmd) {
				if err := printJSON(out, resp); err != nil {
					return err
				}
			} else {
				status := "HEALTHY"
				if !resp.Healthy() {
					status = "UNHEALTHY"
				}
				w := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
				_, _ = fmt.Fprintf(w, "Status\t%s (%s)\n", status, resp.Status)
				_, _ = fmt.Fprintf(w, "Database\t%s\n", mark(resp.DatabaseOK))
				_, _ = fmt.Fprintf(w, "Rclone\t%s\n", mark(resp.RcloneInstalled))
				_, _ = fmt.Fprintf(w, "Uptime\t%.0fs\n", resp.UptimeSeconds)
				_, _ = fmt.Fprintf(w, "Version\t%s\n", resp.Version)
				if err := w.Flush(); err != nil {
					return err
				}
			}
			// The backend's own status decides, so this agrees with GET /health.
			if !resp.Healthy() {
				return fmt.Errorf("%w (status %q)", errUnhealthy, resp.Status)
			}
			return nil
		},
	}
}

func profilesCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "profiles",
		Args:  usageArgs(cobra.NoArgs),
		Short: "List sync profiles with their sync mode (two-way or mirror)",
		RunE: func(cmd *cobra.Command, args []string) error {
			client := newClientFromFlags(cmd)
			resp, err := client.ListProfiles(cmd.Context())
			if err != nil {
				return fmt.Errorf("failed to list profiles: %w", err)
			}
			out := cmd.OutOrStdout()
			if isJSON(cmd) {
				return printJSON(out, resp)
			}
			w := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
			_, _ = fmt.Fprintf(w, "SLUG\tNAME\tMODE\tENABLED\tSTATE\tLOCAL\tREMOTE\n")
			for _, p := range resp {
				state := string(p.State)
				if p.ResyncRequired {
					state = "resync required"
				}
				if p.State == api.SyncStateError && p.LastError != nil {
					state += ": " + *p.LastError
				}
				_, _ = fmt.Fprintf(w, "%s\t%s\t%s\t%t\t%s\t%s\t%s\n", p.Slug, p.Name, modeLabel(p.SyncMode), p.Enabled, state, p.LocalDir, p.RemoteDir)
			}
			return w.Flush()
		},
	}
}

func jobsCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "jobs",
		Args:  usageArgs(cobra.NoArgs),
		Short: "List sync job history (newest first)",
		RunE: func(cmd *cobra.Command, args []string) error {
			client := newClientFromFlags(cmd)
			profile, _ := cmd.Flags().GetString("profile")
			limit, _ := cmd.Flags().GetInt("limit")
			resp, err := client.ListJobs(cmd.Context(), 0, limit, profile)
			if err != nil {
				return fmt.Errorf("failed to list jobs: %w", err)
			}
			out := cmd.OutOrStdout()
			if isJSON(cmd) {
				return printJSON(out, resp)
			}
			w := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
			_, _ = fmt.Fprintf(w, "ID\tPROFILE\tDIRECTION\tSTATUS\tSTARTED\tFILES\tERRORS\n")
			for _, j := range resp {
				p := profile
				if j.ProfileSlug != nil {
					p = *j.ProfileSlug
				}
				if p == "" {
					p = "-"
				}
				_, _ = fmt.Fprintf(w, "%d\t%s\t%s\t%s\t%s\t%d\t%d\n", j.ID, p, j.Direction, j.Status, j.StartedAt, j.FilesChanged, j.Errors)
			}
			return w.Flush()
		},
	}
	cmd.Flags().String("profile", "", "Filter by profile slug")
	cmd.Flags().Int("limit", 20, "Maximum number of jobs to return (1-100)")
	return cmd
}

func pushCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "push <profile>",
		Short: "Push a profile: make the remote match the local folder, and wait until it is done",
		Long: "Push a profile: make the remote match the local folder. Remote files that are not in the local\n" +
			"folder are deleted; replaced and deleted files are kept in .omnisync-trash. The command waits\n" +
			"until the sync has finished and exits non-zero if it failed. Ctrl+C stops waiting; the sync\n" +
			"itself keeps running on the server.",
		Args:              usageArgs(cobra.ExactArgs(1)),
		ValidArgsFunction: completeProfiles,
		RunE: func(cmd *cobra.Command, args []string) error {
			return runSync(cmd, args[0], api.SyncDirectionPush)
		},
	}
}

func pullCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "pull <profile>",
		Short: "Pull a profile: make the local folder match the remote, and wait until it is done",
		Long: "Pull a profile: make the local folder match the remote. Local files that are not on the remote\n" +
			"are deleted; replaced and deleted files are kept in .omnisync-trash. The command waits until the\n" +
			"sync has finished and exits non-zero if it failed. Ctrl+C stops waiting; the sync itself keeps\n" +
			"running on the server.",
		Args:              usageArgs(cobra.ExactArgs(1)),
		ValidArgsFunction: completeProfiles,
		RunE: func(cmd *cobra.Command, args []string) error {
			return runSync(cmd, args[0], api.SyncDirectionPull)
		},
	}
}

type startResult struct {
	resp *api.SyncStartResponse
	err  error
}

// syncAction is what a sync subcommand starts: a push, a pull, a two-way
// sync or a resync.
type syncAction struct {
	// name is the action in messages ("push", "two-way sync", ...) and
	// kind its value in --json output ("push", "pull", "two_way", "resync").
	name string
	kind string
	// start sends the request. It may answer only when the sync has
	// finished, or right away with a busy state; both are handled.
	start func(ctx context.Context, client *api.Client, slug string) (*api.SyncStartResponse, error)
}

// directionAction starts a sync in direction dir. No force: the CLI asks no
// confirmation, so a profile whose intervals are paused is refused (409)
// with the backend's reason.
func directionAction(dir api.SyncDirection) syncAction {
	name := string(dir)
	if dir == api.SyncDirectionTwoWay {
		name = "two-way sync"
	}
	return syncAction{name: name, kind: string(dir), start: func(ctx context.Context, client *api.Client, slug string) (*api.SyncStartResponse, error) {
		return client.StartProfileSync(ctx, slug, dir, false)
	}}
}

// syncOutput is where a sync subcommand writes: the outcome to out, the
// progress to progress (stderr with --json, so stdout stays one JSON value).
type syncOutput struct {
	out, progress io.Writer
	json          bool
}

// syncResult is the --json output of push, pull, sync and resync.
type syncResult struct {
	Profile        string        `json:"profile"`
	Action         string        `json:"action"`
	JobID          *int          `json:"job_id"`
	State          api.SyncState `json:"state"`
	FilesProcessed int           `json:"files_processed"`
	Errors         int           `json:"errors"`
	LastError      *string       `json:"last_error"`
}

// runSync starts a sync and waits for it. POST /profiles/{slug}/sync/start
// answers once the sync runs (or at once when it is refused); the status
// endpoint is then polled until the sync ends. If the connection drops
// while waiting, the command keeps following the status until the sync ends.
func runSync(cmd *cobra.Command, slug string, direction api.SyncDirection) error {
	return runSyncAction(cmd, newClientFromFlags(cmd), slug, directionAction(direction))
}

// runSyncAction starts a sync and waits for it. If the start request answers
// only when the sync has finished, it has no deadline, and meanwhile the
// status endpoint is polled for progress; if it answers while the sync still
// runs, the status is followed until the sync ends. If the connection drops
// while waiting, the command keeps following the status until the sync ends.
// With --json the progress goes to stderr and the outcome to stdout as a
// syncResult; a failed sync exits non-zero either way.
func runSyncAction(cmd *cobra.Command, client *api.Client, slug string, action syncAction) error {
	w := syncOutput{out: cmd.OutOrStdout(), progress: cmd.OutOrStdout(), json: isJSON(cmd)}
	if w.json {
		w.progress = cmd.ErrOrStderr()
	}
	parent := cmd.Context()
	if parent == nil {
		parent = context.Background()
	}
	ctx, stop := signal.NotifyContext(parent, os.Interrupt, syscall.SIGTERM)
	defer stop()

	before, err := client.ProfileSyncStatus(ctx, slug)
	if err != nil {
		if api.IsStatus(err, 404) {
			return fmt.Errorf("profile %q not found or not enabled: %w", slug, err)
		}
		return fmt.Errorf("cannot read the status of %q: %w", slug, err)
	}
	if before.State.Busy() {
		return refusedf("profile %q is already %s; wait for it to finish", slug, before.State)
	}

	_, _ = fmt.Fprintf(w.progress, "Starting %s for %s...\n", action.name, slug)
	done := make(chan startResult, 1)
	go func() {
		resp, err := action.start(ctx, client, slug)
		done <- startResult{resp, err}
	}()

	res := &syncResult{Profile: slug, Action: action.kind}
	ticker := time.NewTicker(SyncPollInterval)
	defer ticker.Stop()
	lastLine := ""
	for {
		select {
		case r := <-done:
			if r.err != nil {
				if ctx.Err() != nil {
					_, _ = fmt.Fprintf(w.progress, "Stopped waiting. The %s keeps running on the server; check it with 'status'.\n", action.name)
					return fmt.Errorf("interrupted while waiting for the %s of %s", action.name, slug)
				}
				var apiErr *api.ApiError
				if errors.As(r.err, &apiErr) {
					return fmt.Errorf("failed to %s %s: %w", action.name, slug, r.err)
				}
				_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Lost the connection while waiting (%v); following the sync status instead.\n", r.err)
				return followSync(ctx, client, w, res, action.name)
			}
			res.JobID = &r.resp.JobID
			if r.resp.State.Busy() {
				// Accepted and still running: follow it to the end.
				return followSync(ctx, client, w, res, action.name)
			}
			return reportSync(ctx, client, w, res, action.name, r.resp.State)
		case <-ticker.C:
			st, err := client.ProfileSyncStatus(ctx, slug)
			if err == nil && st.State.Busy() {
				line := fmt.Sprintf("  %s... (files: %d)", st.State, st.FilesProcessed)
				if line != lastLine {
					_, _ = fmt.Fprintln(w.progress, line)
					lastLine = line
				}
			}
		}
	}
}

// followSync polls the status until the running sync ends.
func followSync(ctx context.Context, client *api.Client, w syncOutput, res *syncResult, name string) error {
	ticker := time.NewTicker(SyncPollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			_, _ = fmt.Fprintf(w.progress, "Stopped waiting. The %s may still be running on the server.\n", name)
			return fmt.Errorf("interrupted while waiting for the %s of %s", name, res.Profile)
		case <-ticker.C:
			st, err := client.ProfileSyncStatus(ctx, res.Profile)
			if err != nil {
				continue // the backend may be restarting; keep trying until Ctrl+C
			}
			if !st.State.Busy() {
				return reportSync(ctx, client, w, res, name, st.State)
			}
		}
	}
}

// reportSync prints the outcome of a finished sync and turns a failure into
// an error (non-zero exit).
func reportSync(ctx context.Context, client *api.Client, w syncOutput, res *syncResult, name string, state api.SyncState) error {
	st, err := client.ProfileSyncStatus(ctx, res.Profile)
	if err == nil {
		state = st.State
		res.FilesProcessed, res.Errors, res.LastError = st.FilesProcessed, st.Errors, st.LastError
	}
	res.State = state
	if w.json {
		if err := printJSON(w.out, res); err != nil {
			return err
		}
	}
	if state == api.SyncStateError {
		reason := "no reason reported"
		if res.LastError != nil && *res.LastError != "" {
			reason = *res.LastError
		}
		return fmt.Errorf("%s of %s failed: %s", name, res.Profile, reason)
	}
	if w.json {
		return nil
	}
	if st != nil {
		_, _ = fmt.Fprintf(w.out, "%s of %s complete (files: %d, errors: %d)\n", name, res.Profile, st.FilesProcessed, st.Errors)
	} else {
		_, _ = fmt.Fprintf(w.out, "%s of %s complete\n", name, res.Profile)
	}
	return nil
}

package cli

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/spf13/cobra"
)

// BackupPollInterval is how often 'backups run --wait' and 'backups restore
// --wait' poll the job while waiting.
var BackupPollInterval = 2 * time.Second

func backupsCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "backups <profile>",
		Short: "List a profile's backup targets (run, snapshots and restore as subcommands)",
		Long: "List the backup targets of a profile. The subcommands run a backup, list a target's snapshots\n" +
			"and restore one; they take the profile slug and the target ID shown here.",
		Args:              usageArgs(cobra.ExactArgs(1)),
		ValidArgsFunction: completeProfiles,
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := newClientFromFlags(cmd).ListBackupTargets(cmd.Context(), args[0])
			if err != nil {
				return fmt.Errorf("failed to list the backup targets of %s: %w", args[0], err)
			}
			if resp == nil {
				resp = []api.BackupTargetResponse{}
			}
			return emit(cmd, resp, func(w io.Writer) {
				_, _ = fmt.Fprintf(w, "ID\tNAME\tTYPE\tMODE\tPATH\tENABLED\tLAST BACKUP\tSTATUS\tNEXT\n")
				for _, t := range resp {
					status := orDash(t.LastBackupStatus)
					if t.Overdue {
						status += " (overdue)"
					}
					_, _ = fmt.Fprintf(w, "%d\t%s\t%s\t%s\t%s\t%t\t%s\t%s\t%s\n", t.ID, t.Name, t.TargetType, t.BackupMode,
						t.TargetPath, t.Enabled, orDash(t.LastBackupAt), status, orDash(t.NextScheduledAt))
				}
			})
		},
	}
	cmd.AddCommand(backupRunCmd(), backupSnapshotsCmd(), backupRestoreCmd())
	return cmd
}

// targetID parses a backup target ID argument.
func targetID(arg string) (int, error) {
	id, err := strconv.Atoi(arg)
	if err != nil || id <= 0 {
		return 0, usagef("backup target ID must be a positive number, not %q", arg)
	}
	return id, nil
}

// completeProfileThenNothing completes the profile slug in first position.
func completeProfileThenNothing(cmd *cobra.Command, args []string, s string) ([]string, cobra.ShellCompDirective) {
	if len(args) == 0 {
		return completeProfiles(cmd, args, s)
	}
	return nil, cobra.ShellCompDirectiveNoFileComp
}

// backupStarted is the --json output when the command left a backup or
// restore running on the server (no --wait).
type backupStarted struct {
	Profile  string              `json:"profile"`
	TargetID int                 `json:"target_id"`
	JobID    int                 `json:"job_id"`
	Status   api.BackupJobStatus `json:"status"`
}

// runBackupJob starts a backup or restore and reports the job. The backend
// answers the start at once with the job running (or refuses it at once:
// 409 already running / profile busy, 404, 422). Without wait the command
// then says that the job started and leaves it running on the server; with
// wait it polls the job (GET .../backups/{id}/jobs/{job_id}) every
// BackupPollInterval until it has ended. A failed or skipped job exits
// non-zero.
func runBackupJob(cmd *cobra.Command, client *api.Client, name, slug string, id int, wait bool,
	start func(ctx context.Context) (*api.BackupJobResponse, error)) error {
	progress := cmd.OutOrStdout()
	if isJSON(cmd) {
		progress = cmd.ErrOrStderr()
	}
	parent := cmd.Context()
	if parent == nil {
		parent = context.Background()
	}
	ctx, stop := signal.NotifyContext(parent, os.Interrupt, syscall.SIGTERM)
	defer stop()

	job, err := start(ctx)
	if err != nil {
		return fmt.Errorf("failed to %s %s (target %d): %w", name, slug, id, err)
	}
	if job.Status != api.BackupJobRunning {
		return reportBackupJob(cmd, name, slug, job)
	}
	if !wait {
		if isJSON(cmd) {
			return printJSON(cmd.OutOrStdout(), backupStarted{Profile: slug, TargetID: id, JobID: job.ID, Status: job.Status})
		}
		_, _ = fmt.Fprintf(cmd.OutOrStdout(), "Started the %s of %s (target %d, job %d); it runs on the server. Check it with 'backups %s'.\n",
			name, slug, id, job.ID, slug)
		return nil
	}
	_, _ = fmt.Fprintf(progress, "Started the %s of %s (target %d, job %d); waiting until it is done...\n", name, slug, id, job.ID)
	done, err := client.WaitForBackupJob(ctx, slug, id, job.ID, BackupPollInterval)
	if err != nil {
		if ctx.Err() != nil {
			_, _ = fmt.Fprintf(progress, "Stopped waiting. The %s keeps running on the server.\n", name)
			return fmt.Errorf("interrupted while waiting for the %s of %s", name, slug)
		}
		return fmt.Errorf("lost track of the %s of %s (target %d, job %d): %w", name, slug, id, job.ID, err)
	}
	return reportBackupJob(cmd, name, slug, done)
}

func reportBackupJob(cmd *cobra.Command, name, slug string, job *api.BackupJobResponse) error {
	if isJSON(cmd) {
		if err := printJSON(cmd.OutOrStdout(), job); err != nil {
			return err
		}
	}
	reason := "no reason reported"
	if job.ErrorMessage != nil && *job.ErrorMessage != "" {
		reason = *job.ErrorMessage
	}
	if job.ErrorCode != nil && *job.ErrorCode != "" {
		reason += " [" + *job.ErrorCode + "]"
	}
	switch job.Status {
	case api.BackupJobFailed:
		return fmt.Errorf("%s of %s failed: %s", name, slug, reason)
	case api.BackupJobSkipped:
		return fmt.Errorf("%s of %s was skipped: %s", name, slug, reason)
	}
	if isJSON(cmd) {
		return nil
	}
	line := fmt.Sprintf("%s of %s %s (job %d", name, slug, job.Status, job.ID)
	if job.SnapshotID != nil && *job.SnapshotID != "" {
		line += ", snapshot " + *job.SnapshotID
	}
	if job.SizeBytes != nil {
		line += ", " + formatBytes(job.SizeBytes)
	}
	_, _ = fmt.Fprintln(cmd.OutOrStdout(), line+")")
	return nil
}

func backupRunCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "run <profile> <target-id> [--wait]",
		Short: "Back up a profile to one target now",
		Long: "Start a backup of a profile to one of its targets now. The server starts the backup and\n" +
			"answers at once. Without --wait the command prints the job ID and exits 0 while the backup\n" +
			"runs on the server. With --wait it polls the job until it has ended and exits 1 when it\n" +
			"failed or was skipped (target unreachable). Exits 3 when a backup of that target is already\n" +
			"running or the profile is busy (a sync, backup or restore of it runs).",
		Args:              usageArgs(cobra.ExactArgs(2)),
		ValidArgsFunction: completeProfileThenNothing,
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := targetID(args[1])
			if err != nil {
				return err
			}
			wait, _ := cmd.Flags().GetBool("wait")
			client := newClientFromFlags(cmd)
			return runBackupJob(cmd, client, "backup", args[0], id, wait, func(ctx context.Context) (*api.BackupJobResponse, error) {
				return client.RunBackup(ctx, args[0], id)
			})
		},
	}
	cmd.Flags().Bool("wait", false, "Wait until the backup has ended")
	return cmd
}

func backupSnapshotsCmd() *cobra.Command {
	return &cobra.Command{
		Use:               "snapshots <profile> <target-id>",
		Short:             "List the snapshots of a backup target",
		Args:              usageArgs(cobra.ExactArgs(2)),
		ValidArgsFunction: completeProfileThenNothing,
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := targetID(args[1])
			if err != nil {
				return err
			}
			resp, err := newClientFromFlags(cmd).ListSnapshots(cmd.Context(), args[0], id)
			if err != nil {
				return fmt.Errorf("failed to list the snapshots of %s (target %d): %w", args[0], id, err)
			}
			if resp == nil {
				resp = []api.SnapshotResponse{}
			}
			return emit(cmd, resp, func(w io.Writer) {
				_, _ = fmt.Fprintf(w, "SNAPSHOT\tCREATED\tSIZE\tSTATUS\tKIND\n")
				for _, s := range resp {
					kind := s.Kind
					if kind == "" {
						kind = "full"
					}
					if s.Latest {
						kind += " (latest)"
					}
					_, _ = fmt.Fprintf(w, "%s\t%s\t%s\t%s\t%s\n", s.SnapshotID, s.CreatedAt, formatBytes(s.SizeBytes), s.Status, kind)
				}
			})
		},
	}
}

// restoreScopes explains each --scope value for the confirmation.
var restoreScopes = map[api.RestoreScope]string{
	api.RestoreScopeLocalOnly:  "the local folder only",
	api.RestoreScopeRemoteOnly: "the remote folder only",
	api.RestoreScopeBoth:       "both the local and the remote folder",
}

func backupRestoreCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "restore <profile> <target-id> <snapshot> --scope local_only|remote_only|both [--yes] [--wait]",
		Short: "Restore a profile from a backup snapshot (asks first; --yes to skip)",
		Long: "Restore a profile's files from a snapshot of a backup target (see 'backups snapshots').\n" +
			"--scope picks what is restored: local_only, remote_only or both. Files the restore replaces\n" +
			"are overwritten with the snapshot's version.\n\n" +
			"Without --yes the command asks on a terminal and refuses (exit 3) when it cannot ask (a\n" +
			"script or a pipe). The server starts the restore and answers at once. Without --wait the\n" +
			"command prints the job ID and exits 0 while the restore runs on the server; with --wait it\n" +
			"polls the job until it has ended and exits 1 when it failed. Exits 3 when the profile is\n" +
			"busy (a sync, backup or restore of it runs).",
		Args:              usageArgs(cobra.ExactArgs(3)),
		ValidArgsFunction: completeProfileThenNothing,
		RunE: func(cmd *cobra.Command, args []string) error {
			slug, snapshot := args[0], args[2]
			id, err := targetID(args[1])
			if err != nil {
				return err
			}
			scopeFlag, _ := cmd.Flags().GetString("scope")
			scope := api.RestoreScope(scopeFlag)
			if _, ok := restoreScopes[scope]; !ok {
				return usagef("--scope must be local_only, remote_only or both")
			}
			if strings.TrimSpace(snapshot) == "" {
				return usagef("snapshot must not be empty")
			}
			client := newClientFromFlags(cmd)
			if yes, _ := cmd.Flags().GetBool("yes"); !yes {
				if err := confirmRestore(cmd, client, slug, id, snapshot, scope); err != nil {
					return err
				}
			}
			wait, _ := cmd.Flags().GetBool("wait")
			return runBackupJob(cmd, client, "restore", slug, id, wait, func(ctx context.Context) (*api.BackupJobResponse, error) {
				return client.RestoreSnapshot(ctx, slug, id, api.RestoreRequest{SnapshotID: snapshot, RestoreScope: scope})
			})
		},
	}
	cmd.Flags().String("scope", "", "What to restore: local_only, remote_only or both (required)")
	cmd.Flags().BoolP("yes", "y", false, "Restore without asking")
	cmd.Flags().Bool("wait", false, "Wait until the restore has ended")
	_ = cmd.RegisterFlagCompletionFunc("scope", fixedCompletion("local_only", "remote_only", "both"))
	return cmd
}

// restoreWarning says what a restore of snapshot changes, by the snapshot's
// kind (like the web UI's restore dialog).
func restoreWarning(ctx context.Context, client *api.Client, slug string, id int, snapshot string, scope api.RestoreScope) string {
	kind := ""
	if snaps, err := client.ListSnapshots(ctx, slug, id); err == nil {
		for _, s := range snaps {
			if s.SnapshotID == snapshot {
				kind = s.Kind
			}
		}
	}
	where := restoreScopes[scope]
	if kind == "legacy" {
		return "The files of this version are copied into " + where + " and overwrite the versions\n" +
			"there. Nothing is deleted: files that are not in this version stay."
	}
	return "This makes " + where + " identical to the snapshot: files changed since then\n" +
		"are overwritten and files that are not in the snapshot are removed."
}

// confirmRestore explains the restore and asks on a terminal. Without a
// terminal it refuses: a script must pass --yes.
func confirmRestore(cmd *cobra.Command, client *api.Client, slug string, id int, snapshot string, scope api.RestoreScope) error {
	in := cmd.InOrStdin()
	if !IsTerminal(in) {
		return fmt.Errorf("%w: not asking without a terminal; pass --yes to restore %q", errNotConfirmed, slug)
	}
	w := cmd.ErrOrStderr()
	_, _ = fmt.Fprintf(w, "Restore profile %q from snapshot %s of backup target %d?\n\n", slug, snapshot, id)
	if p, err := client.GetProfile(cmd.Context(), slug); err == nil {
		_, _ = fmt.Fprintf(w, "  Local:  %s\n  Remote: %s\n\n", p.LocalDir, p.RemoteDir)
	}
	_, _ = fmt.Fprintln(w, restoreWarning(cmd.Context(), client, slug, id, snapshot, scope))
	_, _ = fmt.Fprintln(w, "Syncing of the profile is paused while the restore runs.")
	_, _ = fmt.Fprint(w, "\nRestore now? [y/N] ")
	line, _ := bufio.NewReader(in).ReadString('\n')
	switch strings.ToLower(strings.TrimSpace(line)) {
	case "y", "yes":
		return nil
	}
	_, _ = fmt.Fprintln(w, "Restore cancelled; nothing was changed.")
	return fmt.Errorf("%w: cancelled", errNotConfirmed)
}

package cli

import (
	"fmt"
	"io"
	"strconv"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/spf13/cobra"
)

// keepResolutions maps --keep to the backend's resolution.
var keepResolutions = map[string]api.ConflictResolution{
	"local":   api.ConflictKeepLocal,
	"remote":  api.ConflictKeepRemote,
	"both":    api.ConflictKeepBoth,
	"dismiss": api.ConflictDismiss,
}

func conflictsCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "conflicts",
		Short: "List unresolved conflicts (resolve them with 'conflicts resolve')",
		Args:  usageArgs(cobra.NoArgs),
		RunE: func(cmd *cobra.Command, args []string) error {
			profile, _ := cmd.Flags().GetString("profile")
			resp, err := newClientFromFlags(cmd).ListConflicts(cmd.Context(), profile)
			if err != nil {
				return fmt.Errorf("failed to list conflicts: %w", err)
			}
			if resp == nil {
				resp = []api.ConflictResponse{}
			}
			return emit(cmd, resp, func(w io.Writer) {
				_, _ = fmt.Fprintf(w, "ID\tPROFILE\tPATH\tLOCAL MODIFIED\tREMOTE MODIFIED\tKEPT\n")
				for _, c := range resp {
					kept := "-"
					if c.TwoWay() {
						kept = "both versions (" + orDash(c.LocalKeptAs) + ", " + orDash(c.RemoteKeptAs) + ")"
					}
					_, _ = fmt.Fprintf(w, "%d\t%s\t%s\t%s\t%s\t%s\n", c.ID, orDash(c.ProfileSlug), c.FilePath,
						orDash(c.LocalModified), orDash(c.RemoteModified), kept)
				}
			})
		},
	}
	cmd.Flags().String("profile", "", "Only the conflicts of this profile (slug)")
	_ = cmd.RegisterFlagCompletionFunc("profile", func(c *cobra.Command, _ []string, s string) ([]string, cobra.ShellCompDirective) {
		return completeProfiles(c, nil, s)
	})
	cmd.AddCommand(conflictResolveCmd())
	return cmd
}

func conflictResolveCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "resolve <id> --keep local|remote|both|dismiss",
		Short: "Resolve a conflict: keep the local, the remote or both versions, or dismiss it",
		Long: "Resolve a conflict by its ID (from 'conflicts').\n\n" +
			"  --keep local    copy the local file over the remote one\n" +
			"  --keep remote   copy the remote file over the local one\n" +
			"  --keep both     keep both versions on both sides (the remote one as <name>.conflict-<time>)\n" +
			"  --keep dismiss  only close the record; no file is changed\n\n" +
			"A replaced version goes to .omnisync-trash on its side. For a conflict a two-way sync found,\n" +
			"both versions already exist: local/remote keep only that version under the original name,\n" +
			"both and dismiss close the record and leave both files.\n" +
			"Exits 3 when the conflict cannot be resolved now (already resolved, profile not running, a\n" +
			"file changed since) and 4 when there is no such conflict.",
		Args: usageArgs(cobra.ExactArgs(1)),
		RunE: func(cmd *cobra.Command, args []string) error {
			id, err := strconv.Atoi(args[0])
			if err != nil || id <= 0 {
				return usagef("conflict ID must be a positive number, not %q", args[0])
			}
			keep, _ := cmd.Flags().GetString("keep")
			resolution, ok := keepResolutions[keep]
			if !ok {
				return usagef("--keep must be local, remote, both or dismiss")
			}
			resp, err := newClientFromFlags(cmd).ResolveConflict(cmd.Context(), id, resolution)
			if err != nil {
				return fmt.Errorf("failed to resolve conflict %d: %w", id, err)
			}
			return emit(cmd, resp, func(w io.Writer) {
				_, _ = fmt.Fprintf(w, "Conflict %d (%s) resolved: %s\n", resp.ID, resp.FilePath, resolution)
			})
		},
	}
	cmd.Flags().String("keep", "", "local, remote, both or dismiss (required)")
	_ = cmd.RegisterFlagCompletionFunc("keep", fixedCompletion("local", "remote", "both", "dismiss"))
	return cmd
}

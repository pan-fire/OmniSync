package cli

import (
	"fmt"
	"io"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/spf13/cobra"
)

func remotesCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "remotes",
		Short: "List the configured rclone remotes (test and about as subcommands)",
		Args:  usageArgs(cobra.NoArgs),
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := newClientFromFlags(cmd).ListRemotes(cmd.Context())
			if err != nil {
				return fmt.Errorf("failed to list remotes: %w", err)
			}
			if resp == nil {
				resp = []api.RemoteResponse{}
			}
			return emit(cmd, resp, func(w io.Writer) {
				_, _ = fmt.Fprintf(w, "NAME\tTYPE\tLAST VERIFIED\n")
				for _, r := range resp {
					_, _ = fmt.Fprintf(w, "%s\t%s\t%s\n", r.Name, r.Type, orDash(r.LastVerified))
				}
			})
		},
	}
	cmd.AddCommand(remoteTestCmd(), remoteAboutCmd())
	return cmd
}

func remoteTestCmd() *cobra.Command {
	return &cobra.Command{
		Use:               "test <name>",
		Short:             "Test the connection to a remote (exits 1 when it fails)",
		Args:              usageArgs(cobra.ExactArgs(1)),
		ValidArgsFunction: completeRemotes,
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := newClientFromFlags(cmd).TestRemote(cmd.Context(), args[0])
			if err != nil {
				return fmt.Errorf("failed to test remote %s: %w", args[0], err)
			}
			if err := emit(cmd, resp, func(w io.Writer) {
				if !resp.Success {
					return
				}
				if resp.LatencyMs != nil {
					_, _ = fmt.Fprintf(w, "Remote %s is reachable (%d ms)\n", args[0], *resp.LatencyMs)
				} else {
					_, _ = fmt.Fprintf(w, "Remote %s is reachable\n", args[0])
				}
			}); err != nil {
				return err
			}
			if !resp.Success {
				return fmt.Errorf("remote %s is not reachable: %s", args[0], orDash(resp.Error))
			}
			return nil
		},
	}
}

// formatBytes prints a size in binary units, or "-" when unknown.
func formatBytes(n *int64) string {
	if n == nil {
		return "-"
	}
	const unit = 1024
	v := float64(*n)
	if v < unit {
		return fmt.Sprintf("%d B", *n)
	}
	exp := 0
	for v >= unit && exp < 5 {
		v /= unit
		exp++
	}
	return fmt.Sprintf("%.1f %ciB", v, "KMGTP"[exp-1])
}

func remoteAboutCmd() *cobra.Command {
	return &cobra.Command{
		Use:               "about <name>",
		Short:             "Show a remote's storage usage (total, used, free, trash)",
		Args:              usageArgs(cobra.ExactArgs(1)),
		ValidArgsFunction: completeRemotes,
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := newClientFromFlags(cmd).RemoteStorageInfo(cmd.Context(), args[0])
			if err != nil {
				return fmt.Errorf("failed to read the storage usage of %s: %w", args[0], err)
			}
			return emit(cmd, resp, func(w io.Writer) {
				if !resp.Supported {
					_, _ = fmt.Fprintf(w, "Remote %s does not report its storage usage.\n", args[0])
					return
				}
				_, _ = fmt.Fprintf(w, "Total\t%s\n", formatBytes(resp.TotalBytes))
				_, _ = fmt.Fprintf(w, "Used\t%s\n", formatBytes(resp.UsedBytes))
				_, _ = fmt.Fprintf(w, "Free\t%s\n", formatBytes(resp.FreeBytes))
				_, _ = fmt.Fprintf(w, "Trash\t%s\n", formatBytes(resp.TrashedBytes))
			})
		},
	}
}

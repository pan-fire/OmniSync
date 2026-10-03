package cli

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/spf13/cobra"
)

// writeJSONLine writes v as one compact JSON line.
func writeJSONLine(w io.Writer, v any) error {
	return json.NewEncoder(w).Encode(v)
}

func notificationsCmd() *cobra.Command {
	cmd := groupCmd("notifications", "Notification commands (test)", "")
	test := &cobra.Command{
		Use:   "test [--channel NAME]",
		Short: "Send a test notification (exits 1 when a channel failed)",
		Long: "Send a test notification to every enabled channel, or with --channel to that one channel,\n" +
			"even if it is turned off (so it can be checked first). Exits 1 when nothing was delivered or a\n" +
			"channel failed, and 4 for an unknown channel.",
		Args: usageArgs(cobra.NoArgs),
		RunE: func(cmd *cobra.Command, args []string) error {
			channel, _ := cmd.Flags().GetString("channel")
			resp, err := newClientFromFlags(cmd).TestNotificationChannel(cmd.Context(), channel)
			if err != nil {
				return fmt.Errorf("failed to send a test notification: %w", err)
			}
			failed := make([]string, 0, len(resp.Errors))
			for ch, code := range resp.Errors {
				failed = append(failed, ch+" ("+code+")")
			}
			sort.Strings(failed)
			if err := emit(cmd, resp, func(w io.Writer) {
				delivered := "none"
				if len(resp.ChannelsDelivered) > 0 {
					delivered = strings.Join(resp.ChannelsDelivered, ", ")
				}
				_, _ = fmt.Fprintf(w, "Delivered\t%s\n", delivered)
				if len(failed) > 0 {
					_, _ = fmt.Fprintf(w, "Failed\t%s\n", strings.Join(failed, ", "))
				}
			}); err != nil {
				return err
			}
			if !resp.Success {
				if len(failed) > 0 {
					return fmt.Errorf("test notification failed: %s (details in the backend log)", strings.Join(failed, ", "))
				}
				return fmt.Errorf("test notification was not delivered: no channel is enabled")
			}
			return nil
		},
	}
	test.Flags().String("channel", "", "Test only this channel (webpush or host_native)")
	_ = test.RegisterFlagCompletionFunc("channel", func(c *cobra.Command, _ []string, _ string) ([]string, cobra.ShellCompDirective) {
		ctx, cancel := context.WithTimeout(context.Background(), completionTimeout)
		defer cancel()
		cfg, err := newClientFromFlags(c).NotificationConfig(ctx)
		if err != nil {
			return nil, cobra.ShellCompDirectiveNoFileComp
		}
		names := make([]string, 0, len(cfg.Channels))
		for name := range cfg.Channels {
			names = append(names, name)
		}
		sort.Strings(names)
		return names, cobra.ShellCompDirectiveNoFileComp
	})
	cmd.AddCommand(test)
	return cmd
}

package cli

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/spf13/cobra"
)

// IsTerminal reports whether r is an interactive terminal. resync asks for
// confirmation only there; tests replace it.
var IsTerminal = func(r io.Reader) bool {
	f, ok := r.(*os.File)
	if !ok {
		return false
	}
	info, err := f.Stat()
	return err == nil && info.Mode()&os.ModeCharDevice != 0
}

// errNotConfirmed is returned when a resync or a restore was not confirmed.
var errNotConfirmed = errors.New("not confirmed")

// modeLabel names a sync mode for the profile list.
func modeLabel(mode api.SyncMode) string {
	switch mode {
	case api.SyncModeTwoWay:
		return "two-way"
	case api.SyncModeMirror:
		return "mirror"
	case "":
		return "-"
	}
	return string(mode)
}

// twoWayProfile reads a profile and refuses a mirror profile with what to
// do instead; action names the command in the message.
func twoWayProfile(cmd *cobra.Command, client *api.Client, slug, action string) (*api.ProfileStatusResponse, error) {
	p, err := client.GetProfile(cmd.Context(), slug)
	if err != nil {
		if api.IsStatus(err, 404) {
			return nil, fmt.Errorf("profile %q not found: %w", slug, err)
		}
		return nil, fmt.Errorf("cannot read profile %q: %w", slug, err)
	}
	if !p.TwoWay() {
		return nil, refusedf("profile %q syncs in mirror mode, so %s is not available: switch it to two-way "+
			"in the web UI or the TUI (profile, key w) first, or use push/pull", slug, action)
	}
	return p, nil
}

func syncCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "sync <profile>",
		Short: "Sync a two-way profile now (both directions), and wait until it is done",
		Long: "Run a two-way sync of a two-way profile now, like \"Sync now\" in the web UI: changes on either\n" +
			"side are carried to the other, deletions included (a deleted file is kept in that side's\n" +
			".omnisync-trash), and a file changed on both sides keeps both versions. The command waits\n" +
			"until the sync has finished and exits non-zero if it failed or was refused, for example for a\n" +
			"mirror profile, a profile that needs a resync, or one whose automatic syncs are paused.\n" +
			"Ctrl+C stops waiting; the sync itself keeps running on the server.",
		Args:              usageArgs(cobra.ExactArgs(1)),
		ValidArgsFunction: completeProfiles,
		RunE: func(cmd *cobra.Command, args []string) error {
			client := newClientFromFlags(cmd)
			p, err := twoWayProfile(cmd, client, args[0], "a two-way sync")
			if err != nil {
				return err
			}
			if p.ResyncRequired {
				reason := ""
				if p.LastError != nil && *p.LastError != "" {
					reason = " (" + *p.LastError + ")"
				}
				return refusedf("profile %q needs a resync first%s: run 'resync %s'", args[0], reason, args[0])
			}
			return runSyncAction(cmd, client, args[0], directionAction(api.SyncDirectionTwoWay))
		},
	}
}

func resyncCmd() *cobra.Command {
	cmd := &cobra.Command{
		Use:   "resync <profile>",
		Short: "Resync a two-way profile: make both folders the union of both sides (asks first; --yes to skip)",
		Long: "Resync a two-way profile: both folders become the union of both sides. Files that are in only\n" +
			"one folder are copied to the other and nothing is deleted; where a file differs, the newer\n" +
			"version wins and the older one is moved to .omnisync-trash on its side. Afterwards automatic\n" +
			"two-way syncing resumes. The first sync after switching a profile to two-way is a resync too.\n\n" +
			"Without --yes the command asks on a terminal and refuses when it cannot ask (a script or a\n" +
			"pipe). It waits until the resync has finished and exits non-zero if it failed, was refused or\n" +
			"was not confirmed. Ctrl+C stops waiting; the resync itself keeps running on the server.",
		Args:              usageArgs(cobra.ExactArgs(1)),
		ValidArgsFunction: completeProfiles,
		RunE: func(cmd *cobra.Command, args []string) error {
			slug := args[0]
			client := newClientFromFlags(cmd)
			p, err := twoWayProfile(cmd, client, slug, "a resync")
			if err != nil {
				return err
			}
			if yes, _ := cmd.Flags().GetBool("yes"); !yes {
				if err := confirmResync(cmd, p); err != nil {
					return err
				}
			}
			return runSyncAction(cmd, client, slug, syncAction{name: "resync", kind: string(api.JobDirectionResync),
				start: func(ctx context.Context, client *api.Client, slug string) (*api.SyncStartResponse, error) {
					return client.ResyncProfile(ctx, slug)
				}})
		},
	}
	cmd.Flags().BoolP("yes", "y", false, "Resync without asking")
	return cmd
}

// confirmResync explains the resync and asks on a terminal. Without a
// terminal it refuses: a script must pass --yes.
func confirmResync(cmd *cobra.Command, p *api.ProfileStatusResponse) error {
	in := cmd.InOrStdin()
	if !IsTerminal(in) {
		return fmt.Errorf("%w: not asking without a terminal; pass --yes to resync %q", errNotConfirmed, p.Slug)
	}
	w := cmd.ErrOrStderr()
	_, _ = fmt.Fprintf(w, "Resync profile %q (%s)?\n\n", p.Name, p.Slug)
	_, _ = fmt.Fprintf(w, "  Local:  %s\n  Remote: %s\n\n", p.LocalDir, p.RemoteDir)
	_, _ = fmt.Fprintln(w, "Both folders become the union of both sides: files that are in only one folder")
	_, _ = fmt.Fprintln(w, "are copied to the other, and nothing is deleted. Where a file differs, the newer")
	_, _ = fmt.Fprintln(w, "version wins and the older one is moved to .omnisync-trash on its side.")
	_, _ = fmt.Fprintln(w, "Afterwards automatic two-way syncing resumes.")
	if p.ResyncRequired && p.LastError != nil && *p.LastError != "" {
		_, _ = fmt.Fprintf(w, "\nThis profile needs a resync: %s\n", *p.LastError)
	}
	_, _ = fmt.Fprint(w, "\nResync now? [y/N] ")
	line, _ := bufio.NewReader(in).ReadString('\n')
	switch strings.ToLower(strings.TrimSpace(line)) {
	case "y", "yes":
		return nil
	}
	_, _ = fmt.Fprintln(w, "Resync cancelled; nothing was changed.")
	return fmt.Errorf("%w: cancelled", errNotConfirmed)
}

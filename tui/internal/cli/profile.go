package cli

import (
	"fmt"
	"io"
	"text/tabwriter"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/spf13/cobra"
)

// emit writes v as JSON with --json, otherwise calls text with a tabwriter
// on stdout and flushes it.
func emit(cmd *cobra.Command, v any, text func(w io.Writer)) error {
	out := cmd.OutOrStdout()
	if isJSON(cmd) {
		return printJSON(out, v)
	}
	w := tabwriter.NewWriter(out, 0, 4, 2, ' ', 0)
	text(w)
	return w.Flush()
}

// profileArg is the argument validator and completion of commands that
// take one profile slug.
func profileArg(cmd *cobra.Command) *cobra.Command {
	cmd.Args = usageArgs(cobra.ExactArgs(1))
	cmd.ValidArgsFunction = completeProfiles
	return cmd
}

func orDash(s *string) string {
	if s == nil || *s == "" {
		return "-"
	}
	return *s
}

func profileCmd() *cobra.Command {
	cmd := groupCmd("profile", "Show and control one profile (show, enable, disable, stop, check, diff, resume, manual-flags)",
		"Show and control one sync profile. Use 'profiles' to list them all.")
	cmd.AddCommand(profileShowCmd(), profileEnableCmd(true), profileEnableCmd(false), profileStopCmd(),
		profileCheckCmd(), profileDiffCmd(), profileResumeCmd(), profileManualFlagsCmd())
	return cmd
}

func profileShowCmd() *cobra.Command {
	return profileArg(&cobra.Command{
		Use:   "show <profile>",
		Short: "Show a profile's settings and sync state",
		RunE: func(cmd *cobra.Command, args []string) error {
			p, err := newClientFromFlags(cmd).GetProfile(cmd.Context(), args[0])
			if err != nil {
				return fmt.Errorf("cannot read profile %q: %w", args[0], err)
			}
			return emit(cmd, p, func(w io.Writer) {
				state := string(p.State)
				if p.ResyncRequired {
					state += " (resync required)"
				}
				limit := "none"
				if p.MaxDelete != nil {
					limit = fmt.Sprintf("%d files", *p.MaxDelete)
				}
				_, _ = fmt.Fprintf(w, "Slug\t%s\n", p.Slug)
				_, _ = fmt.Fprintf(w, "Name\t%s\n", p.Name)
				_, _ = fmt.Fprintf(w, "Mode\t%s\n", modeLabel(p.SyncMode))
				_, _ = fmt.Fprintf(w, "Enabled\t%t\n", p.Enabled)
				_, _ = fmt.Fprintf(w, "State\t%s\n", state)
				_, _ = fmt.Fprintf(w, "Local\t%s\n", p.LocalDir)
				_, _ = fmt.Fprintf(w, "Remote\t%s\n", p.RemoteDir)
				_, _ = fmt.Fprintf(w, "Last sync\t%s\n", orDash(p.LastSync))
				_, _ = fmt.Fprintf(w, "Pending\t%d changes\n", p.PendingChanges)
				if p.IntervalsPaused {
					_, _ = fmt.Fprintf(w, "Intervals\tpaused since %s (resume with 'profile resume %s')\n", orDash(p.PausedAt), p.Slug)
				}
				_, _ = fmt.Fprintf(w, "Pull every\t%d min\n", p.PullIntervalMinutes)
				_, _ = fmt.Fprintf(w, "Debounce\t%d s\n", p.DebounceSeconds)
				_, _ = fmt.Fprintf(w, "Delete limit\t%s\n", limit)
				if p.LastError != nil && *p.LastError != "" {
					_, _ = fmt.Fprintf(w, "Last error\t%s\n", *p.LastError)
				}
			})
		},
	})
}

func profileEnableCmd(enable bool) *cobra.Command {
	verb, short := "enable", "Enable a profile and start its automatic syncing"
	if !enable {
		verb, short = "disable", "Disable a profile and stop its automatic syncing"
	}
	return profileArg(&cobra.Command{
		Use:   verb + " <profile>",
		Short: short,
		RunE: func(cmd *cobra.Command, args []string) error {
			client := newClientFromFlags(cmd)
			var p *api.ProfileResponse
			var err error
			if enable {
				p, err = client.EnableProfile(cmd.Context(), args[0])
			} else {
				p, err = client.DisableProfile(cmd.Context(), args[0])
			}
			if err != nil {
				return fmt.Errorf("failed to %s %s: %w", verb, args[0], err)
			}
			return emit(cmd, p, func(w io.Writer) {
				_, _ = fmt.Fprintf(w, "Profile %s %sd\n", p.Slug, verb)
			})
		},
	})
}

func profileStopCmd() *cobra.Command {
	return profileArg(&cobra.Command{
		Use:   "stop <profile>",
		Short: "Stop the running sync of a profile (automatic syncing continues)",
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := newClientFromFlags(cmd).StopProfileSync(cmd.Context(), args[0])
			if err != nil {
				return fmt.Errorf("failed to stop %s: %w", args[0], err)
			}
			return emit(cmd, resp, func(w io.Writer) {
				_, _ = fmt.Fprintf(w, "%s (state: %s)\n", resp.Message, resp.State)
			})
		},
	})
}

func profileCheckCmd() *cobra.Command {
	return profileArg(&cobra.Command{
		Use:   "check <profile>",
		Short: "Compare both folders now (the startup check) and list what differs",
		Long: "Compare the local and the remote folder now, like the check at startup, and list the files\n" +
			"that are only on one side or differ. It changes no file but refreshes the profile's pending\n" +
			"count. Exits 1 when the comparison failed.",
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := newClientFromFlags(cmd).CheckProfileSync(cmd.Context(), args[0])
			if err != nil {
				return fmt.Errorf("failed to check %s: %w", args[0], err)
			}
			if err := emit(cmd, resp, func(w io.Writer) {
				if resp.Error != nil {
					return
				}
				if !resp.HasChanges {
					_, _ = fmt.Fprintln(w, "Both folders match.")
					return
				}
				_, _ = fmt.Fprintf(w, "WHERE\tPATH\n")
				for _, group := range []struct {
					label string
					paths []string
				}{{"local only", resp.LocalOnly}, {"remote only", resp.RemoteOnly}, {"differs", resp.Differ}} {
					for _, p := range group.paths {
						_, _ = fmt.Fprintf(w, "%s\t%s\n", group.label, p)
					}
				}
			}); err != nil {
				return err
			}
			if resp.Error != nil {
				return fmt.Errorf("check of %s failed: %s", args[0], *resp.Error)
			}
			return nil
		},
	})
}

// categoryLabel names a diff category for the file list.
func categoryLabel(c api.ChangeCategory) string {
	switch c {
	case api.ChangeCategoryLocalOnly:
		return "local only"
	case api.ChangeCategoryRemoteOnly:
		return "remote only"
	case api.ChangeCategoryModifiedLocal:
		return "changed locally"
	case api.ChangeCategoryModifiedRemote:
		return "changed remotely"
	case api.ChangeCategoryModifiedBoth:
		return "changed on both"
	}
	return string(c)
}

func profileDiffCmd() *cobra.Command {
	return profileArg(&cobra.Command{
		Use:   "diff <profile>",
		Short: "Show the per-file differences between both folders, with a summary",
		Long: "Compare both folders file by file and print a summary per category and the list of files.\n" +
			"It changes no file. The result is kept by the backend for selective sync in the web UI or\n" +
			"the TUI. Exits 1 when the comparison failed.",
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := newClientFromFlags(cmd).ProfileDiff(cmd.Context(), args[0], 0, 0)
			if err != nil {
				return fmt.Errorf("failed to diff %s: %w", args[0], err)
			}
			if err := emit(cmd, resp, func(w io.Writer) {
				if resp.Error != nil {
					return
				}
				s := resp.Summary
				_, _ = fmt.Fprintf(w, "Local only\t%d\n", s.LocalOnly)
				_, _ = fmt.Fprintf(w, "Remote only\t%d\n", s.RemoteOnly)
				_, _ = fmt.Fprintf(w, "Changed locally\t%d\n", s.ModifiedLocal)
				_, _ = fmt.Fprintf(w, "Changed remotely\t%d\n", s.ModifiedRemote)
				_, _ = fmt.Fprintf(w, "Changed on both\t%d\n", s.ModifiedBoth)
				_, _ = fmt.Fprintf(w, "Manual\t%d\n", s.Manual)
				_, _ = fmt.Fprintf(w, "Total\t%d\n", s.Total)
				if len(resp.Files) == 0 {
					return
				}
				_, _ = fmt.Fprintf(w, "\nCATEGORY\tPATH\tNOTE\n")
				for _, f := range resp.Files {
					note := ""
					switch {
					case f.IsConflict && f.ManualFlag:
						note = "conflict, manual"
					case f.IsConflict:
						note = "conflict"
					case f.ManualFlag:
						note = "manual"
					}
					_, _ = fmt.Fprintf(w, "%s\t%s\t%s\n", categoryLabel(f.Category), f.Path, note)
				}
			}); err != nil {
				return err
			}
			if resp.Error != nil {
				return fmt.Errorf("diff of %s failed: %s", args[0], *resp.Error)
			}
			return nil
		},
	})
}

func profileResumeCmd() *cobra.Command {
	return profileArg(&cobra.Command{
		Use:   "resume <profile>",
		Short: "Resume a profile's paused sync intervals",
		Long: "Resume the automatic syncs of a profile whose intervals were paused (for example after the\n" +
			"startup check found differences). Exits 3 when the backend refuses (nothing to resume, or the\n" +
			"differences must be handled first).",
		RunE: func(cmd *cobra.Command, args []string) error {
			resp, err := newClientFromFlags(cmd).ResumeProfileIntervals(cmd.Context(), args[0])
			if err != nil {
				return fmt.Errorf("failed to resume %s: %w", args[0], err)
			}
			return emit(cmd, resp, func(w io.Writer) {
				_, _ = fmt.Fprintln(w, resp.Detail)
			})
		},
	})
}

// manualFlagCleared is the --json output of 'profile manual-flags --clear'.
type manualFlagCleared struct {
	Profile string `json:"profile"`
	Cleared string `json:"cleared"`
}

func profileManualFlagsCmd() *cobra.Command {
	cmd := profileArg(&cobra.Command{
		Use:   "manual-flags <profile> [--clear PATH]",
		Short: "List the files flagged for manual handling, or clear one flag",
		Long: "List the files of a profile that are flagged for manual handling: automatic syncs leave them\n" +
			"alone until the flag is cleared. --clear PATH clears the flag of one file (a path relative to\n" +
			"the profile folders, as listed); exits 4 when that file has no flag.",
		RunE: func(cmd *cobra.Command, args []string) error {
			client := newClientFromFlags(cmd)
			if cmd.Flags().Changed("clear") {
				path, _ := cmd.Flags().GetString("clear")
				if path == "" {
					return usagef("--clear needs a file path")
				}
				if err := client.ClearManualFlag(cmd.Context(), args[0], path); err != nil {
					return fmt.Errorf("failed to clear the manual flag of %s: %w", path, err)
				}
				res := manualFlagCleared{Profile: args[0], Cleared: path}
				return emit(cmd, res, func(w io.Writer) {
					_, _ = fmt.Fprintf(w, "Cleared the manual flag of %s\n", path)
				})
			}
			resp, err := client.ManualFlags(cmd.Context(), args[0])
			if err != nil {
				return fmt.Errorf("failed to list the manual flags of %s: %w", args[0], err)
			}
			if resp.Flags == nil {
				resp.Flags = []string{}
			}
			return emit(cmd, resp, func(w io.Writer) {
				if len(resp.Flags) == 0 {
					_, _ = fmt.Fprintln(w, "No files are flagged for manual handling.")
				}
				for _, f := range resp.Flags {
					_, _ = fmt.Fprintln(w, f)
				}
			})
		},
	})
	cmd.Flags().String("clear", "", "Clear the manual flag of this file instead of listing")
	return cmd
}

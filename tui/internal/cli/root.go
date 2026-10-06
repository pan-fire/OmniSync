// Package cli builds the osync command line: the TUI (root command) and the
// scriptable subcommands.
package cli

import (
	"fmt"
	"os"
	"runtime/debug"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/config"
	"github.com/pan-fire/OmniSync/tui/internal/logging"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui"
	"github.com/spf13/cobra"
)

// version is the client version sent in the User-Agent header.
var version = "dev"

// TUIProgramOptions are passed to the TUI's Bubble Tea program after its
// defaults. Empty in osync, so the program uses the terminal; tests replace
// it to run the TUI against an in-memory input and output (tea.WithInput,
// tea.WithOutput).
var TUIProgramOptions []tea.ProgramOption

// NewRootCommand returns the root command. invoked is the program name used
// in usage text.
func NewRootCommand(invoked, ver, commit string) *cobra.Command {
	version = ver
	rootCmd := &cobra.Command{
		Use:   invoked,
		Short: "Terminal UI for OmniSync cloud-sync management",
		Long: "A full-featured TUI client for the OmniSync backend API, providing dashboard, profile management, sync control, and more, all from the terminal.\n\n" +
			"Without a subcommand it starts the TUI. The subcommands are for scripts: each takes --json for\n" +
			"machine-readable output and exits with 0 (ok), 1 (error or failure), 2 (wrong arguments or flags),\n" +
			"3 (refused: HTTP 409 or not confirmed) or 4 (not found: HTTP 404).",
		RunE:         runTUI,
		SilenceUsage: true,
	}

	rootCmd.Version = fmt.Sprintf("%s (commit %s)", ver, commit)

	// Global persistent flags, available to all subcommands.
	rootCmd.PersistentFlags().String("url", "", "Backend base URL (default "+config.DefaultURL+"; env OMNISYNC_URL)")
	rootCmd.PersistentFlags().String("api-key", "", "API token sent as 'Authorization: Bearer <token>' (env OMNISYNC_API_KEY)")
	rootCmd.PersistentFlags().Bool("json", false, "Output as JSON (CLI subcommands only)")
	rootCmd.PersistentFlags().Bool("ascii", false, "Use ASCII borders and symbols instead of Unicode (env OMNISYNC_ASCII_MODE)")
	rootCmd.PersistentFlags().String("theme", "", "Colour theme: dark or light (default dark; env OMNISYNC_THEME)")
	rootCmd.PersistentFlags().Bool("no-mouse", false, "Leave the mouse to the terminal: no clickable tabs (env OMNISYNC_NO_MOUSE)")

	rootCmd.AddCommand(statusCmd())
	rootCmd.AddCommand(healthCmd())
	rootCmd.AddCommand(profilesCmd())
	rootCmd.AddCommand(jobsCmd())
	rootCmd.AddCommand(pushCmd())
	rootCmd.AddCommand(pullCmd())
	rootCmd.AddCommand(syncCmd())
	rootCmd.AddCommand(resyncCmd())
	rootCmd.AddCommand(profileCmd())
	rootCmd.AddCommand(conflictsCmd())
	rootCmd.AddCommand(remotesCmd())
	rootCmd.AddCommand(backupsCmd())
	rootCmd.AddCommand(logsCmd())
	rootCmd.AddCommand(notificationsCmd())

	// Server text never reaches the terminal as control sequences
	// (terminalSafeWriter); the TUI writes to the terminal itself.
	rootCmd.PersistentPreRun = func(cmd *cobra.Command, _ []string) {
		root := cmd.Root()
		root.SetOut(newTerminalSafeWriter(root.OutOrStdout()))
		root.SetErr(newTerminalSafeWriter(root.ErrOrStderr()))
	}

	// A wrong flag is a usage error (exit status 2), like wrong arguments.
	rootCmd.SetFlagErrorFunc(func(_ *cobra.Command, err error) error {
		return usageError{err}
	})
	return rootCmd
}

// loadConfig reads the configuration and applies the display settings.
func loadConfig(cmd *cobra.Command) *config.Config {
	cfg, err := config.Load(cmd)
	if err != nil {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: config load failed, using defaults: %v\n", err)
		cfg = &config.Config{URL: config.DefaultURL, Theme: "dark"}
	}
	for _, w := range cfg.Warnings {
		_, _ = fmt.Fprintf(cmd.ErrOrStderr(), "Warning: %s\n", w)
	}
	theme.Set(cfg.Theme)
	theme.SetASCII(cfg.ASCIIMode)
	return cfg
}

func runTUI(cmd *cobra.Command, args []string) error {
	cfg := loadConfig(cmd)

	// Debug logs go to OMNISYNC_LOG_FILE only; never to the terminal, which
	// the TUI owns while it runs.
	closeLog, logErr := logging.Open(cfg.LogFile)
	if logErr != nil {
		return fmt.Errorf("failed to open log file %s: %w", cfg.LogFile, logErr)
	}
	defer func() { _ = closeLog() }()
	logging.L().Info("osync starting", "version", version, "url", cfg.URL)

	client := api.NewClient(cfg.URL, cfg.APIKey, version)
	app := ui.NewApp(client, cfg)

	// Views are value types: the app stores whatever Update returns.
	app.RegisterView(ui.NewDashboardModel(client))
	app.RegisterView(ui.NewProfilesModel(client))
	app.RegisterView(ui.NewProfileDetailModel(client))
	app.RegisterView(ui.NewJobsModel(client))
	app.RegisterView(ui.NewLogsModel(client))
	app.RegisterView(ui.NewConflictsModel(client))
	app.RegisterView(ui.NewRemotesModel(client))
	app.RegisterView(ui.NewWizardModel(client))
	app.RegisterView(ui.NewNotificationsModel(client))
	app.RegisterView(ui.NewConfigModel(client))

	// A panic outside the Bubble Tea program (it catches its own).
	defer func() {
		if r := recover(); r != nil {
			logging.L().Error("panic", "value", fmt.Sprint(r), "stack", string(debug.Stack()))
			_, _ = fmt.Fprint(os.Stderr, logging.FatalMessage(r, cfg.LogFile))
			os.Exit(2)
		}
	}()

	guard := logging.NewPanicGuard(app)
	_, err := tea.NewProgram(guard, TUIProgramOptions...).Run()
	return guard.Report(err, cfg.LogFile, os.Stderr)
}

package cli

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/spf13/cobra"
)

// Exit statuses of the scriptable subcommands.
const (
	// ExitOK: the command did what was asked.
	ExitOK = 0
	// ExitError: anything else went wrong: the backend is unreachable or
	// unhealthy, a sync, backup, restore or test failed, an API error.
	ExitError = 1
	// ExitUsage: wrong arguments or flags; nothing was sent.
	ExitUsage = 2
	// ExitRefused: the backend refused the request in its current state
	// (HTTP 409: already running, paused, already resolved, ...) or the
	// command refused it (an unconfirmed restore or resync, a busy profile,
	// a mirror profile for sync/resync).
	ExitRefused = 3
	// ExitNotFound: the profile, conflict, remote, backup target, manual
	// flag or notification channel does not exist (HTTP 404). For the
	// sync endpoints the backend also answers 404 when the profile is
	// disabled.
	ExitNotFound = 4
)

// usageError marks wrong arguments or flags (exit status 2).
type usageError struct{ err error }

func (e usageError) Error() string { return e.err.Error() }
func (e usageError) Unwrap() error { return e.err }

// usagef returns a usage error (exit status 2).
func usagef(format string, args ...any) error {
	return usageError{fmt.Errorf(format, args...)}
}

// errRefused marks a request the command itself refused (exit status 3).
var errRefused = errors.New("refused")

// refusedf returns an error with exit status 3.
func refusedf(format string, args ...any) error {
	return refusedError{fmt.Errorf(format, args...)}
}

type refusedError struct{ err error }

func (e refusedError) Error() string   { return e.err.Error() }
func (e refusedError) Unwrap() []error { return []error{e.err, errRefused} }

// ExitCode maps the error a command returned to the process exit status
// (see ExitOK ... ExitNotFound).
func ExitCode(err error) int {
	if err == nil {
		return ExitOK
	}
	var u usageError
	if errors.As(err, &u) {
		return ExitUsage
	}
	// Cobra's own errors for an unknown subcommand of the root command.
	msg := err.Error()
	if strings.HasPrefix(msg, "unknown command ") || strings.HasPrefix(msg, "unknown flag") ||
		strings.HasPrefix(msg, "unknown shorthand flag") {
		return ExitUsage
	}
	if errors.Is(err, errRefused) || errors.Is(err, errNotConfirmed) {
		return ExitRefused
	}
	var apiErr *api.ApiError
	if errors.As(err, &apiErr) {
		switch apiErr.StatusCode {
		case http.StatusConflict:
			return ExitRefused
		case http.StatusNotFound:
			return ExitNotFound
		}
	}
	return ExitError
}

// usageArgs makes an argument validator's errors usage errors.
func usageArgs(v cobra.PositionalArgs) cobra.PositionalArgs {
	return func(cmd *cobra.Command, args []string) error {
		if err := v(cmd, args); err != nil {
			return usageError{err}
		}
		return nil
	}
}

// groupCmd is a command that only holds subcommands: without one it prints
// its help, with an unknown one it is a usage error.
func groupCmd(use, short, long string) *cobra.Command {
	return &cobra.Command{
		Use:   use,
		Short: short,
		Long:  long,
		Args:  usageArgs(cobra.NoArgs),
		RunE: func(cmd *cobra.Command, args []string) error {
			return cmd.Help()
		},
	}
}

// completionTimeout bounds the API call behind a shell completion.
const completionTimeout = 3 * time.Second

// completeProfiles completes the first argument with the profile slugs.
func completeProfiles(cmd *cobra.Command, args []string, _ string) ([]string, cobra.ShellCompDirective) {
	if len(args) > 0 {
		return nil, cobra.ShellCompDirectiveNoFileComp
	}
	ctx, cancel := context.WithTimeout(context.Background(), completionTimeout)
	defer cancel()
	profiles, err := newClientFromFlags(cmd).ListProfiles(ctx)
	if err != nil {
		return nil, cobra.ShellCompDirectiveNoFileComp
	}
	out := make([]string, 0, len(profiles))
	for _, p := range profiles {
		out = append(out, p.Slug+"\t"+p.Name)
	}
	return out, cobra.ShellCompDirectiveNoFileComp
}

// completeRemotes completes the first argument with the remote names.
func completeRemotes(cmd *cobra.Command, args []string, _ string) ([]string, cobra.ShellCompDirective) {
	if len(args) > 0 {
		return nil, cobra.ShellCompDirectiveNoFileComp
	}
	ctx, cancel := context.WithTimeout(context.Background(), completionTimeout)
	defer cancel()
	remotes, err := newClientFromFlags(cmd).ListRemotes(ctx)
	if err != nil {
		return nil, cobra.ShellCompDirectiveNoFileComp
	}
	out := make([]string, 0, len(remotes))
	for _, r := range remotes {
		out = append(out, r.Name+"\t"+r.Type)
	}
	return out, cobra.ShellCompDirectiveNoFileComp
}

// fixedCompletion completes a flag from a fixed list.
func fixedCompletion(values ...string) func(*cobra.Command, []string, string) ([]string, cobra.ShellCompDirective) {
	return func(*cobra.Command, []string, string) ([]string, cobra.ShellCompDirective) {
		return values, cobra.ShellCompDirectiveNoFileComp
	}
}

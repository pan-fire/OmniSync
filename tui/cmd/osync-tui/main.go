package main

import (
	"context"
	"os"
	"path/filepath"

	"github.com/pan-fire/OmniSync/tui/internal/cli"
)

// Set via -ldflags at build time.
var (
	version = "dev"
	commit  = "unknown"
)

func main() {
	// Name the command after however it was invoked, so the 'osync' alias
	// installed by `make install` doesn't print osync-tui in its usage text.
	invoked := filepath.Base(os.Args[0])
	if invoked == "" || invoked == "." || invoked == string(os.PathSeparator) {
		invoked = "osync-tui"
	}

	// The exit status tells scripts what went wrong (cli.ExitCode).
	err := cli.NewRootCommand(invoked, version, commit).ExecuteContext(context.Background())
	os.Exit(cli.ExitCode(err))
}

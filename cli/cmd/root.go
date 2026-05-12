// Package cmd implements the fedora-nexus CLI using Cobra + Bubble Tea.
package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/spf13/cobra"
)

// Version is set at build time via -ldflags "-X fedora-nexus/cmd.Version=x.y.z".
// Falls back to "dev" when built without the flag (e.g. `go run`).
var Version = "dev"

var (
	serverFlag string
	jsonFlag   bool
)

var rootCmd = &cobra.Command{
	Use:     "fedora-nexus",
	Short:   "Code dependency graph — index, query, and explore your codebase",
	Version: Version,
	Long: `fedora-nexus analyses code dependencies using a graph database.

The CLI connects to a running fedora-nexus server (Docker container).
Start the server with:  fedora-nexus server-start

Set a custom server URL via --server or FEDORA_NEXUS_SERVER_URL env var.`,
}

// Execute runs the root command. Called by main.
func Execute() {
	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

func init() {
	rootCmd.PersistentFlags().StringVar(&serverFlag, "server", "", "server URL (default: auto-detect localhost:7832)")
	rootCmd.PersistentFlags().BoolVar(&jsonFlag, "json", false, "output raw JSON (no TUI, for agent consumption)")
}

// printJSON marshals v and prints it, exiting on error.
func printJSON(v any) {
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
}

// fatal prints an error message and exits with code 1.
func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "\n"+
		"  \033[1;31m✗\033[0m  "+format+"\n\n", args...)
	os.Exit(1)
}

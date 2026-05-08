package cmd

import (
	"fmt"

	"depgraph/internal/client"
	"depgraph/internal/ui"

	"github.com/spf13/cobra"
)

func init() {
	rootCmd.AddCommand(resetDbCmd)
}

var resetDbCmd = &cobra.Command{
	Use:   "reset-db",
	Short: "Wipe the database and reinitialize it",
	Long:  "Deletes all indexed data and reinitializes the database. Use to recover from corruption or to start fresh. All repos must be re-indexed afterwards.",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		srv := client.ServerURL(serverFlag)

		result := ui.RunWithSpinner(
			"Wiping and reinitializing database",
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "reset_db", map[string]any{})
				return ui.ResultMsg{Data: r.Data, Err: r.Err}
			},
		)

		if result.Err != nil {
			fatal("%v", result.Err)
		}
		if jsonFlag {
			printJSON(result.Data)
			return nil
		}

		if errMsg, ok := result.Data["error"].(string); ok {
			return fmt.Errorf("%s", errMsg)
		}
		fmt.Println()
		fmt.Println(ui.SuccessStyle.Render("  ✓  Database wiped and reinitialized"))
		fmt.Println()
		if msg, ok := result.Data["message"].(string); ok {
			fmt.Println(ui.MutedStyle.Render("     " + msg))
		}
		fmt.Println()
		return nil
	},
}

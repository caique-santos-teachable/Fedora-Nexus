package cmd

import (
	"fmt"

	"fedora-nexus/internal/client"
	"fedora-nexus/internal/ui"

	"github.com/spf13/cobra"
)

func init() {
	rootCmd.AddCommand(deleteCmd)
}

var deleteCmd = &cobra.Command{
	Use:   "delete <root-path>",
	Short: "Remove a repository from the graph database",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		rootPath := absPath(args[0])
		srv := client.ServerURL(serverFlag)

		result := ui.RunWithSpinner(
			fmt.Sprintf("Deleting %s", rootPath),
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "delete_repo", map[string]any{"root_path": rootPath})
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

		fmt.Println()
		fmt.Println(ui.SuccessStyle.Render("  ✓  Repository removed from graph"))
		fmt.Println()
		fmt.Println(ui.Field("Path", rootPath))
		if msg, ok := result.Data["message"].(string); ok {
			fmt.Println(ui.MutedStyle.Render("     " + msg))
		}
		fmt.Println()
		return nil
	},
}

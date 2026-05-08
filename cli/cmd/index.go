package cmd

import (
	"fmt"
	"strings"

	"fedora-nexus/internal/client"
	"fedora-nexus/internal/ui"

	"github.com/spf13/cobra"
)

var forceFlag bool

func init() {
	rootCmd.AddCommand(indexCmd)
	indexCmd.Flags().BoolVarP(&forceFlag, "force", "f", false, "re-index even if already indexed")
}

var indexCmd = &cobra.Command{
	Use:   "index <root-path>",
	Short: "Index a repository into the dependency graph",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		rootPath := absPath(args[0])
		srv := client.ServerURL(serverFlag)

		result := ui.RunWithSpinner(
			fmt.Sprintf("Indexing %s", rootPath),
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "index_repo", map[string]any{
					"root_path":    rootPath,
					"with_symbols": true,
					"force_reindex": forceFlag,
				})
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
		renderIndexResult(result.Data, rootPath)
		return nil
	},
}

func renderIndexResult(data map[string]any, rootPath string) {
	fmt.Println()
	fmt.Println(ui.SuccessStyle.Render("  ✓  Indexed successfully"))
	fmt.Println()

	if msg, ok := data["message"].(string); ok {
		fmt.Println(ui.Field("Message", msg))
	}

	// Node counts
	if nodes, ok := data["nodes"].(float64); ok {
		fmt.Println(ui.Tick("Nodes", fmt.Sprintf("%.0f", nodes)))
	}
	if edges, ok := data["edges"].(float64); ok {
		fmt.Println(ui.Field("Edges", fmt.Sprintf("%.0f", edges)))
	}

	// Language breakdown
	if langs, ok := data["languages"].(map[string]any); ok && len(langs) > 0 {
		parts := make([]string, 0, len(langs))
		for lang, count := range langs {
			parts = append(parts, fmt.Sprintf("%s (%v)", lang, count))
		}
		fmt.Println(ui.Field("Languages", strings.Join(parts, "  ")))
	}

	if root, ok := data["root_path"].(string); ok {
		fmt.Println(ui.Field("Path", root))
	}

	fmt.Println()
}

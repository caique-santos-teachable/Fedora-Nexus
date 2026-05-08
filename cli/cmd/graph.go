package cmd

import (
	"fmt"

	"fedora-nexus/internal/client"
	"fedora-nexus/internal/ui"

	"github.com/spf13/cobra"
)

func init() {
	rootCmd.AddCommand(graphCmd)
}

var graphCmd = &cobra.Command{
	Use:   "graph <root-path>",
	Short: "Export the full dependency graph as JSON",
	Args:  cobra.ExactArgs(1),
	RunE: func(cmd *cobra.Command, args []string) error {
		rootPath := absPath(args[0])
		srv := client.ServerURL(serverFlag)

		result := ui.RunWithSpinner(
			"Loading graph",
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "get_graph", map[string]any{"root_path": rootPath})
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
		renderGraphSummary(result.Data, rootPath)
		return nil
	},
}

func renderGraphSummary(data map[string]any, rootPath string) {
	fmt.Println()
	fmt.Println("  " + ui.TitleStyle.Render("Graph Summary"))
	fmt.Println()
	fmt.Println(ui.Field("Root", rootPath))

	if nodes, ok := data["nodes"].([]any); ok {
		fmt.Println(ui.Tick("Nodes", fmt.Sprintf("%d", len(nodes))))
	} else if count, ok := data["node_count"].(float64); ok {
		fmt.Println(ui.Tick("Nodes", fmt.Sprintf("%.0f", count)))
	}

	if edges, ok := data["edges"].([]any); ok {
		fmt.Println(ui.Field("Edges", fmt.Sprintf("%d", len(edges))))
	} else if count, ok := data["edge_count"].(float64); ok {
		fmt.Println(ui.Field("Edges", fmt.Sprintf("%.0f", count)))
	}

	fmt.Println()
	fmt.Println("  " + ui.MutedStyle.Render("Use --json to export the full graph data."))
	fmt.Println()
}

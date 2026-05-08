package cmd

import (
	"fmt"
	"strings"

	"depgraph/internal/client"
	"depgraph/internal/ui"

	"github.com/spf13/cobra"
)

func init() {
	rootCmd.AddCommand(queryCmd)
}

var queryCmd = &cobra.Command{
	Use:   "query <root-path> <cypher>",
	Short: "Run a Cypher query against the dependency graph",
	Args:  cobra.ExactArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		rootPath, cypher := absPath(args[0]), args[1]
		srv := client.ServerURL(serverFlag)

		result := ui.RunWithSpinner(
			"Running Cypher query",
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "query_graph", map[string]any{
					"root_path": rootPath,
					"cypher":    cypher,
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
		renderQueryResult(result.Data, cypher)
		return nil
	},
}

func renderQueryResult(data map[string]any, cypher string) {
	fmt.Println()

	rows, _ := data["rows"].([]any)
	if len(rows) == 0 {
		fmt.Println("  " + ui.MutedStyle.Render("Query returned no results."))
		fmt.Println()
		return
	}

	fmt.Println("  " + ui.TitleStyle.Render(fmt.Sprintf("Query Results: %d row(s)", len(rows))))
	fmt.Println("  " + ui.MutedStyle.Render(fmt.Sprintf("╷ %s", truncate(cypher, 72))))
	fmt.Println()

	// Detect columns from first row.
	var columns []string
	if first, ok := rows[0].(map[string]any); ok {
		for k := range first {
			columns = append(columns, k)
		}
	}

	if len(columns) > 0 {
		// Header row
		header := ""
		for _, col := range columns {
			header += ui.LabelStyle.Render(padRight(col, 30)) + "  "
		}
		fmt.Println("  " + strings.TrimRight(header, " "))
		fmt.Println("  " + ui.MutedStyle.Render(strings.Repeat("─", 64)))

		for _, row := range rows {
			r, ok := row.(map[string]any)
			if !ok {
				fmt.Println("  " + ui.ValueStyle.Render(fmt.Sprintf("%v", row)))
				continue
			}
			line := ""
			for _, col := range columns {
				line += ui.ValueStyle.Render(padRight(fmt.Sprintf("%v", r[col]), 30)) + "  "
			}
			fmt.Println("  " + strings.TrimRight(line, " "))
		}
	} else {
		for _, row := range rows {
			fmt.Println("  " + ui.ValueStyle.Render(fmt.Sprintf("%v", row)))
		}
	}
	fmt.Println()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n-3] + "..."
}

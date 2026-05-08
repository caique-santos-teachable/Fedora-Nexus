package cmd

import (
	"fmt"
	"strings"

	"fedora-nexus/internal/client"
	"fedora-nexus/internal/ui"

	"github.com/spf13/cobra"
)

var searchLimitFlag int

func init() {
	rootCmd.AddCommand(searchCmd)
	searchCmd.Flags().IntVar(&searchLimitFlag, "limit", 20, "maximum number of results")
}

var searchCmd = &cobra.Command{
	Use:   "search <root-path> <query>",
	Short: "Search for symbols by name or content",
	Args:  cobra.ExactArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		rootPath, query := absPath(args[0]), args[1]
		srv := client.ServerURL(serverFlag)

		result := ui.RunWithSpinner(
			fmt.Sprintf("Searching for %q", query),
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "search", map[string]any{
					"root_path": rootPath,
					"query":     query,
					"limit":     searchLimitFlag,
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
		renderSearchResult(result.Data, query)
		return nil
	},
}

func renderSearchResult(data map[string]any, query string) {
	results, _ := data["results"].([]any)
	fmt.Println()

	if len(results) == 0 {
		fmt.Printf("  %s\n\n", ui.MutedStyle.Render(fmt.Sprintf("No results for %q", query)))
		return
	}

	title := ui.TitleStyle.Render(fmt.Sprintf("Search Results: %d matches for %q", len(results), query))
	fmt.Println("  " + title)
	fmt.Println()

	for i, r := range results {
		item, ok := r.(map[string]any)
		if !ok {
			continue
		}
		name, _ := item["name"].(string)
		kind, _ := item["kind"].(string)
		filePath, _ := item["file_path"].(string)
		line, _ := item["line"].(float64)

		num := ui.MutedStyle.Render(fmt.Sprintf("%3d.", i+1))
		nameStr := ui.LabelStyle.Render(padRight(name, 30))
		kindStr := ui.MutedStyle.Render(padRight(kind, 10))
		loc := ui.ValueStyle.Render(filePath)
		if line > 0 {
			loc += ui.MutedStyle.Render(fmt.Sprintf(":%d", int(line)))
		}
		fmt.Printf("  %s %s %s %s\n", num, nameStr, kindStr, loc)
	}
	fmt.Println()
}

func padRight(s string, n int) string {
	if len(s) >= n {
		return s[:n]
	}
	return s + strings.Repeat(" ", n-len(s))
}

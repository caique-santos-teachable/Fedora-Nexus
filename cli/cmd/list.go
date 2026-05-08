package cmd

import (
	"fmt"
	"strings"
	"time"

	"depgraph/internal/client"
	"depgraph/internal/ui"

	"github.com/spf13/cobra"
)

func init() {
	rootCmd.AddCommand(listCmd)
}

var listCmd = &cobra.Command{
	Use:   "list",
	Short: "List all indexed repositories",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		srv := client.ServerURL(serverFlag)

		result := ui.RunWithSpinner(
			"Loading indexed repositories",
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "list_repos", map[string]any{})
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
		renderListResult(result.Data)
		return nil
	},
}

func renderListResult(data map[string]any) {
	repos, _ := data["repos"].([]any)
	fmt.Println()

	if len(repos) == 0 {
		fmt.Println(ui.MutedStyle.Render("  No repositories indexed yet."))
		fmt.Println()
		fmt.Println(ui.MutedStyle.Render("  Get started:"))
		fmt.Println(ui.ValueStyle.Render("    depgraph index /path/to/your/repo"))
		fmt.Println()
		return
	}

	title := ui.TitleStyle.Render(fmt.Sprintf("Indexed Repositories (%d)", len(repos)))
	fmt.Println("  " + title)
	fmt.Println()

	for _, r := range repos {
		repo, ok := r.(map[string]any)
		if !ok {
			continue
		}
		renderRepoCard(repo)
	}
}

func renderRepoCard(repo map[string]any) {
	rootPath, _ := repo["root_path"].(string)
	nodes, _ := repo["nodes"].(float64)
	edges, _ := repo["edges"].(float64)

	lines := []string{
		ui.LabelStyle.Render(rootPath),
		ui.MutedStyle.Render(fmt.Sprintf("Nodes: %.0f  Edges: %.0f", nodes, edges)),
	}

	// Language summary
	if langs, ok := repo["languages"].(map[string]any); ok && len(langs) > 0 {
		parts := make([]string, 0, len(langs))
		for lang, count := range langs {
			parts = append(parts, fmt.Sprintf("%s (%v)", lang, count))
		}
		lines = append(lines, ui.MutedStyle.Render("Languages: "+strings.Join(parts, "  ")))
	}

	// Indexed-at timestamp
	if ts, ok := repo["indexed_at"].(string); ok && ts != "" {
		if t, err := time.Parse(time.RFC3339, ts); err == nil {
			lines = append(lines, ui.MutedStyle.Render("Indexed: "+t.Local().Format("2006-01-02 15:04")))
		} else {
			lines = append(lines, ui.MutedStyle.Render("Indexed: "+ts))
		}
	}

	box := ui.BoxStyle.Render(strings.Join(lines, "\n"))
	fmt.Println("  " + strings.ReplaceAll(box, "\n", "\n  "))
	fmt.Println()
}

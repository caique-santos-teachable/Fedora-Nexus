package cmd

import (
	"fmt"
	"sort"
	"strconv"

	"fedora-nexus/internal/client"
	"fedora-nexus/internal/ui"

	"github.com/spf13/cobra"
)

var blastMaxDepthFlag int

func init() {
	rootCmd.AddCommand(blastCmd)
	blastCmd.Flags().IntVar(&blastMaxDepthFlag, "max-depth", 10, "maximum traversal depth")
}

var blastCmd = &cobra.Command{
	Use:   "blast-radius <root-path> <file> [file...]",
	Short: "Show which files are affected by changes to the given files",
	Args:  cobra.MinimumNArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		rootPath := absPath(args[0])
		changedFiles := args[1:]
		srv := client.ServerURL(serverFlag)

		result := ui.RunWithSpinner(
			"Computing blast radius",
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "blast_radius", map[string]any{
					"root_path":     rootPath,
					"changed_files": changedFiles,
					"max_depth":     blastMaxDepthFlag,
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
		renderBlastResult(result.Data, changedFiles)
		return nil
	},
}

func renderBlastResult(data map[string]any, changedFiles []string) {
	fmt.Println()

	// Changed files header
	fmt.Println("  " + ui.TitleStyle.Render("Changed Files"))
	for _, f := range changedFiles {
		fmt.Println("  " + ui.WarnStyle.Render("  ▶ ") + ui.ValueStyle.Render(f))
	}
	fmt.Println()

	affected, _ := data["affected_files"].([]any)
	total := len(affected)

	if total == 0 {
		fmt.Println("  " + ui.SuccessStyle.Render("No files affected — isolated change."))
		fmt.Println()
		return
	}

	fmt.Println("  " + ui.TitleStyle.Render(fmt.Sprintf("Blast Radius: %d file(s) affected", total)))
	fmt.Println()

	// Group by depth if available.
	byDepth := make(map[int][]string)
	for _, f := range affected {
		item, ok := f.(map[string]any)
		if !ok {
			// scalar string
			byDepth[1] = append(byDepth[1], fmt.Sprintf("%v", f))
			continue
		}
		depth := 1
		if d, ok := item["depth"].(float64); ok {
			depth = int(d)
		}
		filePath, _ := item["file_path"].(string)
		if filePath == "" {
			filePath = fmt.Sprintf("%v", item)
		}
		byDepth[depth] = append(byDepth[depth], filePath)
	}

	depths := make([]int, 0, len(byDepth))
	for d := range byDepth {
		depths = append(depths, d)
	}
	sort.Ints(depths)

	for _, depth := range depths {
		files := byDepth[depth]
		depthLabel := ui.DepthStyle.Render("depth " + strconv.Itoa(depth))
		divider := ui.MutedStyle.Render(" ─────────────────────────────────")
		fmt.Println("  " + depthLabel + divider)
		for _, f := range files {
			fmt.Println("    " + ui.ValueStyle.Render(f))
		}
		fmt.Println()
	}
}

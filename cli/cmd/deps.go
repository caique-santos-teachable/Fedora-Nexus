package cmd

import (
	"fmt"

	"fedora-nexus/internal/client"
	"fedora-nexus/internal/ui"

	"github.com/spf13/cobra"
)

var depthFlag int

func init() {
	depsCmd.Flags().IntVar(&depthFlag, "depth", 1, "traversal depth")
	dependentsCmd.Flags().IntVar(&depthFlag, "depth", 1, "traversal depth")
	rootCmd.AddCommand(depsCmd)
	rootCmd.AddCommand(dependentsCmd)
}

var depsCmd = &cobra.Command{
	Use:   "deps <root-path> <file-path>",
	Short: "Show what a file imports (its dependencies)",
	Args:  cobra.ExactArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		rootPath, filePath := absPath(args[0]), args[1]
		srv := client.ServerURL(serverFlag)
		depth, _ := cmd.Flags().GetInt("depth")

		result := ui.RunWithSpinner(
			fmt.Sprintf("Loading dependencies for %s", filePath),
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "get_dependencies", map[string]any{
					"root_path": rootPath,
					"file_path": filePath,
					"depth":     depth,
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
		renderFileList(result.Data, filePath, "Dependencies", "imports", "No dependencies found.")
		return nil
	},
}

var dependentsCmd = &cobra.Command{
	Use:   "dependents <root-path> <file-path>",
	Short: "Show what files import a given file (its dependents)",
	Args:  cobra.ExactArgs(2),
	RunE: func(cmd *cobra.Command, args []string) error {
		rootPath, filePath := absPath(args[0]), args[1]
		srv := client.ServerURL(serverFlag)
		depth, _ := cmd.Flags().GetInt("depth")

		result := ui.RunWithSpinner(
			fmt.Sprintf("Loading dependents for %s", filePath),
			jsonFlag,
			func() ui.ResultMsg {
				r := client.Call(srv, "get_dependents", map[string]any{
					"root_path": rootPath,
					"file_path": filePath,
					"depth":     depth,
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
		renderFileList(result.Data, filePath, "Dependents", "imported by", "No dependents found — this file is a leaf.")
		return nil
	},
}

func renderFileList(data map[string]any, target, title, relation, emptyMsg string) {
	fmt.Println()

	// Find the file list — servers may return "dependencies" or "dependents" or "files"
	var files []any
	for _, key := range []string{"dependencies", "dependents", "files"} {
		if v, ok := data[key].([]any); ok {
			files = v
			break
		}
	}

	header := ui.TitleStyle.Render(title) + ui.MutedStyle.Render(fmt.Sprintf(" of %s", target))
	fmt.Println("  " + header)
	fmt.Println()

	if len(files) == 0 {
		fmt.Println("  " + ui.MutedStyle.Render(emptyMsg))
		fmt.Println()
		return
	}

	fmt.Println("  " + ui.MutedStyle.Render(fmt.Sprintf("%d file(s) %s this file:", len(files), relation)))
	fmt.Println()

	for _, f := range files {
		switch v := f.(type) {
		case string:
			fmt.Println("    " + ui.ValueStyle.Render(v))
		case map[string]any:
			fp, _ := v["file_path"].(string)
			if fp == "" {
				fp = fmt.Sprintf("%v", v)
			}
			fmt.Println("    " + ui.ValueStyle.Render(fp))
		}
	}
	fmt.Println()
}

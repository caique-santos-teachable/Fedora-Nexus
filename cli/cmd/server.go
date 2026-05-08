package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"depgraph/internal/client"
	"depgraph/internal/ui"

	"github.com/spf13/cobra"
)

func init() {
	rootCmd.AddCommand(serverStartCmd)
	rootCmd.AddCommand(serverStopCmd)
	rootCmd.AddCommand(serverRemoveCmd)
	rootCmd.AddCommand(serverStatusCmd)
}

// dataDir returns the depgraph data directory (~/.local/share/depgraph).
func dataDir() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".local", "share", "depgraph")
}

// composeFile resolves the depgraph docker-compose.yml path.
// Search order:
//  1. DEPGRAPH_COMPOSE_FILE env var (explicit override)
//  2. ~/.local/share/depgraph/docker-compose.yml (installed by setup.sh)
//  3. Current working directory walking up (works when run from the repo)
func composeFile() (string, error) {
	// 1. Explicit override.
	if from := os.Getenv("DEPGRAPH_COMPOSE_FILE"); from != "" {
		if _, err := os.Stat(from); err == nil {
			return from, nil
		}
		return "", fmt.Errorf("DEPGRAPH_COMPOSE_FILE=%s: file not found", from)
	}

	// 2. Known data directory (populated by setup.sh).
	known := filepath.Join(dataDir(), "docker-compose.yml")
	if _, err := os.Stat(known); err == nil {
		return known, nil
	}

	// 3. Walk up from the current working directory.
	cwd, _ := os.Getwd()
	dir := cwd
	for i := 0; i < 8; i++ {
		candidate := filepath.Join(dir, "docker-compose.yml")
		if _, err := os.Stat(candidate); err == nil {
			return candidate, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}

	return "", fmt.Errorf(
		"docker-compose.yml not found.\n"+
			"  Tip: re-run setup.sh to install it, or set DEPGRAPH_COMPOSE_FILE=/path/to/docker-compose.yml",
	)
}

// dockerCompose runs docker compose with the depgraph compose file and streams output.
func dockerCompose(args ...string) error {
	cf, err := composeFile()
	if err != nil {
		return err
	}
	fullArgs := append([]string{"compose", "-f", cf}, args...)
	cmd := exec.Command("docker", fullArgs...) //nolint:gosec
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

var serverStartCmd = &cobra.Command{
	Use:   "server-start",
	Short: "Start the depgraph server container",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Println()
		fmt.Println("  " + ui.TitleStyle.Render("Starting depgraph server..."))
		fmt.Println()

		if err := dockerCompose("up", "-d", "mcp-server"); err != nil {
			return fmt.Errorf("docker compose failed: %w", err)
		}

		// Wait until healthy.
		fmt.Print("\n  " + ui.MutedStyle.Render("Waiting for server to become ready"))
		srv := client.ServerURL("")
		if srv == "" {
			srv = "http://localhost:7832"
		}
		for i := 0; i < 30; i++ {
			if client.IsHealthy(srv) {
				fmt.Println()
				fmt.Println()
				fmt.Println(ui.SuccessStyle.Render("  ✓  Server is ready at ") + ui.ValueStyle.Render(srv))
				fmt.Println()
				return nil
			}
			fmt.Print(".")
			time.Sleep(1 * time.Second)
		}
		fmt.Println()
		fmt.Println()
		fmt.Println(ui.WarnStyle.Render("  !  Server started but health check timed out."))
		fmt.Println(ui.MutedStyle.Render("     Check status with: depgraph server-status"))
		fmt.Println()
		return nil
	},
}

var serverStopCmd = &cobra.Command{
	Use:   "server-stop",
	Short: "Stop the depgraph server container",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Println()
		fmt.Println("  " + ui.TitleStyle.Render("Stopping depgraph server..."))
		fmt.Println()

		if err := dockerCompose("stop", "mcp-server"); err != nil {
			return fmt.Errorf("docker compose failed: %w", err)
		}
		fmt.Println()
		fmt.Println(ui.SuccessStyle.Render("  ✓  Server stopped"))
		fmt.Println()
		return nil
	},
}

var serverRemoveCmd = &cobra.Command{
	Use:   "server-remove",
	Short: "Stop and remove the depgraph server container and volumes",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Println()
		fmt.Println("  " + ui.TitleStyle.Render("Removing depgraph server..."))
		fmt.Println()

		if err := dockerCompose("down", "--remove-orphans"); err != nil {
			return fmt.Errorf("docker compose failed: %w", err)
		}
		fmt.Println()
		fmt.Println(ui.SuccessStyle.Render("  ✓  Server removed"))
		fmt.Println()
		return nil
	},
}

var serverStatusCmd = &cobra.Command{
	Use:   "server-status",
	Short: "Show depgraph server container status",
	Args:  cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		fmt.Println()
		fmt.Println("  " + ui.TitleStyle.Render("Server Status"))
		fmt.Println()

		// Docker container status
		cf, err := composeFile()
		if err == nil {
			c := exec.Command("docker", "compose", "-f", cf, "ps", "--format", "table") //nolint:gosec
			c.Stdout = os.Stdout
			c.Stderr = os.Stderr
			_ = c.Run()
			fmt.Println()
		}

		// HTTP health check
		srv := client.ServerURL(serverFlag)
		if srv != "" && client.IsHealthy(srv) {
			fmt.Println(ui.Tick("HTTP health", srv+" → OK"))
		} else {
			target := srv
			if target == "" {
				target = "localhost:7832"
			}
			fmt.Println(ui.ErrorStyle.Render("  ✗") + "  " + ui.LabelStyle.Render("HTTP health:") +
				" " + ui.MutedStyle.Render(target+" → unreachable"))
		}

		// Show HOST_REPOS_PREFIX if set (useful for multi-mount Docker setups)
		if prefix := os.Getenv("HOST_REPOS_PREFIX"); prefix != "" {
			fmt.Println(ui.Field("HOST_REPOS_PREFIX", prefix))
		}

		fmt.Println()
		fmt.Println("  " + ui.MutedStyle.Render("OS: "+runtime.GOOS+"  Arch: "+runtime.GOARCH+"  depgraph CLI "+cliVersion()))
		fmt.Println()
		return nil
	},
}

func cliVersion() string {
	// Read from embedded version or environment; placeholder for now.
	if v := os.Getenv("DEPGRAPH_VERSION"); v != "" {
		return v
	}
	return "dev"
}

// hostToContainerPath translates a host path to a container-mounted path using
// HOST_REPOS_PREFIX / CONTAINER_REPOS_PATH env vars (set in docker-compose.yml).
// Returns rootPath unchanged if env vars are not set.
func hostToContainerPath(rootPath string) string {
	hostPrefix := os.Getenv("HOST_REPOS_PREFIX")
	containerPath := os.Getenv("CONTAINER_REPOS_PATH")
	if hostPrefix == "" || containerPath == "" {
		return rootPath
	}
	if strings.HasPrefix(rootPath, hostPrefix) {
		return containerPath + rootPath[len(hostPrefix):]
	}
	return rootPath
}

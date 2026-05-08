// Package client provides an HTTP client for the depgraph server.
package client

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"
	"time"
)

const defaultURL = "http://localhost:7832"

// ServerURL returns the depgraph server URL to use.
// Priority: flag override > DEPGRAPH_SERVER_URL env > auto-detect localhost:7832.
// Returns empty string if no server is reachable.
func ServerURL(override string) string {
	if override != "" {
		return strings.TrimRight(override, "/")
	}
	if from := os.Getenv("DEPGRAPH_SERVER_URL"); from != "" {
		return strings.TrimRight(from, "/")
	}
	// Probe TCP to avoid a full HTTP roundtrip on failure.
	conn, err := net.DialTimeout("tcp", "localhost:7832", 500*time.Millisecond)
	if err == nil {
		conn.Close()
		return defaultURL
	}
	return ""
}

// Result is the parsed response from a /call endpoint.
type Result struct {
	Data map[string]any
	Err  error
}

// Call invokes a depgraph tool via the server's /call endpoint.
func Call(serverURL, tool string, args map[string]any) Result {
	if serverURL == "" {
		return Result{Err: fmt.Errorf("no server reachable — start it with: depgraph server-start")}
	}
	payload, err := json.Marshal(map[string]any{"tool": tool, "args": args})
	if err != nil {
		return Result{Err: err}
	}
	resp, err := http.Post(serverURL+"/call", "application/json", bytes.NewReader(payload)) //nolint:noctx
	if err != nil {
		return Result{Err: fmt.Errorf("server unreachable: %w", err)}
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return Result{Err: err}
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return Result{Err: fmt.Errorf("invalid server response: %w", err)}
	}
	if errMsg, ok := data["error"].(string); ok {
		return Result{Err: fmt.Errorf("%s", errMsg), Data: data}
	}
	return Result{Data: data}
}

// IsHealthy returns true if the server at serverURL responds to /health.
func IsHealthy(serverURL string) bool {
	resp, err := http.Get(serverURL + "/health") //nolint:noctx
	if err != nil {
		return false
	}
	resp.Body.Close()
	return resp.StatusCode == 200
}

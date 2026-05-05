package ui

import (
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"
)

// ResultMsg carries the result of an async server call.
type ResultMsg struct {
	Data map[string]any
	Err  error
}

// spinnerFrames is the animation sequence for the spinner.
var spinnerFrames = []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

// RunWithSpinner runs fn in the background while showing an animated spinner
// printed to stderr. Exits cleanly when fn returns.
// In JSON mode (jsonMode=true), the spinner is skipped and fn is called directly.
func RunWithSpinner(title string, jsonMode bool, fn func() ResultMsg) ResultMsg {
	if jsonMode {
		return fn()
	}

	done := make(chan ResultMsg, 1)
	go func() { done <- fn() }()

	// Handle Ctrl+C: cancel gracefully.
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(sig)

	start := time.Now()
	tick := time.NewTicker(100 * time.Millisecond)
	defer tick.Stop()

	frame := 0
	fmt.Fprintln(os.Stderr)
	for {
		select {
		case result := <-done:
			// Clear the spinner line.
			fmt.Fprintf(os.Stderr, "\r\033[2K")
			return result
		case <-sig:
			fmt.Fprintf(os.Stderr, "\r\033[2K")
			return ResultMsg{Err: fmt.Errorf("interrupted")}
		case <-tick.C:
			elapsed := time.Since(start)
			fmt.Fprintf(os.Stderr, "\r  %s %s %s",
				spinnerFrames[frame%len(spinnerFrames)],
				title,
				MutedStyle.Render(fmt.Sprintf("(%.1fs)", elapsed.Seconds())),
			)
			frame++
		}
	}
}


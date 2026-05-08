package ui

import (
	"fmt"
	"time"

	"github.com/charmbracelet/bubbles/spinner"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// ResultMsg carries the result of an async server call.
// It also implements tea.Msg so it can be sent to the Bubble Tea program.
type ResultMsg struct {
	Data map[string]any
	Err  error
}

// tickMsg is sent by the elapsed-time ticker.
type tickMsg time.Time

func elapsedTick() tea.Cmd {
	return tea.Tick(100*time.Millisecond, func(t time.Time) tea.Msg {
		return tickMsg(t)
	})
}

// SpinnerModel is a reusable Bubble Tea model that shows an animated spinner
// while waiting for an async operation, then quits when the result arrives.
type SpinnerModel struct {
	spinner spinner.Model
	title   string
	result  *ResultMsg
	start   time.Time
	elapsed time.Duration
	fetchFn func() ResultMsg
}

// NewSpinnerModel creates a SpinnerModel that calls fetchFn in the background.
func NewSpinnerModel(title string, fetchFn func() ResultMsg) SpinnerModel {
	s := spinner.New()
	s.Spinner = spinner.MiniDot
	s.Style = lipgloss.NewStyle().Foreground(ColorPrimary)
	return SpinnerModel{
		spinner: s,
		title:   title,
		start:   time.Now(),
		fetchFn: fetchFn,
	}
}

func (m SpinnerModel) Init() tea.Cmd {
	fn := m.fetchFn
	fetch := func() tea.Msg { return fn() }
	return tea.Batch(m.spinner.Tick, elapsedTick(), fetch)
}

func (m SpinnerModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.KeyMsg:
		if msg.String() == "ctrl+c" {
			return m, tea.Quit
		}
	case ResultMsg:
		m.result = &msg
		m.elapsed = time.Since(m.start)
		return m, tea.Quit
	case tickMsg:
		m.elapsed = time.Since(m.start)
		return m, elapsedTick()
	case spinner.TickMsg:
		var cmd tea.Cmd
		m.spinner, cmd = m.spinner.Update(msg)
		return m, cmd
	}
	return m, nil
}

func (m SpinnerModel) View() string {
	if m.result != nil {
		return "" // clear the spinner line when done
	}
	elapsed := MutedStyle.Render(fmt.Sprintf("(%.1fs)", m.elapsed.Seconds()))
	return "\n  " + m.spinner.View() + " " + TitleStyle.Render(m.title) + " " + elapsed + "\n"
}

// RunWithSpinner runs fn in the background while showing an animated spinner.
// In JSON mode (jsonMode=true), the spinner is skipped and fn is called directly.
// Returns the result from fn.
func RunWithSpinner(title string, jsonMode bool, fn func() ResultMsg) ResultMsg {
	if jsonMode {
		return fn()
	}
	m := NewSpinnerModel(title, fn)
	p := tea.NewProgram(m)
	finalModel, err := p.Run()
	if err != nil {
		return ResultMsg{Err: fmt.Errorf("TUI error: %w", err)}
	}
	sm, ok := finalModel.(SpinnerModel)
	if !ok || sm.result == nil {
		return ResultMsg{Err: fmt.Errorf("interrupted")}
	}
	return *sm.result
}

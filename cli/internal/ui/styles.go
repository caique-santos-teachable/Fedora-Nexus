// Package ui provides shared Bubble Tea TUI utilities and lipgloss styles.
package ui

import "github.com/charmbracelet/lipgloss"

// Colours.
var (
	ColorPrimary = lipgloss.Color("#7C3AED") // violet
	ColorSuccess = lipgloss.Color("#10B981") // emerald
	ColorWarning = lipgloss.Color("#F59E0B") // amber
	ColorError   = lipgloss.Color("#EF4444") // red
	ColorMuted   = lipgloss.Color("#6B7280") // gray-500
	ColorLabel   = lipgloss.Color("#C4B5FD") // violet-300
	ColorValue   = lipgloss.Color("#E5E7EB") // gray-200
	ColorAccent  = lipgloss.Color("#8B5CF6") // violet-500
	ColorBorder  = lipgloss.Color("#4C1D95") // violet-900
)

// Styles.
var (
	TitleStyle = lipgloss.NewStyle().Bold(true).Foreground(ColorPrimary)
	LabelStyle = lipgloss.NewStyle().Foreground(ColorLabel).Bold(true)
	ValueStyle = lipgloss.NewStyle().Foreground(ColorValue)
	MutedStyle = lipgloss.NewStyle().Foreground(ColorMuted)

	SuccessStyle = lipgloss.NewStyle().Foreground(ColorSuccess).Bold(true)
	ErrorStyle   = lipgloss.NewStyle().Foreground(ColorError).Bold(true)
	WarnStyle    = lipgloss.NewStyle().Foreground(ColorWarning).Bold(true)

	BoxStyle = lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(ColorBorder).
			Padding(0, 1)

	HighlightStyle = lipgloss.NewStyle().
			Background(lipgloss.Color("#1E1B4B")).
			Foreground(ColorLabel).
			Padding(0, 1)

	DepthStyle = lipgloss.NewStyle().
			Foreground(ColorAccent).
			Bold(true)
)

// Field renders a label: value pair.
func Field(label, value string) string {
	return "  " + LabelStyle.Render(label+":") + " " + ValueStyle.Render(value)
}

// Tick renders a success checkmark with label and value.
func Tick(label, value string) string {
	return SuccessStyle.Render("  ✓") + "  " + LabelStyle.Render(label+":") + " " + ValueStyle.Render(value)
}

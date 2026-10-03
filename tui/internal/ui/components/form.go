package components

import (
	"strings"

	"charm.land/bubbles/v2/textinput"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// FieldType specifies the type of form field.
type FieldType int

const (
	FieldText FieldType = iota
	FieldPassword
	FieldDropdown
)

// Field represents a single form field. Value holds the initial value when
// the form is built and the current value afterwards.
type Field struct {
	Name     string
	Label    string
	Type     FieldType
	Value    string
	Required bool
	Help     string
	Options  []string // for dropdown
	optIdx   int      // current option index for dropdown
	input    textinput.Model
}

// FormSubmitMsg is sent when the form is submitted.
type FormSubmitMsg struct {
	FormID string
	Values map[string]string
}

// FormCancelMsg is sent when the form is cancelled.
type FormCancelMsg struct {
	FormID string
}

// Form is an interactive form. Text and password fields are bubbles
// textinputs, so they take any printable input: spaces, digits, 'q' and
// non-ASCII text such as umlauts.
//
// Keys: Tab/Down next field, Shift+Tab/Up previous field, Left/Right change
// a dropdown, Enter submits, Esc cancels.
type Form struct {
	ID       string // identifies the form in FormSubmitMsg/FormCancelMsg
	Title    string
	Intro    string // optional text shown above the fields
	Fields   []Field
	FocusIdx int
	Active   bool
	Error    string
}

// NewForm creates a new, active form with the first field focused.
func NewForm(title string, fields []Field) Form {
	for i := range fields {
		f := &fields[i]
		if f.Type == FieldDropdown {
			for j, opt := range f.Options {
				if opt == f.Value {
					f.optIdx = j
					break
				}
			}
			if f.Value == "" && len(f.Options) > 0 {
				f.Value = f.Options[0]
			}
			continue
		}
		ti := textinput.New()
		ti.Prompt = ""
		ti.CharLimit = 1024
		ti.SetWidth(48)
		styles := textinput.DefaultDarkStyles()
		styles.Cursor.Blink = false
		ti.SetStyles(styles)
		if f.Type == FieldPassword {
			ti.EchoMode = textinput.EchoPassword
			ti.EchoCharacter = '*'
		}
		ti.SetValue(f.Value)
		f.input = ti
	}
	form := Form{Title: title, Fields: fields, Active: true}
	form.focus(0)
	return form
}

// NewFormWithID creates a form whose submit and cancel messages carry id.
func NewFormWithID(id, title string, fields []Field) Form {
	f := NewForm(title, fields)
	f.ID = id
	return f
}

func (f *Form) focus(idx int) {
	if len(f.Fields) == 0 {
		return
	}
	for i := range f.Fields {
		if f.Fields[i].Type != FieldDropdown {
			f.Fields[i].input.Blur()
		}
	}
	f.FocusIdx = idx
	if fld := &f.Fields[idx]; fld.Type != FieldDropdown {
		_ = fld.input.Focus()
	}
}

// FocusedField returns the name of the focused field, or "".
func (f *Form) FocusedField() string {
	if f.FocusIdx < 0 || f.FocusIdx >= len(f.Fields) {
		return ""
	}
	return f.Fields[f.FocusIdx].Name
}

// Value returns the current value of the named field, or "".
func (f *Form) Value(name string) string {
	for _, field := range f.Fields {
		if field.Name == name {
			return field.Value
		}
	}
	return ""
}

// SetValue replaces the value of the named text field, e.g. with a folder a
// picker returned.
func (f *Form) SetValue(name, value string) {
	for i := range f.Fields {
		fld := &f.Fields[i]
		if fld.Name != name || fld.Type == FieldDropdown {
			continue
		}
		fld.Value = value
		fld.input.SetValue(value)
		fld.input.CursorEnd()
	}
}

// Reopen makes a submitted form active again with an error message, keeping
// every value the user entered (for example after the backend refused it).
func (f *Form) Reopen(errMsg string) {
	f.Active = true
	f.Error = errMsg
	f.focus(f.FocusIdx)
}

// Values returns the current field values.
func (f *Form) Values() map[string]string {
	vals := make(map[string]string, len(f.Fields))
	for _, field := range f.Fields {
		vals[field.Name] = field.Value
	}
	return vals
}

// Validate checks required fields and returns an error message or "".
func (f *Form) Validate() string {
	for _, field := range f.Fields {
		if field.Required && strings.TrimSpace(field.Value) == "" {
			return field.Label + " is required"
		}
	}
	return ""
}

// Update handles input while the form is active. It reports whether the
// message was consumed; an active form consumes every key.
func (f *Form) Update(msg tea.Msg) (bool, tea.Cmd) {
	if !f.Active || len(f.Fields) == 0 {
		return false, nil
	}

	field := &f.Fields[f.FocusIdx]

	switch msg := msg.(type) {
	case tea.PasteMsg:
		if field.Type == FieldDropdown {
			return true, nil
		}
		var cmd tea.Cmd
		field.input, cmd = field.input.Update(msg)
		field.Value = field.input.Value()
		return true, cmd
	case tea.KeyPressMsg:
		switch msg.String() {
		case "tab", "down":
			f.focus((f.FocusIdx + 1) % len(f.Fields))
			return true, nil
		case "shift+tab", "up":
			f.focus((f.FocusIdx - 1 + len(f.Fields)) % len(f.Fields))
			return true, nil
		case "enter":
			if err := f.Validate(); err != "" {
				f.Error = err
				return true, nil
			}
			f.Active = false
			id, values := f.ID, f.Values()
			return true, func() tea.Msg {
				return FormSubmitMsg{FormID: id, Values: values}
			}
		case "esc":
			f.Active = false
			id := f.ID
			return true, func() tea.Msg {
				return FormCancelMsg{FormID: id}
			}
		}
		if field.Type == FieldDropdown {
			if len(field.Options) > 0 {
				switch msg.String() {
				case "left":
					field.optIdx = (field.optIdx - 1 + len(field.Options)) % len(field.Options)
					field.Value = field.Options[field.optIdx]
				case "right", "space":
					field.optIdx = (field.optIdx + 1) % len(field.Options)
					field.Value = field.Options[field.optIdx]
				}
			}
			return true, nil
		}
		var cmd tea.Cmd
		field.input, cmd = field.input.Update(msg)
		field.Value = field.input.Value()
		f.Error = ""
		return true, cmd
	}
	return false, nil
}

// View renders the form.
func (f *Form) View() string {
	if !f.Active {
		return ""
	}

	var b strings.Builder
	titleStyle := lipgloss.NewStyle().Bold(true).Foreground(theme.Current.Primary)
	labelStyle := lipgloss.NewStyle().Width(20).Foreground(theme.Current.Foreground)
	focusStyle := lipgloss.NewStyle().Foreground(theme.Current.Info).Bold(true)
	helpStyle := lipgloss.NewStyle().Foreground(theme.Current.Muted)
	errStyle := lipgloss.NewStyle().Foreground(theme.Current.Error)

	b.WriteString(titleStyle.Render(f.Title))
	b.WriteString("\n\n")
	if f.Intro != "" {
		b.WriteString(f.Intro)
		b.WriteString("\n\n")
	}

	for i, field := range f.Fields {
		prefix := "  "
		if i == f.FocusIdx {
			prefix = focusStyle.Render("> ")
		}
		label := field.Label
		if field.Required {
			label += " *"
		}
		var value string
		if field.Type == FieldDropdown {
			left, right := theme.Glyphs().DropLeft, theme.Glyphs().DropRight
			value = left + " " + field.Value + " " + right
		} else {
			value = field.input.View()
		}
		b.WriteString(prefix + labelStyle.Render(label+":") + " " + value + "\n")
		if i == f.FocusIdx && field.Help != "" {
			// A help text may span several lines; each is indented.
			for _, line := range strings.Split(field.Help, "\n") {
				b.WriteString("    " + helpStyle.Render(line) + "\n")
			}
		}
	}

	if f.Error != "" {
		b.WriteString("\n" + errStyle.Render(f.Error))
	}

	b.WriteString("\n\n" + helpStyle.Render("Enter: submit  Esc: cancel  Tab/Shift+Tab: next/previous field  Left/Right: change choice"))

	style := lipgloss.NewStyle().
		Border(theme.Current.Border).
		BorderForeground(theme.Current.Secondary).
		Padding(1, 2)

	return style.Render(b.String())
}

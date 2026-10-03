package components_test

import (
	"strings"
	"testing"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func makeForm() components.Form {
	return components.NewForm("Test Form", []components.Field{
		{Name: "name", Label: "Name", Type: components.FieldText, Required: true},
		{Name: "pass", Label: "Password", Type: components.FieldPassword},
		{Name: "env", Label: "Environment", Type: components.FieldDropdown, Options: []string{"dev", "staging", "prod"}, Value: "dev"},
	})
}

func TestForm_InitialState(t *testing.T) {
	f := makeForm()
	if !f.Active {
		t.Error("expected form to be active")
	}
	if f.FocusIdx != 0 {
		t.Errorf("expected focus 0, got %d", f.FocusIdx)
	}
}

func TestForm_Values(t *testing.T) {
	f := makeForm()
	vals := f.Values()
	if vals["name"] != "" {
		t.Errorf("expected empty name, got %q", vals["name"])
	}
	if vals["env"] != "dev" {
		t.Errorf("expected env='dev', got %q", vals["env"])
	}
}

func TestForm_TabNavigation(t *testing.T) {
	f := makeForm()
	f.Update(keyPress(tea.KeyTab))
	if f.FocusIdx != 1 {
		t.Errorf("expected focus 1, got %d", f.FocusIdx)
	}
}

func TestForm_TabWraps(t *testing.T) {
	f := makeForm()
	f.Update(keyPress(tea.KeyTab))
	f.Update(keyPress(tea.KeyTab))
	f.Update(keyPress(tea.KeyTab))
	if f.FocusIdx != 0 {
		t.Errorf("expected focus wrap to 0, got %d", f.FocusIdx)
	}
}

func TestForm_TextInput(t *testing.T) {
	f := makeForm()
	f.Update(keyPress('h'))
	f.Update(keyPress('i'))
	vals := f.Values()
	if vals["name"] != "hi" {
		t.Errorf("expected name='hi', got %q", vals["name"])
	}
}

func TestForm_PasswordInput(t *testing.T) {
	f := makeForm()
	f.Update(keyPress(tea.KeyTab)) // focus on password
	f.Update(keyPress('a'))
	f.Update(keyPress('b'))
	vals := f.Values()
	if vals["pass"] != "ab" {
		t.Errorf("expected pass='ab', got %q", vals["pass"])
	}
}

func TestForm_DropdownCycle(t *testing.T) {
	f := makeForm()
	f.Update(keyPress(tea.KeyTab))
	f.Update(keyPress(tea.KeyTab)) // focus on dropdown
	f.Update(keyPress(tea.KeyRight))
	vals := f.Values()
	if vals["env"] != "staging" {
		t.Errorf("expected env='staging', got %q", vals["env"])
	}
}

func TestForm_ValidationRequired(t *testing.T) {
	f := makeForm()
	// Try to submit without filling required "name"
	_, cmd := f.Update(keyPress(tea.KeyEnter))
	if cmd != nil {
		t.Error("expected no submit command when validation fails")
	}
	if f.Error == "" {
		t.Error("expected error message for required field")
	}
}

func TestForm_Submit(t *testing.T) {
	f := makeForm()
	f.Update(keyPress('t'))
	f.Update(keyPress('e'))
	f.Update(keyPress('s'))
	f.Update(keyPress('t'))
	_, cmd := f.Update(keyPress(tea.KeyEnter))
	if cmd == nil {
		t.Fatal("expected submit command")
	}
	msg := cmd()
	result, ok := msg.(components.FormSubmitMsg)
	if !ok {
		t.Fatalf("expected FormSubmitMsg, got %T", msg)
	}
	if result.Values["name"] != "test" {
		t.Errorf("expected name='test', got %q", result.Values["name"])
	}
}

func TestForm_Cancel(t *testing.T) {
	f := makeForm()
	_, cmd := f.Update(keyPress(tea.KeyEscape))
	if cmd == nil {
		t.Fatal("expected cancel command")
	}
	msg := cmd()
	_, ok := msg.(components.FormCancelMsg)
	if !ok {
		t.Fatalf("expected FormCancelMsg, got %T", msg)
	}
	if f.Active {
		t.Error("expected form to be inactive after cancel")
	}
}

func TestForm_Backspace(t *testing.T) {
	f := makeForm()
	f.Update(keyPress('a'))
	f.Update(keyPress('b'))
	f.Update(keyPress(tea.KeyBackspace))
	vals := f.Values()
	if vals["name"] != "a" {
		t.Errorf("expected name='a', got %q", vals["name"])
	}
}

// SetValue fills a text field (a picker result); typing continues from it.
func TestForm_SetValueAndFocusedField(t *testing.T) {
	f := makeForm()
	if f.FocusedField() != "name" {
		t.Errorf("focused = %q", f.FocusedField())
	}
	f.SetValue("name", "/home/u")
	f.Update(tea.KeyPressMsg(tea.Key{Code: 'x', Text: "x"}))
	if got := f.Value("name"); got != "/home/ux" {
		t.Errorf("value = %q", got)
	}
	f.SetValue("env", "prod") // dropdowns are not set this way
	if f.Value("env") != "dev" {
		t.Errorf("dropdown changed to %q", f.Value("env"))
	}
}

// R5.11: Reopen shows an error and keeps what the user typed.
func TestForm_ReopenKeepsInput(t *testing.T) {
	f := makeForm()
	f.Update(tea.KeyPressMsg(tea.Key{Code: 'a', Text: "a"}))
	f.Update(tea.KeyPressMsg(tea.Key{Code: tea.KeyEnter}))
	if f.Active {
		t.Fatal("form still active after submit")
	}
	f.Reopen("name already exists")
	if !f.Active || f.Error != "name already exists" || f.Value("name") != "a" {
		t.Errorf("active=%v error=%q value=%q", f.Active, f.Error, f.Value("name"))
	}
	if !strings.Contains(f.View(), "name already exists") {
		t.Error("error not shown")
	}
}

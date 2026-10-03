package ui_test

import (
	"fmt"
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui"
)

// f cycles the profile filter through the real slugs and back to all;
// each step asks the backend for that profile's conflicts.
func TestConflicts_FilterCyclesSlugs(t *testing.T) {
	b := conflictsBackend(t)
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "D", "idle"), profileJSON("work", "W", "idle")})
	m := open(t, ui.NewConflictsModel(b.client()))
	if !strings.Contains(content(m), "Filter: all profiles") {
		t.Errorf("header:\n%s", content(m))
	}
	m = drive(t, m, press("f"))
	if !strings.Contains(content(m), "Filter: profile docs") {
		t.Errorf("header:\n%s", content(m))
	}
	_ = drive(t, m, press("f"), press("f"))
	want := []string{
		"GET /conflicts",
		"GET /conflicts?profile=docs",
		"GET /conflicts?profile=work",
		"GET /conflicts",
	}
	if got := b.matching("GET /conflicts"); fmt.Sprint(got) != fmt.Sprint(want) {
		t.Errorf("queries = %v, want %v", got, want)
	}
}

// An answer for a filter the user already left is dropped.
func TestConflicts_FilterDropsStaleAnswer(t *testing.T) {
	b := conflictsBackend(t)
	b.json("GET", "/profiles", 200, []any{profileJSON("docs", "D", "idle")})
	m := open(t, ui.NewConflictsModel(b.client()))
	stale := m.Init() // fetches for "all profiles"
	updated, _ := m.Update(press("f"))
	m = updated.(ui.ConflictsModel)
	b.json("GET", "/conflicts", 200, []any{})
	m = drive(t, m, runAll(stale)...)
	if !strings.Contains(content(m), "Filter: profile docs") || !strings.Contains(content(m), "Loading") {
		t.Errorf("stale answer shown:\n%s", content(m))
	}
}

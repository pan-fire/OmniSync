package components_test

import (
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func TestRenderBadge_ContainsState(t *testing.T) {
	badge := stripANSI(components.RenderBadge("syncing"))
	if !strings.Contains(badge, "SYNCING") {
		t.Errorf("expected badge to contain 'SYNCING', got %q", badge)
	}
}

func TestRenderBadge_UppercasesState(t *testing.T) {
	badge := stripANSI(components.RenderBadge("idle"))
	if !strings.Contains(badge, "IDLE") {
		t.Errorf("expected badge to contain 'IDLE', got %q", badge)
	}
}

func TestRenderBadge_UnknownState(t *testing.T) {
	badge := stripANSI(components.RenderBadge("unknown_state"))
	if !strings.Contains(badge, "UNKNOWN_STATE") {
		t.Errorf("expected badge to contain 'UNKNOWN_STATE', got %q", badge)
	}
}

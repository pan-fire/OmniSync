package components

import (
	"fmt"
	"strings"

	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// RenderBadge returns a styled status badge string like "[IDLE]" or "[ERROR]".
func RenderBadge(state string) string {
	label := strings.ToUpper(state)
	style := theme.Current.StatusBadge(state)
	return style.Render(fmt.Sprintf("[%s]", label))
}

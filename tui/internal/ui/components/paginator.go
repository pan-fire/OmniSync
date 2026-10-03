package components

import (
	"fmt"

	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
)

// Paginator tracks pagination state.
type Paginator struct {
	Page     int
	PageSize int
	Total    int
}

// NewPaginator creates a new paginator.
func NewPaginator(pageSize int) Paginator {
	return Paginator{Page: 0, PageSize: pageSize}
}

// TotalPages returns the total number of pages.
func (p *Paginator) TotalPages() int {
	if p.Total <= 0 {
		return 1
	}
	return (p.Total + p.PageSize - 1) / p.PageSize
}

// NextPage advances to the next page if possible.
func (p *Paginator) NextPage() bool {
	if p.Page < p.TotalPages()-1 {
		p.Page++
		return true
	}
	return false
}

// PrevPage goes back one page if possible.
func (p *Paginator) PrevPage() bool {
	if p.Page > 0 {
		p.Page--
		return true
	}
	return false
}

// Offset returns the current offset for API queries.
func (p *Paginator) Offset() int {
	return p.Page * p.PageSize
}

// View renders the pagination info.
func (p *Paginator) View() string {
	style := lipgloss.NewStyle().Foreground(theme.Current.Muted)
	return style.Render(fmt.Sprintf("Page %d of %d (n/N)", p.Page+1, p.TotalPages()))
}

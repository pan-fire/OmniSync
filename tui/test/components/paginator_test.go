package components_test

import (
	"strings"
	"testing"

	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

func TestPaginator_Defaults(t *testing.T) {
	p := components.NewPaginator(10)
	if p.Page != 0 {
		t.Errorf("expected page 0, got %d", p.Page)
	}
	if p.PageSize != 10 {
		t.Errorf("expected page size 10, got %d", p.PageSize)
	}
}

func TestPaginator_TotalPages(t *testing.T) {
	p := components.NewPaginator(10)
	p.Total = 25
	if p.TotalPages() != 3 {
		t.Errorf("expected 3 pages, got %d", p.TotalPages())
	}
}

func TestPaginator_TotalPagesEmpty(t *testing.T) {
	p := components.NewPaginator(10)
	p.Total = 0
	// With 0 items, TotalPages returns 1 (minimum 1 page)
	if p.TotalPages() != 1 {
		t.Errorf("expected 1 page, got %d", p.TotalPages())
	}
}

func TestPaginator_Offset(t *testing.T) {
	p := components.NewPaginator(10)
	p.Total = 50
	p.Page = 2
	if p.Offset() != 20 {
		t.Errorf("expected offset 20, got %d", p.Offset())
	}
}

func TestPaginator_NextPage(t *testing.T) {
	p := components.NewPaginator(10)
	p.Total = 25
	ok := p.NextPage()
	if !ok || p.Page != 1 {
		t.Errorf("expected page 1, got %d, ok=%v", p.Page, ok)
	}
}

func TestPaginator_NextPageAtEnd(t *testing.T) {
	p := components.NewPaginator(10)
	p.Total = 15
	p.Page = 1 // last page
	ok := p.NextPage()
	if ok {
		t.Error("expected NextPage to return false at last page")
	}
	if p.Page != 1 {
		t.Errorf("expected page to stay 1, got %d", p.Page)
	}
}

func TestPaginator_PrevPage(t *testing.T) {
	p := components.NewPaginator(10)
	p.Total = 50
	p.Page = 2
	ok := p.PrevPage()
	if !ok || p.Page != 1 {
		t.Errorf("expected page 1, got %d, ok=%v", p.Page, ok)
	}
}

func TestPaginator_PrevPageAtStart(t *testing.T) {
	p := components.NewPaginator(10)
	p.Total = 50
	ok := p.PrevPage()
	if ok {
		t.Error("expected PrevPage to return false at page 0")
	}
}

func TestPaginator_ViewShowsPageInfo(t *testing.T) {
	p := components.NewPaginator(10)
	p.Total = 30
	view := stripANSI(p.View())
	if !strings.Contains(view, "Page 1 of 3") {
		t.Errorf("expected view to contain 'Page 1 of 3', got %q", view)
	}
}

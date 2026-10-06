package ui

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

type jobsMode int

const (
	jobsModeList jobsMode = iota
	jobsModeDetail
)

// jobsPage carries one page of GET /jobs, tagged with the query it answers.
type jobsPage struct {
	Skip   int
	Filter string
	Jobs   []api.SyncJobResponse
}

// JobsModel is the Jobs list view.
type JobsModel struct {
	client   *api.Client
	jobs     []api.SyncJobResponse
	table    components.Table
	detail   *api.SyncJobResponse
	files    []api.FileChangeResponse
	filesTbl components.Table
	mode     jobsMode
	// filter is "" (all profiles) or a profile slug; f cycles through the
	// slugs in profiles.
	filter   string
	profiles []string
	skip     int
	limit    int
	hasMore  bool
	loading  bool
	err      error
	width    int
	height   int
}

// NewJobsModel creates a new jobs view.
func NewJobsModel(client *api.Client) JobsModel {
	return JobsModel{
		client:   client,
		table:    components.NewTable(jobColumns(true), jobsPageSize),
		filesTbl: components.NewTable(fileChangeColumns(), 20),
		limit:    jobsPageSize,
		loading:  true,
	}
}

// jobsPageSize is how many jobs one GET /jobs page asks for.
const jobsPageSize = 20

// jobColumns are the columns of a job list. The Profile column is left out
// where every row is the same profile's (Profile Detail's History tab).
func jobColumns(withProfile bool) []components.Column {
	cols := []components.Column{{Title: "ID", Width: 6}}
	if withProfile {
		cols = append(cols, components.Column{Title: "Profile", Width: 14})
	}
	return append(cols,
		components.Column{Title: "Direction", Width: 10},
		components.Column{Title: "Started", Width: 19},
		components.Column{Title: "Status", Width: 10},
		components.Column{Title: "Files", Width: 6},
		components.Column{Title: "Conflicts", Width: 9},
		components.Column{Title: "Errors", Width: 6},
	)
}

// fileChangeColumns are the columns of a job's file changes.
func fileChangeColumns() []components.Column {
	return []components.Column{
		{Title: "Path", Width: 44},
		{Title: "Action", Width: 10},
		{Title: "Side", Width: 7},
		{Title: "Size", Width: 12},
	}
}

// jobRows builds the rows of a job list. profile names a job's profile;
// nil leaves the Profile column out (see jobColumns).
func jobRows(jobs []api.SyncJobResponse, profile func(api.SyncJobResponse) string) []components.Row {
	rows := make([]components.Row, 0, len(jobs))
	for _, j := range jobs {
		values := []string{strconv.Itoa(j.ID)}
		if profile != nil {
			values = append(values, profile(j))
		}
		values = append(values,
			jobDirectionLabel(j.Direction),
			formatTime(j.StartedAt),
			jobListStatus(j),
			strconv.Itoa(j.FilesChanged),
			strconv.Itoa(j.Conflicts),
			strconv.Itoa(j.Errors),
		)
		rows = append(rows, components.Row{
			Key:    strconv.Itoa(j.ID),
			Values: values,
			// The Status column is the fourth from the end.
			Colors: cellColors(len(values), len(values)-4, theme.Current.StatusColor(jobStatusLabel(j))),
		})
	}
	return rows
}

// fileChangeRows builds the rows of a job's file changes.
func fileChangeRows(files []api.FileChangeResponse) []components.Row {
	rows := make([]components.Row, 0, len(files))
	for _, f := range files {
		rows = append(rows, components.Row{
			Key:    f.FilePath,
			Values: []string{f.FilePath, f.Action, fileSideLabel(f.Side), sizeOrDash(f.SizeBytes)},
		})
	}
	return rows
}

// renderJobSummary draws a job's header lines in a job detail. profile is
// the job's profile, or "" to leave it out (all jobs are one profile's).
func renderJobSummary(job *api.SyncJobResponse, profile string) string {
	labelStyle := lipgloss.NewStyle().Foreground(theme.Current.Muted)
	valueStyle := lipgloss.NewStyle().Foreground(theme.Current.Foreground)
	var b strings.Builder
	fmt.Fprintf(&b, "  ID: %s  Status: %s  Direction: %s\n",
		valueStyle.Render(strconv.Itoa(job.ID)),
		components.RenderBadge(jobStatusLabel(*job)),
		valueStyle.Render(jobDirectionLabel(job.Direction)))

	b.WriteString("  ")
	if profile != "" {
		fmt.Fprintf(&b, "Profile: %s  ", valueStyle.Render(profile))
	}
	fmt.Fprintf(&b, "Started: %s  Finished: %s\n",
		labelStyle.Render(formatTime(job.StartedAt)),
		labelStyle.Render(formatTimePtr(job.FinishedAt, "-")))

	fmt.Fprintf(&b, "  Files: %d  Conflicts: %d  Errors: %d\n\n",
		job.FilesChanged, job.Conflicts, job.Errors)
	if warnings := renderWarnings(job.Warnings); warnings != "" {
		b.WriteString(headerText("  Warnings"))
		b.WriteString("\n")
		b.WriteString(warnings)
		b.WriteString("\n")
	}

	return b.String()
}

// jobsPageLabel says which page of GET /jobs is shown. GET /jobs returns no
// total, so the label says whether older jobs exist instead of "of N".
func jobsPageLabel(skip, limit, shown int, hasMore, loading bool) string {
	page := skip/limit + 1
	switch {
	case loading && shown == 0:
		return fmt.Sprintf("Page %d", page)
	case hasMore:
		return fmt.Sprintf("Page %d (more: n)", page)
	case page == 1:
		return "Page 1 (only page)"
	}
	return fmt.Sprintf("Page %d (last page)", page)
}

func (m JobsModel) ViewID() ViewID { return ViewJobs }

// KeyHints names the keys of the current mode for the bottom bar.
func (m JobsModel) KeyHints() string {
	if m.mode == jobsModeDetail {
		return "Up/Down:move  n/N:page  Esc:back to list"
	}
	hints := "Enter:detail  f:filter  r:refresh"
	if m.hasMore {
		hints += "  n:older"
	}
	if m.skip > 0 {
		hints += "  N:newer"
	}
	return hints
}

func (m JobsModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "Up/Down, j/k", Desc: "Move"},
		{Key: "Enter", Desc: "View job detail"},
		{Key: "f", Desc: "Filter: cycle all profiles / one profile"},
		{Key: "n/N", Desc: "Next/previous page (older/newer jobs)"},
		{Key: "r", Desc: "Refresh"},
		{Key: "Esc", Desc: "Back to list"},
	}
}

// PollSpec polls quickly while a listed job runs.
func (m JobsModel) PollSpec() *PollSpec {
	return &PollSpec{
		FastInterval: FastPollInterval,
		SlowInterval: SlowTickInterval,
		IsActive: func(interface{}) bool {
			for _, j := range m.jobs {
				if j.Status == api.JobStatusRunning {
					return true
				}
			}
			return false
		},
	}
}

func (m JobsModel) Init() tea.Cmd {
	return tea.Batch(m.fetchJobs(), m.fetchProfileSlugs())
}

func (m JobsModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.table.SetWidth(msg.Width)

	case PollResultMsg:
		return m.handlePollResult(msg)

	case TickMsg:
		if m.mode == jobsModeList {
			return m, m.fetchJobs()
		}
		return m, nil

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m JobsModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	switch data := msg.Data.(type) {
	case jobsPage:
		if data.Skip != m.skip || data.Filter != m.filter {
			return m, nil // answer to an older query
		}
		m.loading = false
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		m.err = nil
		m.jobs = data.Jobs
		m.hasMore = len(data.Jobs) == m.limit
		m.updateTable()
	case []api.ProfileStatusResponse:
		if msg.Err == nil {
			slugs := make([]string, 0, len(data))
			for _, p := range data {
				slugs = append(slugs, p.Slug)
			}
			m.profiles = slugs
		}
	case *api.SyncJobResponse:
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		m.detail = data
	case []api.FileChangeResponse:
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		m.files = data
		m.updateFilesTable()
	default:
		if msg.Err != nil {
			m.err = msg.Err
			m.loading = false
		}
	}
	return m, nil
}

func (m JobsModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.mode == jobsModeDetail {
		switch msg.String() {
		case "esc":
			m.mode = jobsModeList
			return m, m.fetchJobs()
		default:
			m.filesTbl.Update(msg)
		}
		return m, nil
	}

	switch msg.String() {
	case "enter":
		if row := m.table.SelectedRow(); row != nil {
			id, err := strconv.Atoi(row.Key)
			if err != nil {
				return m, flash("Invalid job ID: "+row.Key, true)
			}
			m.mode = jobsModeDetail
			m.detail = nil
			m.files = nil
			m.filesTbl.SetRows(nil)
			return m, tea.Batch(m.fetchJobDetail(id), m.fetchJobFiles(id))
		}
	case "f":
		m.filter = nextFilter(m.filter, m.profiles)
		m.skip = 0
		m.loading = true
		return m, m.fetchJobs()
	case "n":
		if m.hasMore {
			m.skip += m.limit
			m.loading = true
			return m, m.fetchJobs()
		}
		return m, nil
	case "N":
		if m.skip > 0 {
			m.skip -= m.limit
			if m.skip < 0 {
				m.skip = 0
			}
			m.loading = true
			return m, m.fetchJobs()
		}
		return m, nil
	case "r":
		m.loading = true
		return m, tea.Batch(m.fetchJobs(), m.fetchProfileSlugs())
	default:
		m.table.Update(msg)
	}
	return m, nil
}

// nextFilter cycles "" (all) -> first slug -> ... -> last slug -> "".
func nextFilter(current string, slugs []string) string {
	if len(slugs) == 0 {
		return ""
	}
	if current == "" {
		return slugs[0]
	}
	for i, s := range slugs {
		if s == current {
			if i+1 < len(slugs) {
				return slugs[i+1]
			}
			return ""
		}
	}
	return ""
}

func (m JobsModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder

	if m.mode == jobsModeDetail {
		b.WriteString(headerText("  Job Detail"))
		b.WriteString("\n")
		if m.err != nil {
			b.WriteString(errorLine(m.err))
		}
		if m.detail != nil {
			b.WriteString(renderJobSummary(m.detail, m.jobProfile(*m.detail)))
		}
		b.WriteString(headerText("  File Changes"))
		b.WriteString("\n")
		b.WriteString(m.filesTbl.View())
		return tea.NewView(b.String())
	}

	filter := "all profiles"
	if m.filter != "" {
		filter = "profile " + m.filter
	}
	b.WriteString(headerText("  Jobs"))
	b.WriteString(mutedText(fmt.Sprintf("  Filter: %s  %s", filter, m.pageLabel())))
	b.WriteString("\n")

	if m.err != nil {
		b.WriteString(errorLine(m.err))
	}

	if m.loading && len(m.jobs) == 0 && m.err == nil {
		b.WriteString("  Loading...")
		return tea.NewView(b.String())
	}

	b.WriteString(m.table.View())

	return tea.NewView(b.String())
}

// pageLabel says which page is shown.
func (m JobsModel) pageLabel() string {
	return jobsPageLabel(m.skip, m.limit, len(m.jobs), m.hasMore, m.loading)
}

func (m JobsModel) jobProfile(j api.SyncJobResponse) string {
	switch {
	case j.ProfileName != nil && *j.ProfileName != "":
		return *j.ProfileName
	case j.ProfileSlug != nil && *j.ProfileSlug != "":
		return *j.ProfileSlug
	case m.filter != "":
		return m.filter
	}
	return "-"
}

func (m *JobsModel) updateTable() {
	m.table.SetRows(jobRows(m.jobs, m.jobProfile))
}

func (m *JobsModel) updateFilesTable() {
	m.filesTbl.SetRows(fileChangeRows(m.files))
}

func (m JobsModel) fetchJobs() tea.Cmd {
	client, skip, limit, filter := m.client, m.skip, m.limit, m.filter
	return func() tea.Msg {
		data, err := client.ListJobs(context.Background(), skip, limit, filter)
		return PollResultMsg{ViewID: ViewJobs, Data: jobsPage{Skip: skip, Filter: filter, Jobs: data}, Err: err}
	}
}

func (m JobsModel) fetchProfileSlugs() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.ListProfiles(context.Background())
		return PollResultMsg{ViewID: ViewJobs, Data: data, Err: err}
	}
}

func (m JobsModel) fetchJobDetail(id int) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.GetJob(context.Background(), id)
		return PollResultMsg{ViewID: ViewJobs, Data: data, Err: err}
	}
}

func (m JobsModel) fetchJobFiles(id int) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.GetJobFiles(context.Background(), id)
		return PollResultMsg{ViewID: ViewJobs, Data: data, Err: err}
	}
}

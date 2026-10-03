package ui

import (
	"context"
	"strconv"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// The History tab lists the profile's sync jobs (GET /jobs?profile=<slug>),
// newest first and a page at a time, with the same columns as the Jobs view
// minus Profile. Enter opens a job's detail and file changes as in Jobs.

type historySubView int

const (
	historySubList historySubView = iota
	historySubDetail
)

// historyPage carries one page of the profile's jobs, tagged with the page
// it answers.
type historyPage struct {
	skip int
	jobs []api.SyncJobResponse
}

// historyJob and historyFiles carry a job's detail and file changes, tagged
// with the job they answer.
type historyJob struct {
	id  int
	job *api.SyncJobResponse
}

type historyFiles struct {
	id    int
	files []api.FileChangeResponse
}

func (m ProfileDetailModel) handleHistoryKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.historyView == historySubDetail {
		m.historyFilesTbl.Update(msg)
		return m, nil
	}

	switch msg.String() {
	case "enter":
		row := m.historyTable.SelectedRow()
		if row == nil {
			return m, nil
		}
		id, err := strconv.Atoi(row.Key)
		if err != nil {
			return m, flash("Invalid job ID: "+row.Key, true)
		}
		m.historyView = historySubDetail
		m.historyJobID = id
		m.historyDetail = nil
		m.historyFiles = nil
		m.historyDetailErr = nil
		m.historyFilesTbl.SetRows(nil)
		return m, tea.Batch(m.fetchHistoryJob(id), m.fetchHistoryFiles(id))
	case "n":
		if m.historyHasMore {
			m.historySkip += jobsPageSize
			m.historyLoading = true
			return m, m.fetchHistory()
		}
		return m, nil
	case "N":
		if m.historySkip > 0 {
			m.historySkip -= jobsPageSize
			if m.historySkip < 0 {
				m.historySkip = 0
			}
			m.historyLoading = true
			return m, m.fetchHistory()
		}
		return m, nil
	case "r":
		m.historyLoading = true
		return m, m.fetchHistory()
	default:
		m.historyTable.Update(msg)
	}
	return m, nil
}

// handleHistoryData applies an answer for the History tab.
func (m ProfileDetailModel) handleHistoryData(data interface{}, err error) ProfileDetailModel {
	switch data := data.(type) {
	case historyPage:
		if data.skip != m.historySkip {
			return m // answer to a page the user has left
		}
		m.historyLoading = false
		m.historyErr = err
		if err == nil {
			m.historyLoadedFor = m.slug
			m.historyJobs = data.jobs
			m.historyHasMore = len(data.jobs) == jobsPageSize
			m.historyTable.SetRows(jobRows(m.historyJobs, nil))
		}
	case historyJob:
		if data.id != m.historyJobID {
			return m
		}
		if err != nil {
			m.historyDetailErr = err
			return m
		}
		m.historyDetail = data.job
	case historyFiles:
		if data.id != m.historyJobID {
			return m
		}
		if err != nil {
			m.historyDetailErr = err
			return m
		}
		m.historyFiles = data.files
		m.historyFilesTbl.SetRows(fileChangeRows(data.files))
	}
	return m
}

// historyKeyHints names the History tab's keys under the table.
func (m ProfileDetailModel) historyKeyHints() string {
	if m.historyView == historySubDetail {
		return "  Up/Down:move  n/N:page  Esc:back to history"
	}
	hints := "  Enter:detail  r:refresh"
	if m.historyHasMore {
		hints += "  n:older"
	}
	if m.historySkip > 0 {
		hints += "  N:newer"
	}
	return hints + "  Esc:back"
}

func (m ProfileDetailModel) renderHistoryTab() string {
	var b strings.Builder

	if m.historyView == historySubDetail {
		b.WriteString(headerText("  Sync job " + strconv.Itoa(m.historyJobID)))
		b.WriteString("\n")
		if m.historyDetailErr != nil {
			b.WriteString(errorLine(m.historyDetailErr))
		}
		if m.historyDetail != nil {
			b.WriteString(renderJobSummary(m.historyDetail, ""))
		}
		b.WriteString(headerText("  File Changes"))
		b.WriteString("\n")
		b.WriteString(m.historyFilesTbl.View())
		b.WriteString("\n")
		b.WriteString(mutedText(m.historyKeyHints()))
		return b.String()
	}

	b.WriteString(headerText("  Sync history"))
	b.WriteString(mutedText("  " + jobsPageLabel(m.historySkip, jobsPageSize, len(m.historyJobs), m.historyHasMore, m.historyLoading)))
	b.WriteString("\n")
	if m.historyErr != nil {
		b.WriteString(errorLine(m.historyErr))
	}
	switch {
	case m.historyLoading && len(m.historyJobs) == 0 && m.historyErr == nil:
		b.WriteString("  Loading...\n")
	case len(m.historyJobs) == 0 && m.historyErr == nil:
		b.WriteString(mutedText("  No sync jobs for this profile yet.") + "\n")
	default:
		b.WriteString(m.historyTable.View())
		b.WriteString("\n")
	}
	b.WriteString(mutedText(m.historyKeyHints()))
	return b.String()
}

func (m ProfileDetailModel) fetchHistory() tea.Cmd {
	client, slug, skip := m.client, m.slug, m.historySkip
	return func() tea.Msg {
		data, err := client.ListJobs(context.Background(), skip, jobsPageSize, slug)
		return PollResultMsg{ViewID: ViewProfileDetail, Data: ProfileDetailData{Slug: slug, Value: historyPage{skip: skip, jobs: data}}, Err: err}
	}
}

func (m ProfileDetailModel) fetchHistoryJob(id int) tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		data, err := client.GetJob(context.Background(), id)
		return PollResultMsg{ViewID: ViewProfileDetail, Data: ProfileDetailData{Slug: slug, Value: historyJob{id: id, job: data}}, Err: err}
	}
}

func (m ProfileDetailModel) fetchHistoryFiles(id int) tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		data, err := client.GetJobFiles(context.Background(), id)
		return PollResultMsg{ViewID: ViewProfileDetail, Data: ProfileDetailData{Slug: slug, Value: historyFiles{id: id, files: data}}, Err: err}
	}
}

// historyKeyBindings lists the History tab's keys for the help screen.
func (m ProfileDetailModel) historyKeyBindings() []components.KeyBinding {
	if m.historyView == historySubDetail {
		return []components.KeyBinding{
			{Key: "Up/Down, j/k", Desc: "Move through the job's file changes"},
			{Key: "n/N", Desc: "Next/previous page of file changes"},
			{Key: "Esc", Desc: "Back to the history list"},
		}
	}
	return []components.KeyBinding{
		{Key: "Up/Down, j/k", Desc: "Move"},
		{Key: "Enter", Desc: "View job detail and file changes"},
		{Key: "n/N", Desc: "Next/previous page (older/newer jobs)"},
		{Key: "r", Desc: "Refresh"},
	}
}

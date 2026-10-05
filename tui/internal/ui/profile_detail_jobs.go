package ui

import (
	"context"
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// Backups, restores and selective syncs run in the background on the
// server: the start answers at once (202, job running) or refuses at once
// (409 backup_running / sync_busy, 404, 422). The view then follows the job
// by polling it at FastPollInterval in a command, so the UI stays usable, and
// flashes the outcome when it ends.

// Kinds of background job the profile view follows.
const (
	jobKindBackup    = "backup"
	jobKindRestore   = "restore"
	jobKindSelective = "selective sync"
)

// Actions of the start answers and of the followed outcomes.
const (
	actionBackupStarted       = "run_backup_started"
	actionBackupDone          = "run_backup"
	actionRestoreStarted      = "restore_started"
	actionRestoreFilesStarted = "restore_files_started"
	actionRestoreDone         = "restore"
	actionRestoreFilesDone    = "restore_files"
	actionSelectiveStarted    = "selective_sync_started"
	actionSelectiveDone       = "selective_sync"
)

// runningJob is a job this view started and is still following.
type runningJob struct {
	slug string
	kind string
	id   int
}

// backupStart is the answer to a backup or restore start.
type backupStart struct {
	Slug     string
	Kind     string
	TargetID int
	Job      *api.BackupJobResponse
}

// backupFollow is how a followed backup or restore job ended.
type backupFollow struct {
	Slug  string
	Kind  string
	JobID int
	// Job is the ended job; nil when FollowErr is set.
	Job *api.BackupJobResponse
	// FollowErr: polling the job failed (the backend went away).
	FollowErr error
}

// selectiveStart is the answer to a selective sync start.
type selectiveStart struct {
	Slug string
	Run  *api.SelectiveSyncResponse
}

// selectiveFollow is how a followed selective sync ended.
type selectiveFollow struct {
	Slug      string
	JobID     int
	Run       *api.SelectiveSyncResponse
	FollowErr error
}

// startBackupCmd sends a backup or restore start and reports the answer as
// action.
func startBackupCmd(slug, kind, action string, targetID int,
	start func(ctx context.Context) (*api.BackupJobResponse, error)) tea.Cmd {
	return func() tea.Msg {
		job, err := start(context.Background())
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: action, Err: err,
			Data: backupStart{Slug: slug, Kind: kind, TargetID: targetID, Job: job}}
	}
}

// followBackupCmd polls a started backup or restore job until it has ended
// and reports it as action.
func followBackupCmd(client *api.Client, start backupStart, action string) tea.Cmd {
	return func() tea.Msg {
		out := backupFollow{Slug: start.Slug, Kind: start.Kind, JobID: start.Job.ID, Job: start.Job}
		if start.Job.Status == api.BackupJobRunning {
			out.Job, out.FollowErr = client.WaitForBackupJob(context.Background(), start.Slug, start.TargetID,
				start.Job.ID, FastPollInterval)
		}
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: action, Data: out}
	}
}

// followSelectiveCmd polls a started selective sync until it has ended.
func followSelectiveCmd(client *api.Client, start selectiveStart) tea.Cmd {
	return func() tea.Msg {
		out := selectiveFollow{Slug: start.Slug, JobID: start.Run.JobID, Run: start.Run}
		if start.Run.Status == api.JobStatusRunning {
			out.Run, out.FollowErr = client.WaitForSelectiveSync(context.Background(), start.Slug, start.Run.JobID,
				FastPollInterval)
		}
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: actionSelectiveDone, Data: out}
	}
}

// withRunning returns m following one more job.
func (m ProfileDetailModel) withRunning(j runningJob) ProfileDetailModel {
	m.running = append(append([]runningJob(nil), m.running...), j)
	return m
}

// withoutRunning returns m no longer following the job.
func (m ProfileDetailModel) withoutRunning(slug, kind string, id int) ProfileDetailModel {
	kept := make([]runningJob, 0, len(m.running))
	for _, j := range m.running {
		if j.slug != slug || j.kind != kind || j.id != id {
			kept = append(kept, j)
		}
	}
	m.running = kept
	return m
}

// runningLine lists the jobs of the shown profile of the given kinds that
// still run (one line, no newline), or "" when there are none.
func (m ProfileDetailModel) runningLine(kinds ...string) string {
	var parts []string
	for _, j := range m.running {
		if j.slug != m.slug {
			continue
		}
		for _, k := range kinds {
			if j.kind == k {
				parts = append(parts, fmt.Sprintf("%s job %d", j.kind, j.id))
			}
		}
	}
	if len(parts) == 0 {
		return ""
	}
	return "  Running in the background: " + strings.Join(parts, ", ")
}

func capitalize(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

// handleBackupStarted handles the answer to a backup or restore start: a
// refusal is shown, a started job is followed.
func (m ProfileDetailModel) handleBackupStarted(msg ActionResultMsg, doneAction string) (tea.Model, tea.Cmd) {
	start, _ := msg.Data.(backupStart)
	what := fmt.Sprintf("%s of %s", capitalize(start.Kind), start.Slug)
	var refresh tea.Cmd
	if start.Kind == jobKindBackup {
		refresh = m.fetchBackups()
	}
	if msg.Err != nil || start.Job == nil {
		err := msg.Err
		if err == nil {
			err = fmt.Errorf("no job in the answer")
		}
		return m, tea.Batch(refresh, errorFlash(what+" not started", err))
	}
	text := fmt.Sprintf("%s started (job %d); it runs in the background", what, start.Job.ID)
	if start.Job.Status == api.BackupJobRunning {
		m = m.withRunning(runningJob{slug: start.Slug, kind: start.Kind, id: start.Job.ID})
	}
	return m, tea.Batch(refresh, flash(text, false), followBackupCmd(m.client, start, doneAction))
}

// handleBackupDone flashes how a followed backup or restore job ended.
func (m ProfileDetailModel) handleBackupDone(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	out, _ := msg.Data.(backupFollow)
	m = m.withoutRunning(out.Slug, out.Kind, out.JobID)
	what := fmt.Sprintf("%s of %s", capitalize(out.Kind), out.Slug)
	var refresh tea.Cmd
	if out.Kind == jobKindBackup && out.Slug == m.slug {
		refresh = m.fetchBackups()
	}
	switch {
	case msg.Err != nil:
		return m, tea.Batch(refresh, errorFlash(what+" failed", msg.Err))
	case out.FollowErr != nil:
		return m, tea.Batch(refresh, errorFlash(fmt.Sprintf("Lost track of the %s of %s (job %d); see the backups tab",
			out.Kind, out.Slug, out.JobID), out.FollowErr))
	case out.Job == nil:
		return m, refresh
	}
	reason := "no reason reported"
	if out.Job.ErrorMessage != nil && *out.Job.ErrorMessage != "" {
		reason = *out.Job.ErrorMessage
	}
	switch out.Job.Status {
	case api.BackupJobFailed:
		return m, tea.Batch(refresh, flash(what+" failed: "+reason, true))
	case api.BackupJobSkipped:
		return m, tea.Batch(refresh, flash(what+" skipped: "+reason, true))
	}
	return m, tea.Batch(refresh, flash(what+" finished", false))
}

// handleSelectiveStarted handles the answer to a selective sync start.
func (m ProfileDetailModel) handleSelectiveStarted(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	start, _ := msg.Data.(selectiveStart)
	if msg.Err != nil || start.Run == nil {
		err := msg.Err
		if err == nil {
			err = fmt.Errorf("no job in the answer")
		}
		return m, errorFlash("Selective sync not started", err)
	}
	if start.Run.Status == api.JobStatusRunning {
		m = m.withRunning(runningJob{slug: start.Slug, kind: jobKindSelective, id: start.Run.JobID})
	}
	text := fmt.Sprintf("Selective sync of %d file(s) started (job %d); it runs in the background",
		start.Run.Total, start.Run.JobID)
	return m, tea.Batch(flash(text, false), followSelectiveCmd(m.client, start))
}

// handleSelectiveDone flashes how a followed selective sync ended.
func (m ProfileDetailModel) handleSelectiveDone(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	out, _ := msg.Data.(selectiveFollow)
	m = m.withoutRunning(out.Slug, jobKindSelective, out.JobID)
	var refresh tea.Cmd
	if out.Slug == m.slug {
		refresh = m.fetchDiff()
	}
	if out.FollowErr != nil {
		return m, tea.Batch(refresh, errorFlash(fmt.Sprintf("Lost track of the selective sync (job %d); reload the diff with r",
			out.JobID), out.FollowErr))
	}
	r := out.Run
	if r == nil {
		return m, refresh
	}
	if r.Failed == 0 && r.Status != api.JobStatusFailed {
		return m, tea.Batch(refresh, flash(fmt.Sprintf("Selective sync applied (%d file(s))", r.Succeeded), false))
	}
	text := fmt.Sprintf("Selective sync: %d succeeded, %d failed", r.Succeeded, r.Failed)
	if r.Status == api.JobStatusFailed && r.Failed == 0 {
		text = fmt.Sprintf("Selective sync failed (%d of %d succeeded)", r.Succeeded, r.Total)
	}
	if len(r.Errors) > 0 {
		text += fmt.Sprintf("; %s: %s", safeLine(r.Errors[0].Path), safeLine(r.Errors[0].Error))
		if len(r.Errors) > 1 {
			text += fmt.Sprintf(" (and %d more)", len(r.Errors)-1)
		}
	}
	return m, tea.Batch(refresh, flash(text, true))
}

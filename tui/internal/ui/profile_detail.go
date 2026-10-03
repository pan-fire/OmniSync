package ui

import (
	"context"
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// ProfileDetailData tags data fetched for Profile Detail with the profile it
// belongs to, so a slow answer for a previously opened profile is dropped.
type ProfileDetailData struct {
	Slug  string
	Value interface{}
}

// syncRequest is a push, pull, two-way sync, resync or mode switch the user
// asked for. It is captured when the user presses the key and is what the
// confirmation acts on.
type syncRequest struct {
	seq       int
	slug      string
	name      string
	dir       api.SyncDirection
	localDir  string
	remoteDir string
	// maxDelete is the profile's delete limit, shown when the preview fails.
	maxDelete *int
	paused    bool
	// twoWay is set when the profile syncs both ways; lastError is why its
	// last sync failed or was refused (or why it needs a resync).
	twoWay    bool
	lastError string
}

// syncPreviewResult carries the answer of POST /profiles/{slug}/sync/preview
// for a pending push or pull prompt.
type syncPreviewResult struct {
	seq  int
	resp *api.SyncPreviewResponse
}

const (
	tabOverview    = "Overview"
	tabDifferences = "Differences"
	tabBackups     = "Backups"
	tabHistory     = "History"
)

// ProfileDetailModel is the Profile Detail view with tabs.
type ProfileDetailModel struct {
	client  *api.Client
	slug    string
	profile *api.ProfileStatusResponse
	tabs    components.Tabs
	loading bool
	err     error
	width   int
	height  int

	// Overview state
	syncing bool
	spinner components.Spinner

	// Push/pull (and paused two-way sync) confirmation: a preview runs
	// first, then the prompt opens.
	syncSeq     int
	checking    *syncRequest
	pendingSync *syncRequest
	// pendingResync and pendingSwitch are the profile a Resync or a
	// switch-to-two-way prompt acts on, captured when it opened.
	pendingResync *syncRequest
	pendingSwitch *syncRequest

	// Diff tab state
	diffTable   components.Table
	diffData    *api.DiffResponse
	diffFilter  diffFilter
	diffLoading bool
	diffErr     error
	pendingSel  []api.SelectiveSyncItem

	// Backups tab state
	backupTargets    []api.BackupTargetResponse
	backupTable      components.Table
	snapshots        []api.SnapshotResponse
	snapshotTable    components.Table
	backupsView      backupsSubView
	snapshotTarget   int
	pendingDeleteID  int
	pendingRestore   *restoreTarget
	pendingFull      *fullRestore
	pendingFiles     *fileRestore
	files            *snapshotBrowser
	backupsErr       error
	backupsLoadedFor string

	// running lists the backups, restores and selective syncs this view
	// started and still follows (profile_detail_jobs.go).
	running []runningJob

	// History tab state: the profile's sync jobs, a page at a time.
	historyJobs      []api.SyncJobResponse
	historyTable     components.Table
	historySkip      int
	historyHasMore   bool
	historyLoading   bool
	historyErr       error
	historyLoadedFor string
	historyView      historySubView
	historyJobID     int
	historyDetail    *api.SyncJobResponse
	historyFiles     []api.FileChangeResponse
	historyFilesTbl  components.Table
	historyDetailErr error

	// Trash tab state (profile_detail_trash.go)
	trash trashState

	// Overlays
	confirm components.Confirm
	form    components.Form
	// picker is the folder picker the backup target form opens with
	// Ctrl+O, or nil.
	picker *dirPicker
}

// NewProfileDetailModel creates a new profile detail view.
func NewProfileDetailModel(client *api.Client) ProfileDetailModel {
	return ProfileDetailModel{
		client:          client,
		tabs:            components.NewTabs(tabOverview, tabDifferences, tabBackups, tabHistory, tabTrash),
		spinner:         components.NewSpinner("Syncing"),
		diffTable:       components.NewTable(diffColumns(), 20),
		backupTable:     components.NewTable(backupColumns(), 10),
		snapshotTable:   components.NewTable(snapshotColumns(), 10),
		historyTable:    components.NewTable(jobColumns(false), jobsPageSize),
		historyFilesTbl: components.NewTable(fileChangeColumns(), 15),
		trash:           newTrashState(),
		loading:         true,
	}
}

// SetSlug selects the profile to show and clears data of the previous one.
func (m *ProfileDetailModel) SetSlug(slug string) {
	fresh := NewProfileDetailModel(m.client)
	fresh.width, fresh.height = m.width, m.height
	fresh.syncSeq = m.syncSeq
	fresh.running = m.running
	fresh.slug = slug
	*m = fresh
}

// Slug returns the slug of the profile shown.
func (m ProfileDetailModel) Slug() string { return m.slug }

func (m ProfileDetailModel) ViewID() ViewID { return ViewProfileDetail }

// CapturesInput is true while a form or prompt is open or a push/pull
// preview is running (Esc cancels it).
func (m ProfileDetailModel) CapturesInput() bool {
	return m.form.Active || m.confirm.Active || m.checking != nil
}

// KeyHints names the keys of the backup form and its folder picker for the
// bottom bar; elsewhere each tab lists its keys under its content.
func (m ProfileDetailModel) KeyHints() string {
	switch {
	case m.picker != nil:
		return m.picker.hints()
	case m.form.Active && m.form.ID == formCreateBackup:
		return formHints + "  Ctrl+O:browse folders"
	}
	return ""
}

func (m ProfileDetailModel) KeyBindings() []components.KeyBinding {
	common := []components.KeyBinding{
		{Key: "Left/Right", Desc: "Switch tab"},
		{Key: "o", Desc: "Overview tab"},
		{Key: "Esc", Desc: "Back"},
	}
	switch m.tabs.Active() {
	case tabDifferences:
		return append([]components.KeyBinding{
			{Key: "f", Desc: "Cycle filter"},
			{Key: "Space", Desc: "Select file"},
			{Key: "a", Desc: "Select all / none"},
			{Key: "p", Desc: "Push selected files (asks first)"},
			{Key: "l", Desc: "Pull selected files (asks first)"},
			{Key: "s", Desc: "Skip selected files"},
			{Key: "m", Desc: "Flag selected files for manual handling"},
			{Key: "n/N", Desc: "Next/previous page"},
			{Key: "r", Desc: "Reload differences"},
		}, common...)
	case tabBackups:
		if m.backupsView == backupsSubFiles {
			return append([]components.KeyBinding{
				{Key: "Enter", Desc: "Open folder"},
				{Key: "Backspace/u", Desc: "Parent folder (or leave the search)"},
				{Key: "Space", Desc: "Select file or folder"},
				{Key: "c", Desc: "Clear the selection"},
				{Key: "/", Desc: "Search the snapshot"},
				{Key: "R", Desc: "Restore the selection to its original place (asks first)"},
				{Key: "O", Desc: "Restore the selection into another folder"},
				{Key: "r", Desc: "Reload"},
			}, common...)
		}
		if m.backupsView == backupsSubSnapshots {
			return append([]components.KeyBinding{
				{Key: "Enter/r", Desc: "Restore snapshot (shows a preview, asks first)"},
				{Key: "f", Desc: "Browse the snapshot and restore single files"},
			}, common...)
		}
		return append([]components.KeyBinding{
			{Key: "c", Desc: "Create backup target"},
			{Key: "d", Desc: "Delete target (asks first)"},
			{Key: "t", Desc: "Toggle enabled"},
			{Key: "b", Desc: "Run backup now"},
			{Key: "Enter", Desc: "View snapshots"},
			{Key: "r", Desc: "Refresh"},
		}, common...)
	case tabHistory:
		return append(m.historyKeyBindings(), common...)
	case tabTrash:
		return append(m.trashKeyBindings(), common...)
	default:
		return append(m.overviewKeyBindings(), common...)
	}
}

// overviewKeyBindings lists the Overview keys for the profile's mode (both
// modes' keys while the profile is not loaded yet).
func (m ProfileDetailModel) overviewKeyBindings() []components.KeyBinding {
	twoWay := m.profile == nil || m.profile.TwoWay()
	mirror := m.profile == nil || !m.profile.TwoWay()
	var keys []components.KeyBinding
	if twoWay {
		keys = append(keys,
			components.KeyBinding{Key: "n", Desc: "Sync now: two-way sync (two-way profiles; asks first while syncs are paused)"},
			components.KeyBinding{Key: "R", Desc: "Resync: make both folders the union of both sides, nothing deleted (two-way profiles; asks first)"},
		)
	}
	if mirror {
		keys = append(keys,
			components.KeyBinding{Key: "w", Desc: "Switch this mirror profile to two-way sync (asks first)"},
			components.KeyBinding{Key: "h", Desc: "Hide or show the mirror-mode note (saved for this profile)"},
		)
	}
	pushDesc, pullDesc := "Push: make the remote match local (asks first)", "Pull: make local match the remote (asks first)"
	if m.profile != nil && m.profile.TwoWay() {
		pushDesc = "Push: one-way override, make the remote match local (asks first)"
		pullDesc = "Pull: one-way override, make local match the remote (asks first)"
	}
	return append(keys,
		components.KeyBinding{Key: "p", Desc: pushDesc},
		components.KeyBinding{Key: "l", Desc: pullDesc},
		components.KeyBinding{Key: "s", Desc: "Stop sync"},
		components.KeyBinding{Key: "k", Desc: "Show differences"},
		components.KeyBinding{Key: "i", Desc: "Resume intervals (also a pause you set with z)"},
		components.KeyBinding{Key: "z", Desc: "Pause automatic syncing of this profile until you resume (i); syncs you start still run"},
		components.KeyBinding{Key: "x", Desc: "Test sync"},
		components.KeyBinding{Key: "r", Desc: "Refresh"},
	)
}

// PollSpec returns the adaptive polling specification.
func (m ProfileDetailModel) PollSpec() *PollSpec {
	return &PollSpec{
		FastInterval: FastPollInterval,
		SlowInterval: SlowTickInterval,
		IsActive: func(data interface{}) bool {
			if d, ok := data.(ProfileDetailData); ok {
				data = d.Value
			}
			if ps, ok := data.(*api.ProfileStatusResponse); ok && ps != nil {
				return ps.State.Busy()
			}
			return m.syncing
		},
	}
}

func (m ProfileDetailModel) Init() tea.Cmd {
	if m.slug == "" {
		return nil
	}
	cmds := []tea.Cmd{m.fetchProfile()}
	if m.tabs.Active() == tabDifferences {
		cmds = append(cmds, m.fetchDiff())
	}
	if m.tabs.Active() == tabBackups {
		cmds = append(cmds, m.fetchBackups())
	}
	if m.tabs.Active() == tabHistory {
		cmds = append(cmds, m.fetchHistory())
	}
	if m.tabs.Active() == tabTrash {
		cmds = append(cmds, m.fetchTrash())
	}
	return tea.Batch(cmds...)
}

func (m ProfileDetailModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case OpenProfileMsg:
		if msg.Slug != m.slug {
			m.SetSlug(msg.Slug)
		}
		return m, nil

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.diffTable.SetWidth(msg.Width)
		m.backupTable.SetWidth(msg.Width)
		if m.files != nil {
			m.files.table.SetWidth(msg.Width)
		}
		m.historyTable.SetWidth(msg.Width)
		m.historyFilesTbl.SetWidth(msg.Width)
		m.trash.table.SetWidth(msg.Width)

	case PollResultMsg:
		return m.handlePollResult(msg)

	case ActionResultMsg:
		return m.handleActionResult(msg)

	case TickMsg:
		if m.slug == "" {
			return m, nil
		}
		if m.tabs.Active() == tabHistory && m.historyView == historySubList {
			return m, tea.Batch(m.fetchProfile(), m.fetchHistory())
		}
		return m, m.fetchProfile()

	case components.FormSubmitMsg:
		return m.handleFormSubmit(msg)

	case components.FormCancelMsg:
		m.pendingRestore = nil
		m.pendingFiles = nil
		m.picker = nil
		return m, nil

	case pickerChosenMsg:
		if m.picker != nil && m.form.Active {
			return m.applyPickedPath(msg)
		}
		return m, nil

	case components.ConfirmResultMsg:
		return m.handleConfirmResult(msg)

	case tea.PasteMsg:
		if m.form.Active && m.picker == nil {
			_, cmd := m.form.Update(msg)
			return m, cmd
		}

	case tea.KeyPressMsg:
		return m.handleKey(msg)

	default:
		if m.syncing {
			return m, m.spinner.Update(msg)
		}
	}
	return m, nil
}

func (m ProfileDetailModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	data := msg.Data
	if d, ok := data.(ProfileDetailData); ok {
		if d.Slug != m.slug {
			return m, nil // answer for a profile that is no longer shown
		}
		data = d.Value
	}

	switch data := data.(type) {
	case *api.ProfileStatusResponse:
		m.loading = false
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		if data == nil || (data.Slug != "" && m.slug != "" && data.Slug != m.slug) {
			return m, nil
		}
		m.err = nil
		m.profile = data
		return m.setSyncing(data.State.Busy())
	case *api.DiffResponse:
		m.diffLoading = false
		m.diffErr = msg.Err
		if msg.Err == nil && data != nil {
			m.diffData = data
			m.updateDiffTable()
		}
	case []api.BackupTargetResponse:
		m.backupsErr = msg.Err
		if msg.Err == nil {
			m.backupTargets = data
			m.backupsLoadedFor = m.slug
			m.updateBackupTable()
		}
	case []api.SnapshotResponse:
		m.backupsErr = msg.Err
		if msg.Err == nil {
			m.snapshots = data
			m.updateSnapshotTable()
		}
	case snapshotFilesPage:
		return m.handleSnapshotFilesPage(data, msg.Err), nil
	case historyPage, historyJob, historyFiles:
		return m.handleHistoryData(data, msg.Err), nil
	case trashListData:
		return m.handleTrashData(data, msg.Err), nil
	default:
		if msg.Err != nil {
			m.err = msg.Err
		}
	}
	return m, nil
}

// setSyncing updates the running state and reports a sync that finished.
func (m ProfileDetailModel) setSyncing(busy bool) (tea.Model, tea.Cmd) {
	was := m.syncing
	m.syncing = busy
	switch {
	case busy && !was:
		return m, m.spinner.Start()
	case !busy && was:
		m.spinner.Stop()
		if m.diffData != nil {
			return m, m.fetchDiff()
		}
	}
	return m, nil
}

func (m ProfileDetailModel) handleActionResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	switch msg.Action {
	case actionBrowse:
		if m.picker != nil {
			p := *m.picker
			p.handleAnswer(msg)
			m.picker = &p
		}
		return m, nil
	case "sync_preview":
		return m.handleSyncPreview(msg)
	case "restore_preview":
		if m.pendingFull == nil {
			return m, nil
		}
		preview, _ := msg.Data.(*api.RestorePreviewResponse)
		m.confirm = components.NewConfirm(restorePrompt(*m.pendingFull, preview, msg.Err), "restore")
		return m, nil
	case "trash_restore", "trash_delete":
		return m.handleTrashResult(msg)
	case actionBackupStarted:
		return m.handleBackupStarted(msg, actionBackupDone)
	case actionRestoreStarted:
		return m.handleBackupStarted(msg, actionRestoreDone)
	case actionRestoreFilesStarted:
		return m.handleBackupStarted(msg, actionRestoreFilesDone)
	case actionBackupDone, actionRestoreDone, actionRestoreFilesDone:
		return m.handleBackupDone(msg)
	case actionSelectiveStarted:
		return m.handleSelectiveStarted(msg)
	case actionSelectiveDone:
		return m.handleSelectiveDone(msg)
	case "pause_profile":
		if msg.Err != nil {
			return m, errorFlash("Pause failed", msg.Err)
		}
		return m, tea.Batch(m.fetchProfile(), flash("Automatic syncing paused; i resumes it", false))
	case "sync_done":
		out, _ := msg.Data.(syncAllOutcome)
		refresh := m.fetchProfile()
		if out.Slug == m.slug {
			m.syncing = false
			m.spinner.Stop()
		}
		verb := directionVerb(out.Direction)
		if msg.Err != nil {
			return m, tea.Batch(refresh, errorFlash(fmt.Sprintf("%s of %s failed", verb, out.Slug), msg.Err))
		}
		if out.FollowErr != nil {
			return m, tea.Batch(refresh, errorFlash(fmt.Sprintf("Lost track of the %s of %s; see the status", strings.ToLower(verb), out.Slug), out.FollowErr))
		}
		if failed, reason := out.failed(); failed {
			return m, tea.Batch(refresh, flash(fmt.Sprintf("%s of %s ended in an error: %s", verb, out.Slug, orSeeLastError(reason)), true))
		}
		return m, tea.Batch(refresh, flash(fmt.Sprintf("%s of %s finished", verb, out.Slug), false))
	case "resync_done":
		out, _ := msg.Data.(syncAllOutcome)
		refresh := m.fetchProfile()
		if out.Slug == m.slug {
			m.syncing = false
			m.spinner.Stop()
		}
		if msg.Err != nil {
			return m, tea.Batch(refresh, errorFlash(fmt.Sprintf("Resync of %s failed", out.Slug), msg.Err))
		}
		if out.FollowErr != nil {
			return m, tea.Batch(refresh, errorFlash(fmt.Sprintf("Lost track of the resync of %s; see the status", out.Slug), out.FollowErr))
		}
		if failed, reason := out.failed(); failed {
			return m, tea.Batch(refresh, flash(fmt.Sprintf("Resync of %s ended in an error: %s", out.Slug, orSeeLastError(reason)), true))
		}
		return m, tea.Batch(refresh, flash(fmt.Sprintf("Resync of %s finished; automatic two-way syncing resumes", out.Slug), false))
	case "mirror_notice":
		if msg.Err != nil {
			return m, errorFlash("Could not save the mirror-mode note setting", msg.Err)
		}
		text := "Mirror-mode note shown again"
		if hidden, _ := msg.Data.(bool); hidden {
			text = "Mirror-mode note hidden for this profile; h shows it again"
		}
		return m, tea.Batch(m.fetchProfile(), flash(text, false))
	case "switch_two_way":
		if msg.Err != nil {
			return m, tea.Batch(m.fetchProfile(), errorFlash("Switching to two-way failed", msg.Err))
		}
		return m, tea.Batch(m.fetchProfile(),
			flash("The profile now syncs two-way. The next sync is a resync: the union of both folders, nothing deleted", false))
	}

	if msg.Err != nil {
		return m, errorFlash("Error", msg.Err)
	}

	switch msg.Action {
	case "stop":
		m.syncing = false
		m.spinner.Stop()
		return m, tea.Batch(m.fetchProfile(), flash("Sync stopped", false))
	case "resume_intervals":
		return m, tea.Batch(m.fetchProfile(), flash("Intervals resumed", false))
	case "test_sync":
		if result, ok := msg.Data.(*api.TestSyncResponse); ok && result != nil && !result.Success {
			text := "Test sync failed"
			if result.Error != nil {
				text += ": " + *result.Error
			}
			return m, flash(text, true)
		}
		return m, flash("Test sync passed", false)
	case "delete_backup":
		return m, tea.Batch(m.fetchBackups(), flash("Backup target deleted", false))
	case "create_backup":
		return m, tea.Batch(m.fetchBackups(), flash("Backup target created", false))
	case "toggle_backup":
		return m, m.fetchBackups()
	}
	return m, nil
}

func (m ProfileDetailModel) handleFormSubmit(msg components.FormSubmitMsg) (tea.Model, tea.Cmd) {
	switch msg.FormID {
	case formCreateBackup:
		return m, m.createBackup(msg.Values)
	case formRestore:
		target := m.pendingRestore
		m.pendingRestore = nil
		if target != nil {
			m.pendingFull = &fullRestore{target: *target, scope: api.RestoreScope(msg.Values["restore_scope"])}
			return m, tea.Batch(flash("Checking what the restore would change...", false), m.previewRestore(*m.pendingFull))
		}
	case formSnapshotSearch:
		if m.files != nil {
			return m.browseTo(m.files.path, strings.TrimSpace(msg.Values["search"]))
		}
	case formRestoreFilesTo:
		req := m.pendingFiles
		m.pendingFiles = nil
		if req != nil {
			dir := strings.TrimSpace(msg.Values["target_dir"])
			req.targetDir = &dir
			return m, m.restoreFiles(*req)
		}
	}
	return m, nil
}

func (m ProfileDetailModel) handleConfirmResult(msg components.ConfirmResultMsg) (tea.Model, tea.Cmd) {
	switch msg.Tag {
	case "trash_delete", "trash_overwrite":
		return m.handleTrashConfirm(msg)
	case "sync":
		req := m.pendingSync
		m.pendingSync = nil
		if req == nil {
			return m, nil
		}
		if !msg.Confirmed {
			return m, flash(directionVerb(req.dir)+" cancelled", false)
		}
		m.syncing = true
		return m, tea.Batch(
			m.spinner.Start(),
			startConfirmedSyncCmd(m.client, ViewProfileDetail, "sync_done", req.slug, req.dir),
			flash(fmt.Sprintf("%s of %s started", directionVerb(req.dir), req.name), false),
		)
	case "resync":
		req := m.pendingResync
		m.pendingResync = nil
		if req == nil {
			return m, nil
		}
		if !msg.Confirmed {
			return m, flash("Resync cancelled; nothing was changed", false)
		}
		m.syncing = true
		return m, tea.Batch(
			m.spinner.Start(),
			resyncCmd(m.client, req.slug),
			flash(fmt.Sprintf("Resync of %s started", req.name), false),
		)
	case "switch_two_way":
		req := m.pendingSwitch
		m.pendingSwitch = nil
		if req == nil {
			return m, nil
		}
		if !msg.Confirmed {
			return m, flash("The profile stays a mirror", false)
		}
		return m, switchTwoWayCmd(m.client, req.slug)
	case "selective":
		items := m.pendingSel
		m.pendingSel = nil
		if msg.Confirmed && len(items) > 0 {
			return m, m.selectiveSync(items)
		}
	case "delete_backup":
		id := m.pendingDeleteID
		m.pendingDeleteID = 0
		if msg.Confirmed && id > 0 {
			return m, m.deleteBackup(id)
		}
	case "restore":
		req := m.pendingFull
		m.pendingFull = nil
		if req == nil {
			return m, nil
		}
		if !msg.Confirmed {
			return m, flash("Restore cancelled; nothing was changed", false)
		}
		return m, m.restoreSnapshot(req.target, req.scope)
	case "restore_files":
		req := m.pendingFiles
		m.pendingFiles = nil
		if req != nil && msg.Confirmed {
			return m, m.restoreFiles(*req)
		}
	}
	return m, nil
}

func (m ProfileDetailModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.form.Active && m.form.ID == formCreateBackup {
		return m.handleBackupFormKey(msg)
	}
	if m.form.Active {
		_, cmd := m.form.Update(msg)
		return m, cmd
	}
	if m.confirm.Active {
		_, cmd := m.confirm.Update(msg)
		return m, cmd
	}
	if m.checking != nil {
		if msg.String() == "esc" {
			dir := m.checking.dir
			m.checking = nil
			return m, flash(directionVerb(dir)+" cancelled", false)
		}
		return m, nil
	}

	switch msg.String() {
	case "right":
		m.tabs.Next()
		return m, m.onTabChange()
	case "left":
		m.tabs.Prev()
		return m, m.onTabChange()
	case "o":
		m.tabs.SetActive(0)
		return m, nil
	case "esc":
		if m.tabs.Active() == tabBackups && m.backupsView == backupsSubFiles {
			m.backupsView = backupsSubSnapshots
			m.files = nil
			return m, nil
		}
		if m.tabs.Active() == tabBackups && m.backupsView == backupsSubSnapshots {
			m.backupsView = backupsSubTargets
			return m, nil
		}
		if m.tabs.Active() == tabHistory && m.historyView == historySubDetail {
			m.historyView = historySubList
			return m, nil
		}
		return m, func() tea.Msg {
			return NavigateMsg{Target: ViewProfiles}
		}
	}

	switch m.tabs.Active() {
	case tabDifferences:
		return m.handleDiffKey(msg)
	case tabBackups:
		return m.handleBackupsKey(msg)
	case tabHistory:
		return m.handleHistoryKey(msg)
	case tabTrash:
		return m.handleTrashKey(msg)
	default:
		return m.handleOverviewKey(msg)
	}
}

// onTabChange loads a tab's data the first time it is shown.
func (m *ProfileDetailModel) onTabChange() tea.Cmd {
	switch m.tabs.Active() {
	case tabDifferences:
		if m.diffData == nil && !m.diffLoading {
			m.diffLoading = true
			return m.fetchDiff()
		}
	case tabBackups:
		if m.backupsLoadedFor != m.slug {
			return m.fetchBackups()
		}
	case tabHistory:
		if m.historyLoadedFor != m.slug && !m.historyLoading {
			m.historyLoading = true
			return m.fetchHistory()
		}
	case tabTrash:
		if m.trash.loadedFor != m.slug+"/"+string(m.trash.side) && !m.trash.loading {
			m.trash.loading = true
			return m.fetchTrash()
		}
	}
	return nil
}

func (m ProfileDetailModel) handleOverviewKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "p":
		return m.askSync(api.SyncDirectionPush)
	case "l":
		return m.askSync(api.SyncDirectionPull)
	case "n":
		return m.syncNow()
	case "R":
		return m.askResync()
	case "w":
		return m.askSwitchTwoWay()
	case "h":
		return m.toggleMirrorNotice()
	case "s":
		return m, m.stopSync()
	case "k":
		m.tabs.SetActive(1)
		m.diffLoading = true
		return m, m.fetchDiff()
	case "i":
		return m, m.resumeIntervals()
	case "z":
		return m, m.pauseProfile()
	case "x":
		return m, tea.Batch(flash("Running test sync...", false), m.testSync())
	case "r":
		m.loading = true
		return m, m.fetchProfile()
	}
	return m, nil
}

// askSync starts a push/pull request: it captures the profile and direction
// and asks the backend for a preview of what the sync would change (the
// preview changes nothing on the server). Nothing is synced until the user
// answers 'y' in the prompt that follows.
func (m ProfileDetailModel) askSync(dir api.SyncDirection) (tea.Model, tea.Cmd) {
	if m.profile == nil || m.slug == "" {
		return m, flash("Profile not loaded yet", true)
	}
	if m.syncing {
		return m, flash("A sync is already running for this profile", true)
	}
	m.syncSeq++
	req := m.newRequest(dir)
	m.checking = req
	client, slug, seq := m.client, req.slug, req.seq
	return m, func() tea.Msg {
		resp, err := client.PreviewProfileSync(context.Background(), slug)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "sync_preview", Err: err,
			Data: syncPreviewResult{seq: seq, resp: resp}}
	}
}

func (m ProfileDetailModel) handleSyncPreview(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	res, _ := msg.Data.(syncPreviewResult)
	req := m.checking
	if req == nil || res.seq != req.seq {
		return m, nil // cancelled, or an old preview
	}
	m.checking = nil
	m.pendingSync = req
	prompt := syncPrompt(req, res.resp, msg.Err)
	if syncPreviewFailed(req.dir, res.resp, msg.Err) {
		// The confirmed sync is sent with force: without a preview the user
		// has to say explicitly that they accept not knowing what it changes.
		m.confirm = components.NewConfirmWord(prompt, "sync", ForceWord, ForceWordHint)
	} else {
		m.confirm = components.NewConfirm(prompt, "sync")
	}
	return m, nil
}

// ForceWord is what the user types to confirm a forced sync whose preview
// failed; ForceWordHint completes the dialog's instruction.
const (
	ForceWord     = "force"
	ForceWordHint = "to sync without a preview"
)

// syncPreviewFailed reports whether the preview of a sync in dir is missing
// or incomplete, like the web UI's confirmation: the request failed, the
// comparison reported a problem, or a two-way preview has no usable two_way
// part.
func syncPreviewFailed(dir api.SyncDirection, preview *api.SyncPreviewResponse, err error) bool {
	if err != nil || preview == nil {
		return true
	}
	if preview.Error != nil && *preview.Error != "" {
		return true
	}
	if dir == api.SyncDirectionTwoWay {
		return preview.TwoWay == nil || (preview.TwoWay.Error != nil && *preview.TwoWay.Error != "")
	}
	return false
}

// newRequest captures the shown profile for an action in direction dir.
func (m ProfileDetailModel) newRequest(dir api.SyncDirection) *syncRequest {
	return &syncRequest{
		seq: m.syncSeq, slug: m.slug, name: m.profile.Name, dir: dir,
		localDir: m.profile.LocalDir, remoteDir: m.profile.RemoteDir,
		maxDelete: m.profile.MaxDelete, paused: m.profile.IntervalsPaused,
		twoWay: m.profile.TwoWay(), lastError: m.lastError(),
	}
}

// syncNow runs a two-way sync of a two-way profile ("Sync now"). While the
// profile's automatic syncs are paused it previews and asks first, and the
// confirmed sync is sent with force=true; otherwise it starts right away
// without force.
func (m ProfileDetailModel) syncNow() (tea.Model, tea.Cmd) {
	if m.profile == nil || m.slug == "" {
		return m, flash("Profile not loaded yet", true)
	}
	if !m.profile.TwoWay() {
		return m, flash("Sync now is for two-way profiles. This profile mirrors: use p (push) or l (pull), or w to switch it to two-way", true)
	}
	if m.profile.ResyncRequired {
		return m, flash("This profile needs a resync first: press R", true)
	}
	if m.syncing {
		return m, flash("A sync is already running for this profile", true)
	}
	if m.profile.IntervalsPaused {
		return m.askSync(api.SyncDirectionTwoWay)
	}
	m.syncing = true
	return m, tea.Batch(
		m.spinner.Start(),
		startSyncCmd(m.client, ViewProfileDetail, "sync_done", m.slug, api.SyncDirectionTwoWay, false),
		flash(fmt.Sprintf("Two-way sync of %s started", m.profile.Name), false),
	)
}

// askResync opens the Resync confirmation of a two-way profile.
func (m ProfileDetailModel) askResync() (tea.Model, tea.Cmd) {
	if m.profile == nil || m.slug == "" {
		return m, flash("Profile not loaded yet", true)
	}
	if !m.profile.TwoWay() {
		return m, flash("Resync is for two-way profiles; press w to switch this profile to two-way", true)
	}
	if m.syncing {
		return m, flash("A sync is already running for this profile", true)
	}
	req := m.newRequest(api.SyncDirectionTwoWay)
	m.pendingResync = req
	m.confirm = components.NewConfirm(resyncPrompt(req, m.profile.ResyncRequired), "resync")
	return m, nil
}

// askSwitchTwoWay offers to switch a mirror profile to two-way sync.
func (m ProfileDetailModel) askSwitchTwoWay() (tea.Model, tea.Cmd) {
	if m.profile == nil || m.slug == "" {
		return m, flash("Profile not loaded yet", true)
	}
	if m.profile.TwoWay() {
		return m, flash("This profile already syncs two-way", false)
	}
	req := m.newRequest(api.SyncDirectionTwoWay)
	m.pendingSwitch = req
	m.confirm = components.NewConfirm(switchTwoWayPrompt(req), "switch_two_way")
	return m, nil
}

// toggleMirrorNotice hides the mirror-mode note of a mirror profile, or
// shows it again. The choice is stored on the profile.
func (m ProfileDetailModel) toggleMirrorNotice() (tea.Model, tea.Cmd) {
	if m.profile == nil || m.slug == "" {
		return m, flash("Profile not loaded yet", true)
	}
	if m.profile.TwoWay() {
		return m, flash("This profile syncs two-way; the mirror-mode note is only for mirror profiles", false)
	}
	return m, setMirrorNoticeCmd(m.client, m.slug, !m.profile.MirrorNoticeDismissed)
}

// resyncPrompt explains what a resync does.
func resyncPrompt(req *syncRequest, required bool) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Resync profile %q (%s)?\n\n", req.name, req.slug)
	fmt.Fprintf(&b, "Local:  %s\nRemote: %s\n\n", req.localDir, req.remoteDir)
	b.WriteString("A resync makes both folders the union of both sides:\n")
	b.WriteString("  - files that are only in one folder are copied to the other;\n    nothing is deleted\n")
	b.WriteString("  - where a file differs, the newer version wins; the older one is moved\n    to .omnisync-trash on its side\n")
	b.WriteString("Afterwards automatic two-way syncing resumes.")
	if required {
		b.WriteString("\n\nThis profile needs a resync: its two-way sync state was lost or is\ninconsistent, so automatic syncing is paused.")
		if req.lastError != "" {
			fmt.Fprintf(&b, "\nReason: %s", req.lastError)
		}
	}
	return b.String()
}

// switchTwoWayPrompt explains what switching a mirror profile to two-way
// does.
func switchTwoWayPrompt(req *syncRequest) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Switch profile %q (%s) to two-way sync?\n\n", req.name, req.slug)
	b.WriteString("Two-way sync carries changes on either side to the other: edits made on\n")
	b.WriteString("either side are kept, new files on both sides are kept, and deletions are\n")
	b.WriteString("carried over (the deleted file goes to .omnisync-trash). A file changed on\n")
	b.WriteString("both sides keeps both versions and is listed under Conflicts.\n\n")
	b.WriteString("The first two-way sync is a resync: both folders become the union of both\n")
	b.WriteString("sides and nothing is deleted (where a file differs, the newer version wins\n")
	b.WriteString("and the older goes to .omnisync-trash).\n\n")
	b.WriteString("You can switch back to mirror in the Profiles view (e: edit).")
	return b.String()
}

// syncPrompt describes what a push or pull would do, from the preview.
func syncPrompt(req *syncRequest, preview *api.SyncPreviewResponse, previewErr error) string {
	if req.dir == api.SyncDirectionTwoWay {
		return twoWayPrompt(req, preview, previewErr)
	}
	var b strings.Builder
	arrow := theme.Glyphs().Arrow
	verb := directionVerb(req.dir)
	fmt.Fprintf(&b, "%s profile %q (%s)?\n\n", verb, req.name, req.slug)
	if req.dir == api.SyncDirectionPush {
		fmt.Fprintf(&b, "Direction: local %s %s remote %s\n\n", req.localDir, arrow, req.remoteDir)
	} else {
		fmt.Fprintf(&b, "Direction: remote %s %s local %s\n\n", req.remoteDir, arrow, req.localDir)
	}
	if req.twoWay {
		if req.dir == api.SyncDirectionPush {
			b.WriteString("This profile syncs two-way. A push is a one-way override: changes that\nexist only on the remote are replaced or deleted.\n\n")
		} else {
			b.WriteString("This profile syncs two-way. A pull is a one-way override: changes that\nexist only in the local folder are replaced or deleted.\n\n")
		}
	}

	maxDelete := req.maxDelete
	var counts *api.SyncPreviewCounts
	switch {
	case previewErr != nil:
		fmt.Fprintf(&b, "Could not count the files this would change: %s\n", previewErr.Error())
	case preview == nil:
		b.WriteString("Could not count the files this would change.\n")
	default:
		maxDelete = preview.MaxDelete
		c := preview.Counts(req.dir)
		counts = &c
		if preview.Error != nil && *preview.Error != "" {
			fmt.Fprintf(&b, "The comparison reported a problem: %s\n", *preview.Error)
		}
		if req.dir == api.SyncDirectionPush {
			fmt.Fprintf(&b, "Deletes %d file(s) on the remote that are not in the local folder.\n", c.Deletes)
			fmt.Fprintf(&b, "Replaces %d changed file(s) on the remote.\n", c.Replaces)
			fmt.Fprintf(&b, "Uploads %d new file(s).\n", c.Creates)
		} else {
			fmt.Fprintf(&b, "Deletes %d local file(s) that are not on the remote.\n", c.Deletes)
			fmt.Fprintf(&b, "Replaces %d changed local file(s).\n", c.Replaces)
			fmt.Fprintf(&b, "Downloads %d new file(s).\n", c.Creates)
		}
		if preview.Excluded > 0 {
			fmt.Fprintf(&b, "Leaves out %d differing file(s) flagged for manual handling or in an\nunresolved conflict.\n", preview.Excluded)
		}
	}
	side := "remote"
	if req.dir == api.SyncDirectionPull {
		side = "local"
	}
	fmt.Fprintf(&b, "\nFiles it deletes or replaces are kept in .omnisync-trash on the %s side.\n", side)
	b.WriteString(deleteLimitSentence(maxDelete))
	if counts != nil && counts.ExceedsMaxDelete && maxDelete != nil {
		fmt.Fprintf(&b, "\n\n%s This %s would delete %d files, more than the limit of %d:\nit will stop at the limit and delete no more files.",
			theme.Glyphs().Warning, strings.ToLower(verb), counts.Deletes, *maxDelete)
	}
	if req.paused {
		b.WriteString("\n\nAutomatic syncs of this profile are paused (unresolved differences);\nthis sync runs anyway.")
	}
	return b.String()
}

// twoWayPrompt describes what a two-way sync would do to each folder, from
// the preview's two_way part.
func twoWayPrompt(req *syncRequest, preview *api.SyncPreviewResponse, previewErr error) string {
	var b strings.Builder
	warning := theme.Glyphs().Warning
	fmt.Fprintf(&b, "Sync profile %q (%s) both ways now?\n\n", req.name, req.slug)
	fmt.Fprintf(&b, "Local:  %s\nRemote: %s\n\n", req.localDir, req.remoteDir)

	maxDelete := req.maxDelete
	var tw *api.TwoWayPreview
	switch {
	case previewErr != nil:
		fmt.Fprintf(&b, "Could not count the files this would change: %s\n", previewErr.Error())
	case preview == nil || preview.TwoWay == nil:
		b.WriteString("Could not count the files this would change (no two-way preview).\n")
	case preview.TwoWay.Error != nil && *preview.TwoWay.Error != "":
		maxDelete = preview.MaxDelete
		fmt.Fprintf(&b, "Could not preview the two-way sync: %s\n", *preview.TwoWay.Error)
	default:
		maxDelete = preview.MaxDelete
		tw = preview.TwoWay
		if tw.Resync {
			b.WriteString("This run is a resync: both folders become the union of both sides and\nnothing is deleted (where a file differs, the newer version wins).\n")
		}
		b.WriteString(twoWaySideLine("Local folder:", tw.Local, maxDelete))
		b.WriteString(twoWaySideLine("Remote:      ", tw.Remote, maxDelete))
		if tw.Conflicts > 0 {
			fmt.Fprintf(&b, "%d file(s) changed on both sides: both versions will be kept and listed\nunder Conflicts.\n", tw.Conflicts)
		}
	}

	b.WriteString("\nFiles it deletes or replaces are kept in .omnisync-trash on the side that\nchanges.\n")
	if maxDelete == nil {
		b.WriteString("This profile has no delete limit.")
	} else {
		fmt.Fprintf(&b, "Delete limit: %d files on each side, checked before anything changes:\na sync that would delete more on one side changes nothing and pauses\nthe profile.", *maxDelete)
	}
	if tw != nil && maxDelete != nil && (tw.Local.ExceedsMaxDelete || tw.Remote.ExceedsMaxDelete) {
		fmt.Fprintf(&b, "\n\n%s This sync would delete more files than the limit of %d on one side:\nit will change nothing and pause the profile.", warning, *maxDelete)
	}
	if tw != nil && tw.ResyncRequired {
		fmt.Fprintf(&b, "\n\n%s This profile needs a resync first (press R); this sync will be refused.", warning)
	}
	if req.paused {
		b.WriteString("\n\nAutomatic syncs of this profile are paused; this sync runs anyway.")
		if req.lastError != "" {
			fmt.Fprintf(&b, "\nWhy they are paused: %s", req.lastError)
		}
	}
	return b.String()
}

// twoWaySideLine renders what a two-way sync would change in one folder.
func twoWaySideLine(label string, c api.SyncPreviewCounts, maxDelete *int) string {
	line := fmt.Sprintf("%s deletes %d, replaces %d, creates %d file(s)", label, c.Deletes, c.Replaces, c.Creates)
	if c.ExceedsMaxDelete && maxDelete != nil {
		line += fmt.Sprintf("  %s more deletes than the limit of %d", theme.Glyphs().Warning, *maxDelete)
	}
	return line + "\n"
}

func (m ProfileDetailModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder

	name := m.slug
	if m.profile != nil {
		name = fmt.Sprintf("%s (%s)", m.profile.Name, m.slug)
	}
	b.WriteString(headerText("  " + name))
	b.WriteString("\n  ")
	b.WriteString(m.tabs.View())
	b.WriteString("\n\n")

	if m.picker != nil && m.form.Active {
		b.WriteString(m.picker.View())
		return tea.NewView(b.String())
	}
	if m.form.Active {
		b.WriteString(m.form.View())
		return tea.NewView(b.String())
	}
	if m.confirm.Active {
		b.WriteString(m.confirm.View())
		return tea.NewView(b.String())
	}
	if m.checking != nil {
		b.WriteString(fmt.Sprintf("  Counting what a %s of %q would change (nothing is changed yet)...\n\n", strings.ToLower(directionVerb(m.checking.dir)), m.checking.name))
		b.WriteString(mutedText("  Esc: cancel"))
		return tea.NewView(b.String())
	}

	if m.err != nil {
		b.WriteString(errorLine(m.err))
	}

	if m.loading && m.profile == nil {
		b.WriteString("  Loading...")
		return tea.NewView(b.String())
	}

	switch m.tabs.Active() {
	case tabDifferences:
		b.WriteString(m.renderDiffTab())
	case tabBackups:
		b.WriteString(m.renderBackupsTab())
	case tabHistory:
		b.WriteString(m.renderHistoryTab())
	case tabTrash:
		b.WriteString(m.renderTrashTab())
	default:
		b.WriteString(m.renderOverviewTab())
	}

	return tea.NewView(b.String())
}

// lastError returns why the last sync failed, if the backend said.
func (m ProfileDetailModel) lastError() string {
	if m.profile != nil && m.profile.LastError != nil {
		return *m.profile.LastError
	}
	return ""
}

func (m ProfileDetailModel) renderOverviewTab() string {
	if m.profile == nil {
		return "  No data"
	}
	p := m.profile
	valueStyle := lipgloss.NewStyle().Foreground(theme.Current.Foreground)
	warnStyle := lipgloss.NewStyle().Foreground(theme.Current.Warning)
	errStyle := lipgloss.NewStyle().Foreground(theme.Current.Error).Bold(true)

	var b strings.Builder

	b.WriteString(fmt.Sprintf("  State: %s", components.RenderBadge(string(p.State))))
	if m.syncing {
		b.WriteString("  " + m.spinner.View())
	}
	b.WriteString("\n")
	if line := progressLine(p.Progress); line != "" && p.State.Busy() {
		b.WriteString("  Progress: " + valueStyle.Render(line) + "\n")
		b.WriteString(progressFiles(p.Progress, "  "))
	}
	if p.ResyncRequired {
		reason := m.lastError()
		if reason == "" {
			reason = "no reason reported"
		}
		b.WriteString(errStyle.Render(fmt.Sprintf("  %s Resync required: automatic two-way syncing is paused.", theme.Glyphs().Warning)))
		b.WriteString("\n")
		b.WriteString(errStyle.Render("    Why: " + reason))
		b.WriteString("\n")
		b.WriteString(warnStyle.Render("    Press R to resync: both folders become the union of both sides; nothing is deleted."))
		b.WriteString("\n")
	} else if reason := m.lastError(); reason != "" {
		label := "Last error"
		if p.State == api.SyncStateError {
			b.WriteString(errStyle.Render(fmt.Sprintf("  %s: %s", label, reason)))
		} else {
			b.WriteString(mutedText(fmt.Sprintf("  %s: %s", label, reason)))
		}
		b.WriteString("\n")
	}

	enabled := "yes"
	if !p.Enabled {
		enabled = "no (sync engine stopped)"
	}
	b.WriteString(fmt.Sprintf("  Mode: %s\n", valueStyle.Render(syncModeSummary(p.SyncMode))))
	b.WriteString(fmt.Sprintf("  Enabled: %s\n", valueStyle.Render(enabled)))
	b.WriteString(fmt.Sprintf("  Local:  %s\n", valueStyle.Render(p.LocalDir)))
	b.WriteString(fmt.Sprintf("  Remote: %s\n", valueStyle.Render(p.RemoteDir)))
	b.WriteString(fmt.Sprintf("  Last sync: %s\n", mutedText(formatTimePtr(p.LastSync, "never"))))
	b.WriteString(fmt.Sprintf("  Files: %s  Errors: %s  Pending: %s\n",
		valueStyle.Render(fmt.Sprintf("%d", p.FilesProcessed)),
		valueStyle.Render(fmt.Sprintf("%d", p.Errors)),
		valueStyle.Render(fmt.Sprintf("%d", p.PendingChanges)),
	))
	limit := deleteLimitShort(p.MaxDelete)
	if p.TwoWay() && p.MaxDelete != nil {
		limit += " on each side (checked before a two-way sync changes anything)"
	}
	b.WriteString(fmt.Sprintf("  Delete limit: %s\n", valueStyle.Render(limit)))
	if p.Bwlimit != nil && *p.Bwlimit != "" {
		b.WriteString(fmt.Sprintf("  Bandwidth limit: %s\n", valueStyle.Render(*p.Bwlimit)))
	}
	if p.SyncWindow != nil {
		b.WriteString(fmt.Sprintf("  Sync window: %s (server time; automatic syncs only)\n", valueStyle.Render(formatSyncWindow(p.SyncWindow))))
	}
	if status := windowStatus(p.OutsideSyncWindow, p.WaitingForWindow, p.NextWindowStart); status != "" {
		b.WriteString(mutedText("  "+status) + "\n")
	}

	if p.UserPaused && p.PendingChanges == 0 && !p.ResyncRequired {
		b.WriteString("\n")
		b.WriteString(warnStyle.Render(fmt.Sprintf("  %s Paused by you %s press 'i' to resume (syncs you start still run)", theme.Glyphs().Warning, theme.Glyphs().EmDash)))
		b.WriteString("\n")
	} else if p.IntervalsPaused {
		b.WriteString("\n")
		b.WriteString(warnStyle.Render(fmt.Sprintf("  %s Intervals are paused %s press 'i' to resume", theme.Glyphs().Warning, theme.Glyphs().EmDash)))
		b.WriteString("\n")
	}

	if p.ShowMirrorNotice() {
		b.WriteString("\n")
		b.WriteString(renderMirrorNotice())
	}

	b.WriteString("\n")
	if p.TwoWay() {
		b.WriteString(mutedText("  n:sync now  R:resync  p:push  l:pull  s:stop  k:differences  z:pause  i:resume  x:test  r:refresh  Esc:back"))
	} else {
		b.WriteString(mutedText("  p:push  l:pull  w:switch to two-way  h:mirror note  s:stop  k:differences  z:pause  i:resume  x:test  r:refresh  Esc:back"))
	}

	return b.String()
}

// --- API commands ---

func (m ProfileDetailModel) fetchProfile() tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		data, err := client.GetProfile(context.Background(), slug)
		return PollResultMsg{ViewID: ViewProfileDetail, Data: ProfileDetailData{Slug: slug, Value: data}, Err: err}
	}
}

// resyncCmd runs POST /profiles/{slug}/sync/resync with confirm=true and
// follows the resync job until it ends. Use it only after the user
// confirmed the resync.
func resyncCmd(client *api.Client, slug string) tea.Cmd {
	return func() tea.Msg {
		resp, err := client.ResyncProfile(context.Background(), slug)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "resync_done", Err: err,
			Data: followSync(client, slug, api.SyncDirectionTwoWay, resp, err)}
	}
}

// orSeeLastError is reason, or a pointer to the Last error line.
func orSeeLastError(reason string) string {
	if reason == "" {
		return "see Last error"
	}
	return reason
}

// switchTwoWayCmd switches a profile to two-way sync (PUT sync_mode only).
func switchTwoWayCmd(client *api.Client, slug string) tea.Cmd {
	return func() tea.Msg {
		mode := api.SyncModeTwoWay
		_, err := client.UpdateProfile(context.Background(), slug, api.ProfileUpdateRequest{SyncMode: &mode})
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "switch_two_way", Err: err}
	}
}

func (m ProfileDetailModel) stopSync() tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		_, err := client.StopProfileSync(context.Background(), slug)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "stop", Err: err}
	}
}

func (m ProfileDetailModel) resumeIntervals() tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		_, err := client.ResumeProfileIntervals(context.Background(), slug)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "resume_intervals", Err: err}
	}
}

func (m ProfileDetailModel) pauseProfile() tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		_, err := client.PauseProfile(context.Background(), slug)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "pause_profile", Err: err}
	}
}

func (m ProfileDetailModel) testSync() tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		data, err := client.TestProfileSync(context.Background(), slug)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "test_sync", Err: err, Data: data}
	}
}

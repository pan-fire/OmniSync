package ui

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	tea "charm.land/bubbletea/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

type backupsSubView int

const (
	backupsSubTargets backupsSubView = iota
	backupsSubSnapshots
	backupsSubFiles
)

const (
	formCreateBackup = "create_backup"
	formRestore      = "restore"
)

// restoreTarget is the snapshot a restore form acts on, captured when the
// form opened.
type restoreTarget struct {
	targetID   int
	snapshotID string
}

// fullRestore is a full restore waiting for its preview and confirmation.
type fullRestore struct {
	target restoreTarget
	scope  api.RestoreScope
}

func backupColumns() []components.Column {
	return []components.Column{
		{Title: "Name", Width: 16},
		{Title: "Path", Width: 22},
		{Title: "Type", Width: 13},
		{Title: "Mode", Width: 8},
		{Title: "Last", Width: 10},
		{Title: "Enc", Width: 4},
		{Title: "Enabled", Width: 8},
		{Title: "Next run", Width: 19},
	}
}

func snapshotColumns() []components.Column {
	return []components.Column{
		{Title: "Snapshot", Width: 24},
		{Title: "Created", Width: 19},
		{Title: "Size", Width: 12},
		{Title: "Status", Width: 10},
		{Title: "Kind", Width: 17},
	}
}

// snapshotKindLabel names a snapshot's kind as `osync backups snapshots`
// does: "full" or "legacy" (a mirror version from before snapshot manifests,
// which restores the files as they were before that backup and removes
// nothing), with " (latest)" on the target's most recent backup.
func snapshotKindLabel(s api.SnapshotResponse) string {
	kind := s.Kind
	if kind == "" {
		kind = "full"
	}
	if s.Latest {
		kind += " (latest)"
	}
	return kind
}

func (m ProfileDetailModel) handleBackupsKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.backupsView == backupsSubFiles {
		return m.handleSnapshotFilesKey(msg)
	}
	if m.backupsView == backupsSubSnapshots {
		switch msg.String() {
		case "f":
			if row := m.snapshotTable.SelectedRow(); row != nil {
				return m.openSnapshotBrowser(row.Key)
			}
		case "r", "enter":
			if row := m.snapshotTable.SelectedRow(); row != nil {
				m.pendingRestore = &restoreTarget{targetID: m.snapshotTarget, snapshotID: row.Key}
				f := components.NewFormWithID(formRestore, "Restore Snapshot", []components.Field{
					{Name: "restore_scope", Label: "Restore to", Type: components.FieldDropdown, Value: string(api.RestoreScopeBoth),
						Options: []string{string(api.RestoreScopeLocalOnly), string(api.RestoreScopeRemoteOnly), string(api.RestoreScopeBoth)}},
				})
				f.Intro = fmt.Sprintf("Snapshot %s of backup target %d.\nRestoring overwrites the chosen side(s) of this profile with the snapshot.\n"+
					"Next, a preview shows what would change before anything is restored.", row.Key, m.snapshotTarget)
				if m.snapshotKind(row.Key) == "legacy" {
					f.Intro += "\nThis snapshot is in an older backup format: it brings files back as they were before " +
						"that backup and removes nothing."
				}
				m.form = f
			}
		default:
			m.snapshotTable.Update(msg)
		}
		return m, nil
	}

	switch msg.String() {
	case "c":
		m.form = components.NewFormWithID(formCreateBackup, "Create Backup Target", []components.Field{
			{Name: "name", Label: "Name", Type: components.FieldText, Required: true},
			{Name: "target_type", Label: "Type", Type: components.FieldDropdown, Value: string(api.BackupTargetLocal),
				Options: []string{string(api.BackupTargetLocal), string(api.BackupTargetRemote), string(api.BackupTargetCustomRemote)}},
			{Name: "remote_name", Label: "Remote name", Type: components.FieldText,
				Help: "Only for custom_remote targets; remote uses the profile's remote"},
			{Name: "target_path", Label: "Path", Type: components.FieldText, Required: true,
				Help: "A folder, or remote:path for remote targets (Ctrl+O: browse)"},
			{Name: "backup_mode", Label: "Mode", Type: components.FieldDropdown, Value: string(api.BackupModeMirror),
				Options: []string{string(api.BackupModeMirror), string(api.BackupModeArchive)}},
			{Name: "frequency_hours", Label: "Every (hours)", Type: components.FieldText, Value: "24"},
			{Name: "retention_days", Label: "Keep (days)", Type: components.FieldText, Value: "7"},
			{Name: "keep_last", Label: "Keep at least", Type: components.FieldText, Value: "3",
				Help: "The newest snapshots retention never deletes, however old"},
			{Name: "verify", Label: "Verify", Type: components.FieldDropdown, Value: "yes", Options: []string{"yes", "no"},
				Help: "Compare the backup with the folder after each run"},
			{Name: "passphrase", Label: "Passphrase", Type: components.FieldPassword,
				Help: "Optional: encrypts the backup. Lost passphrase = lost backups; it cannot be changed later"},
			{Name: "passphrase2", Label: "Repeat", Type: components.FieldPassword},
		})
		return m, nil
	case "d":
		if bt := m.selectedTarget(); bt != nil {
			m.pendingDeleteID = bt.ID
			m.confirm = components.NewConfirm(
				fmt.Sprintf("Delete backup target %q (%s)?\n\nExisting backups at the target are not removed.", bt.Name, bt.TargetPath),
				"delete_backup")
			return m, nil
		}
	case "t":
		if bt := m.selectedTarget(); bt != nil {
			return m, m.toggleBackup(bt.ID, !bt.Enabled)
		}
	case "b":
		if bt := m.selectedTarget(); bt != nil {
			return m, tea.Batch(flash(fmt.Sprintf("Starting backup %q...", bt.Name), false), m.runBackup(bt.ID))
		}
	case "enter":
		if bt := m.selectedTarget(); bt != nil {
			m.backupsView = backupsSubSnapshots
			m.snapshotTarget = bt.ID
			m.snapshots = nil
			m.snapshotTable.SetRows(nil)
			return m, m.fetchSnapshots(bt.ID)
		}
	case "r":
		return m, m.fetchBackups()
	default:
		m.backupTable.Update(msg)
	}
	return m, nil
}

// handleBackupFormKey handles keys while the create-backup form is open:
// the folder picker first, then Ctrl+O to open it, then the form.
func (m ProfileDetailModel) handleBackupFormKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.picker != nil {
		p := *m.picker
		open, cmd := p.update(msg)
		if !open {
			m.picker = nil
			return m, nil
		}
		m.picker = &p
		return m, cmd
	}
	if msg.String() == "ctrl+o" {
		if m.form.FocusedField() != "target_path" {
			return m, flash("Ctrl+O browses folders on the Path field", false)
		}
		var cmd tea.Cmd
		m.picker, cmd = m.openBackupPicker()
		return m, cmd
	}
	_, cmd := m.form.Update(msg)
	return m, cmd
}

// backupRemote is the remote a remote backup target writes to: the one in
// Remote name for custom_remote, the profile's own remote for remote
// ("same remote"); "" while it is not known.
func (m ProfileDetailModel) backupRemote() string {
	switch api.BackupTargetType(m.form.Value("target_type")) {
	case api.BackupTargetCustomRemote:
		return strings.TrimSpace(m.form.Value("remote_name"))
	case api.BackupTargetRemote:
		if m.profile != nil {
			return remoteOfPath(m.profile.RemoteDir)
		}
	}
	return ""
}

// openBackupPicker opens the folder picker for the backup target's Path:
// local folders for a local target; for a remote target the folders of its
// remote, at the typed path when it is on that remote. While the remote is
// not known it opens at the typed remote path, or else the remote list.
func (m ProfileDetailModel) openBackupPicker() (*dirPicker, tea.Cmd) {
	value := strings.TrimSpace(m.form.Value("target_path"))
	if api.BackupTargetType(m.form.Value("target_type")) == api.BackupTargetLocal {
		if !strings.HasPrefix(value, "/") {
			value = "" // start at home
		}
		return newLocalPicker(m.client, ViewProfileDetail, "target_path", value, m.height)
	}
	start := value
	if remote := m.backupRemote(); remote != "" && remoteOfPath(value) != remote {
		start = remote + ":"
	}
	return newRemotePicker(m.client, ViewProfileDetail, "target_path", start, m.height)
}

// applyPickedPath fills the backup form with the folder the picker chose. A
// custom_remote target without a Remote name takes the folder's remote.
func (m ProfileDetailModel) applyPickedPath(msg pickerChosenMsg) (ProfileDetailModel, tea.Cmd) {
	m.picker = nil
	m.form.SetValue(msg.field, msg.path)
	if api.BackupTargetType(m.form.Value("target_type")) != api.BackupTargetCustomRemote {
		return m, nil
	}
	remote := remoteOfPath(msg.path)
	switch current := strings.TrimSpace(m.form.Value("remote_name")); {
	case remote == "":
	case current == "":
		m.form.SetValue("remote_name", remote)
	case current != remote:
		return m, flash(fmt.Sprintf("The folder is on %s but Remote name is %s; they must match", remote, current), true)
	}
	return m, nil
}

// selectedTarget returns a copy of the highlighted backup target.
func (m ProfileDetailModel) selectedTarget() *api.BackupTargetResponse {
	row := m.backupTable.SelectedRow()
	if row == nil {
		return nil
	}
	for _, bt := range m.backupTargets {
		if strconv.Itoa(bt.ID) == row.Key {
			t := bt
			return &t
		}
	}
	return nil
}

func (m ProfileDetailModel) renderBackupsTab() string {
	var b strings.Builder

	if m.backupsErr != nil {
		b.WriteString(errorLine(m.backupsErr))
	}

	if m.backupsView == backupsSubFiles && m.files != nil {
		b.WriteString(m.renderSnapshotFiles())
		return b.String()
	}
	if m.backupsView == backupsSubSnapshots {
		b.WriteString(headerText(fmt.Sprintf("  Snapshots of target %d", m.snapshotTarget)))
		b.WriteString("\n")
		b.WriteString(m.snapshotTable.View())
		b.WriteString("\n")
		if line := m.runningLine(jobKindBackup, jobKindRestore); line != "" {
			b.WriteString(mutedText(line) + "\n")
		}
		b.WriteString(mutedText("  Enter/r:restore (preview first)  f:browse and restore files  Esc:back"))
		return b.String()
	}

	b.WriteString(m.backupTable.View())
	if bt := m.selectedTarget(); bt != nil && bt.LastLivenessOK != nil && !*bt.LastLivenessOK {
		reason := "unreachable"
		if bt.LastLivenessError != nil {
			reason = *bt.LastLivenessError
		}
		b.WriteString("\n" + errorLine(fmt.Errorf("target %s: %s", bt.Name, reason)))
	}
	if bt := m.selectedTarget(); bt != nil && bt.LastVerifyStatus != nil && *bt.LastVerifyStatus == "failed" {
		reason := "the backup does not match the folder"
		if bt.LastVerifyMessage != nil {
			reason = *bt.LastVerifyMessage
		}
		b.WriteString("\n" + errorLine(fmt.Errorf("target %s: verification of the last backup failed: %s", bt.Name, reason)))
	}
	if bt := m.selectedTarget(); bt != nil && bt.Overdue {
		b.WriteString("\n" + errorLine(fmt.Errorf("target %s: overdue, no backup completed for over twice its frequency (%dh)",
			bt.Name, bt.FrequencyHours)))
	}
	b.WriteString("\n")
	if line := m.runningLine(jobKindBackup, jobKindRestore); line != "" {
		b.WriteString(mutedText(line) + "\n")
	}
	b.WriteString(mutedText("  c:create  d:delete  t:toggle  b:run now  Enter:snapshots  r:refresh"))
	return b.String()
}

func (m *ProfileDetailModel) updateBackupTable() {
	var rows []components.Row
	for _, bt := range m.backupTargets {
		status := ""
		if bt.LastBackupStatus != nil {
			status = *bt.LastBackupStatus
		}
		if bt.LastVerifyStatus != nil && *bt.LastVerifyStatus == "failed" {
			status = "UNVERIFIED"
		}
		if bt.Overdue {
			status = "OVERDUE"
		}
		enabled := "yes"
		if !bt.Enabled {
			enabled = "no"
		}
		encrypted := ""
		if bt.Encrypted {
			encrypted = "yes"
		}
		rows = append(rows, components.Row{
			Key: strconv.Itoa(bt.ID),
			Values: []string{bt.Name, bt.TargetPath, string(bt.TargetType), string(bt.BackupMode), status, encrypted, enabled,
				formatTimePtr(bt.NextScheduledAt, "-")},
		})
	}
	m.backupTable.SetRows(rows)
}

// snapshotKind is the kind of the listed snapshot with this ID ("" when unknown).
func (m ProfileDetailModel) snapshotKind(id string) string {
	for _, s := range m.snapshots {
		if s.SnapshotID == id {
			return s.Kind
		}
	}
	return ""
}

func (m *ProfileDetailModel) updateSnapshotTable() {
	var rows []components.Row
	for _, s := range m.snapshots {
		rows = append(rows, components.Row{
			Key:    s.SnapshotID,
			Values: []string{s.SnapshotID, formatTime(s.CreatedAt), sizeOrDash(s.SizeBytes), s.Status, snapshotKindLabel(s)},
		})
	}
	m.snapshotTable.SetRows(rows)
}

func (m ProfileDetailModel) fetchBackups() tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		data, err := client.ListBackupTargets(context.Background(), slug)
		return PollResultMsg{ViewID: ViewProfileDetail, Data: ProfileDetailData{Slug: slug, Value: data}, Err: err}
	}
}

func (m ProfileDetailModel) fetchSnapshots(targetID int) tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		data, err := client.ListSnapshots(context.Background(), slug, targetID)
		return PollResultMsg{ViewID: ViewProfileDetail, Data: ProfileDetailData{Slug: slug, Value: data}, Err: err}
	}
}

func (m ProfileDetailModel) createBackup(vals map[string]string) tea.Cmd {
	req := api.BackupTargetCreateRequest{
		Name:       strings.TrimSpace(vals["name"]),
		TargetPath: strings.TrimSpace(vals["target_path"]),
		TargetType: api.BackupTargetType(vals["target_type"]),
		BackupMode: api.BackupMode(vals["backup_mode"]),
	}
	if r := strings.TrimSpace(vals["remote_name"]); r != "" {
		req.RemoteName = &r
	}
	verify := vals["verify"] != "no"
	req.VerifyAfterBackup = &verify
	if pass := vals["passphrase"]; pass != "" {
		if pass != vals["passphrase2"] {
			return flash("The two passphrases differ", true)
		}
		req.EncryptionPassphrase = &pass
	}
	for key, dst := range map[string]*int{"frequency_hours": &req.FrequencyHours, "retention_days": &req.RetentionDays,
		"keep_last": &req.KeepLast} {
		if s := strings.TrimSpace(vals[key]); s != "" {
			n, err := strconv.Atoi(s)
			if err != nil || n < 1 {
				return flash(fmt.Sprintf("%s must be a whole number of at least 1", key), true)
			}
			*dst = n
		}
	}
	client, slug := m.client, m.slug
	return func() tea.Msg {
		_, err := client.CreateBackupTarget(context.Background(), slug, req)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "create_backup", Err: err}
	}
}

func (m ProfileDetailModel) deleteBackup(id int) tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		err := client.DeleteBackupTarget(context.Background(), slug, id)
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "delete_backup", Err: err}
	}
}

func (m ProfileDetailModel) toggleBackup(id int, enabled bool) tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		_, err := client.UpdateBackupTarget(context.Background(), slug, id, api.BackupTargetUpdateRequest{Enabled: &enabled})
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "toggle_backup", Err: err}
	}
}

// runBackup starts a backup of target id; the answer (the job running, or a
// refusal) arrives as actionBackupStarted and the job is then followed.
func (m ProfileDetailModel) runBackup(id int) tea.Cmd {
	client, slug := m.client, m.slug
	return startBackupCmd(slug, jobKindBackup, actionBackupStarted, id, func(ctx context.Context) (*api.BackupJobResponse, error) {
		return client.RunBackup(ctx, slug, id)
	})
}

// previewRestore asks the backend what a full restore would change; the
// answer opens the confirmation.
func (m ProfileDetailModel) previewRestore(req fullRestore) tea.Cmd {
	client, slug := m.client, m.slug
	return func() tea.Msg {
		preview, err := client.PreviewRestore(context.Background(), slug, req.target.targetID, api.RestoreRequest{
			SnapshotID: req.target.snapshotID, RestoreScope: req.scope,
		})
		return ActionResultMsg{ViewID: ViewProfileDetail, Action: "restore_preview", Err: err, Data: preview}
	}
}

// restorePrompt is the confirmation text of a full restore, with its preview.
func restorePrompt(req fullRestore, preview *api.RestorePreviewResponse, err error) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Restore snapshot %s (%s)?\n\n", req.target.snapshotID, req.scope)
	switch {
	case err != nil:
		fmt.Fprintf(&b, "The preview failed (%s); what would change is unknown.\n", err)
	case preview != nil:
		for _, s := range preview.Sides {
			fmt.Fprintf(&b, "%s folder %s: %d added, %d replaced, %d removed, %d unchanged\n",
				s.Side, s.Path, s.Added, s.Replaced, s.Removed, s.Unchanged)
		}
		if !preview.Exact {
			b.WriteString("An old-style snapshot: files are copied back, nothing is removed.\n")
		}
	}
	b.WriteString("\nReplaced and removed files are kept in .omnisync-trash/pre-restore/.")
	return b.String()
}

// restoreSnapshot starts a full restore; the answer arrives as
// actionRestoreStarted and the job is then followed.
func (m ProfileDetailModel) restoreSnapshot(target restoreTarget, scope api.RestoreScope) tea.Cmd {
	client, slug := m.client, m.slug
	return tea.Batch(
		flash("Starting the restore...", false),
		startBackupCmd(slug, jobKindRestore, actionRestoreStarted, target.targetID,
			func(ctx context.Context) (*api.BackupJobResponse, error) {
				return client.RestoreSnapshot(ctx, slug, target.targetID, api.RestoreRequest{
					SnapshotID:   target.snapshotID,
					RestoreScope: scope,
				})
			}),
	)
}

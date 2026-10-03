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

type profileMode int

const (
	profileModeList profileMode = iota
	profileModeCreate
	profileModeEdit
	profileModeConfirmDelete
)

const (
	formCreateProfile = "create_profile"
	formEditProfile   = "edit_profile"
)

// ProfilesModel is the Profiles list view.
type ProfilesModel struct {
	client   *api.Client
	profiles []api.ProfileStatusResponse
	table    components.Table
	form     components.Form
	confirm  components.Confirm
	mode     profileMode
	// pendingSlug is the profile an open edit form or delete prompt acts
	// on, captured when it opened. Polls may reorder the table meanwhile.
	pendingSlug string
	// pendingMode is the sync mode of that profile when the edit form
	// opened; the update sends sync_mode only when the user changed it.
	pendingMode api.SyncMode
	// pendingArgs is the rclone_args text the edit form opened with; the
	// update sends rclone_args only when the user changed it.
	pendingArgs string
	// saving is set while a submitted form waits for the backend; the form
	// comes back with the error if the backend refuses it.
	saving bool
	// picker is the folder picker opened from the form (Ctrl+O), or nil.
	picker  *dirPicker
	loading bool
	err     error
	width   int
	height  int
}

// NewProfilesModel creates a new profiles list view.
func NewProfilesModel(client *api.Client) ProfilesModel {
	cols := []components.Column{
		{Title: "Name", Width: 16},
		{Title: "State", Width: 24},
		{Title: "Mode", Width: 8},
		{Title: "Enabled", Width: 8},
		{Title: "Local", Width: 18},
		{Title: "Remote", Width: 18},
		{Title: "Pending", Width: 8},
		{Title: "Last Sync", Width: 19},
	}
	return ProfilesModel{
		client:  client,
		table:   components.NewTable(cols, 15),
		mode:    profileModeList,
		loading: true,
	}
}

func (m ProfilesModel) ViewID() ViewID { return ViewProfiles }

// CapturesInput is true while a form or the delete prompt is open.
func (m ProfilesModel) CapturesInput() bool { return m.mode != profileModeList }

// KeyHints names the keys of the current mode for the bottom bar.
func (m ProfilesModel) KeyHints() string {
	switch m.mode {
	case profileModeCreate, profileModeEdit:
		switch {
		case m.picker != nil:
			return m.picker.hints()
		case m.saving:
			return "Saving..."
		}
		return formHints + "  Ctrl+O:browse folders"
	case profileModeConfirmDelete:
		return confirmHints
	}
	return "c:create  e:edit  d:delete  t:toggle  Enter:detail  r:refresh"
}

func (m ProfilesModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "c", Desc: "Create profile"},
		{Key: "Ctrl+O", Desc: "(form, on Local Dir / Remote Dir) Browse folders, pick a remote"},
		{Key: "e", Desc: "Edit profile"},
		{Key: "d", Desc: "Delete profile (asks first)"},
		{Key: "t", Desc: "Toggle enable/disable"},
		{Key: "Up/Down, j/k", Desc: "Move"},
		{Key: "n/N", Desc: "Next/previous page"},
		{Key: "Enter", Desc: "View detail"},
		{Key: "r", Desc: "Refresh"},
	}
}

// PollSpec polls quickly while any profile syncs.
func (m ProfilesModel) PollSpec() *PollSpec {
	return &PollSpec{
		FastInterval: FastPollInterval,
		SlowInterval: SlowTickInterval,
		IsActive: func(interface{}) bool {
			for _, p := range m.profiles {
				if p.State.Busy() {
					return true
				}
			}
			return false
		},
	}
}

func (m ProfilesModel) Init() tea.Cmd {
	return m.fetchProfiles()
}

func (m ProfilesModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.table.SetWidth(msg.Width)

	case PollResultMsg:
		return m.handlePollResult(msg)

	case TickMsg:
		return m, m.fetchProfiles()

	case ActionResultMsg:
		return m.handleActionResult(msg)

	case components.FormSubmitMsg:
		return m.handleFormSubmit(msg)

	case components.FormCancelMsg:
		m.mode = profileModeList
		m.pendingSlug = ""
		m.picker = nil
		return m, nil

	case pickerChosenMsg:
		if m.picker != nil && (m.mode == profileModeCreate || m.mode == profileModeEdit) {
			m.form.SetValue(msg.field, msg.path)
			m.picker = nil
		}
		return m, nil

	case components.ConfirmResultMsg:
		return m.handleConfirmResult(msg)

	case tea.PasteMsg:
		if (m.mode == profileModeCreate || m.mode == profileModeEdit) && m.picker == nil && !m.saving {
			_, cmd := m.form.Update(msg)
			return m, cmd
		}

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m ProfilesModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	m.loading = false
	if msg.Err != nil {
		m.err = msg.Err
		return m, nil
	}
	if profiles, ok := msg.Data.([]api.ProfileStatusResponse); ok {
		m.err = nil
		m.profiles = profiles
		m.updateTable()
	}
	return m, nil
}

func (m ProfilesModel) handleActionResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	switch msg.Action {
	case actionBrowse:
		if m.picker != nil {
			p := *m.picker
			p.handleAnswer(msg)
			m.picker = &p
		}
		return m, nil
	case "create_profile", "update_profile":
		if !m.saving {
			break
		}
		m.saving = false
		if msg.Err != nil {
			// R5.11: show the error in the form and keep the input.
			m.form.Reopen(apiDetail(msg.Err))
			return m, errorFlash(actionLabel(msg.Action)+" failed", msg.Err)
		}
		m.mode = profileModeList
		m.pendingSlug = ""
		m.pendingMode = ""
		m.pendingArgs = ""
	}
	if msg.Err != nil {
		return m, tea.Batch(m.fetchProfiles(), errorFlash(actionLabel(msg.Action)+" failed", msg.Err))
	}
	switch msg.Action {
	case "update_profile":
		// Data is the new sync mode when the edit switched it.
		text := actionLabel(msg.Action)
		switch mode, _ := msg.Data.(api.SyncMode); mode {
		case api.SyncModeTwoWay:
			text += "; it now syncs two-way. The next sync is a resync: the union of both folders, nothing deleted"
		case api.SyncModeMirror:
			text += "; it now mirrors (one-way push/pull)"
		}
		return m, tea.Batch(m.fetchProfiles(), flash(text, false))
	case "create_profile", "delete_profile", "toggle_profile":
		return m, tea.Batch(m.fetchProfiles(), flash(actionLabel(msg.Action), false))
	}
	return m, nil
}

func actionLabel(action string) string {
	switch action {
	case "create_profile":
		return "Profile created"
	case "update_profile":
		return "Profile updated"
	case "delete_profile":
		return "Profile deleted"
	case "toggle_profile":
		return "Profile toggled"
	}
	return action
}

// handleFormSubmit checks the numbers and sends the form. The form stays
// open (inactive) until the backend answers, and comes back with the error
// if it refuses.
func (m ProfilesModel) handleFormSubmit(msg components.FormSubmitMsg) (tea.Model, tea.Cmd) {
	if msg.FormID != formCreateProfile && msg.FormID != formEditProfile {
		return m, nil
	}
	nums, errText := parseProfileNumbers(msg.Values)
	if errText != "" {
		m.form.Reopen(errText)
		return m, nil
	}
	m.saving = true
	if msg.FormID == formCreateProfile {
		return m, m.createProfile(msg.Values, nums)
	}
	return m, m.updateProfile(m.pendingSlug, m.pendingMode, m.pendingArgs, msg.Values, nums)
}

func (m ProfilesModel) handleConfirmResult(msg components.ConfirmResultMsg) (tea.Model, tea.Cmd) {
	slug := m.pendingSlug
	m.mode = profileModeList
	m.pendingSlug = ""
	if msg.Tag == "delete" && msg.Confirmed && slug != "" {
		return m, m.deleteProfile(slug)
	}
	return m, nil
}

func (m ProfilesModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	switch m.mode {
	case profileModeCreate, profileModeEdit:
		return m.handleFormKey(msg)
	case profileModeConfirmDelete:
		_, cmd := m.confirm.Update(msg)
		return m, cmd
	}

	switch msg.String() {
	case "c":
		m.mode = profileModeCreate
		// New profiles sync two-way unless the user picks mirror.
		m.form = components.NewFormWithID(formCreateProfile, "Create Profile",
			profileFormFields(profileFormValues{mode: api.SyncModeTwoWay}))
		return m, nil
	case "e":
		if row := m.table.SelectedRow(); row != nil {
			if p := m.findProfile(row.Key); p != nil {
				m.mode = profileModeEdit
				m.pendingSlug = p.Slug
				m.pendingMode = modeFromOption(modeOption(p.SyncMode))
				vals := valuesOf(p.ProfileResponse)
				m.pendingArgs = vals.args
				m.form = components.NewFormWithID(formEditProfile,
					fmt.Sprintf("Edit Profile %s (%s)", p.Name, p.Slug), profileFormFields(vals))
				return m, nil
			}
		}
	case "d":
		if row := m.table.SelectedRow(); row != nil {
			if p := m.findProfile(row.Key); p != nil {
				m.mode = profileModeConfirmDelete
				m.pendingSlug = p.Slug
				m.confirm = components.NewConfirm(
					fmt.Sprintf("Delete profile %q (%s)?\n\nThe profile and its sync history are removed.\nFiles in %s and %s are not touched.",
						p.Name, p.Slug, p.LocalDir, p.RemoteDir),
					"delete",
				)
				return m, nil
			}
		}
	case "t":
		if row := m.table.SelectedRow(); row != nil {
			return m, m.toggleProfile(row.Key)
		}
	case "enter":
		if row := m.table.SelectedRow(); row != nil {
			slug := row.Key
			return m, func() tea.Msg {
				return NavigateMsg{Target: ViewProfileDetail, Param: slug}
			}
		}
	case "r":
		m.loading = true
		return m, m.fetchProfiles()
	default:
		m.table.Update(msg)
	}
	return m, nil
}

// handleFormKey handles keys while the create/edit form is open: the picker
// first, then Ctrl+O to open it, then the form.
func (m ProfilesModel) handleFormKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
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
	if m.saving {
		return m, nil
	}
	if msg.String() == "ctrl+o" {
		field := m.form.FocusedField()
		value := strings.TrimSpace(m.form.Value(field))
		var cmd tea.Cmd
		switch field {
		case "local_dir":
			m.picker, cmd = newLocalPicker(m.client, ViewProfiles, field, value, m.height)
		case "remote_dir":
			m.picker, cmd = newRemotePicker(m.client, ViewProfiles, field, value, m.height)
		default:
			return m, flash("Ctrl+O browses folders on the Local Dir and Remote Dir fields", false)
		}
		return m, cmd
	}
	_, cmd := m.form.Update(msg)
	return m, cmd
}

// View renders the profiles view.
func (m ProfilesModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder

	switch m.mode {
	case profileModeCreate, profileModeEdit:
		switch {
		case m.picker != nil:
			b.WriteString(m.picker.View())
		case m.saving:
			b.WriteString("  Saving the profile...")
		default:
			b.WriteString(m.form.View())
		}
		return tea.NewView(b.String())
	case profileModeConfirmDelete:
		b.WriteString(m.confirm.View())
		return tea.NewView(b.String())
	}

	b.WriteString(headerText("  Profiles"))
	b.WriteString("\n")

	if m.err != nil {
		b.WriteString(errorLine(m.err))
	}

	if m.loading && len(m.profiles) == 0 {
		b.WriteString("  Loading...")
		return tea.NewView(b.String())
	}

	b.WriteString(m.table.View())
	if row := m.table.SelectedRow(); row != nil {
		if p := m.findProfile(row.Key); p != nil && (p.State == api.SyncStateError || p.ResyncRequired) {
			reason := "no reason reported"
			if p.LastError != nil && *p.LastError != "" {
				reason = *p.LastError
			}
			if p.ResyncRequired {
				reason = "resync required (open the profile and press R): " + reason
			}
			b.WriteString("\n" + errorLine(fmt.Errorf("%s: %s", p.Name, reason)))
		}
	}

	return tea.NewView(b.String())
}

// --- Data helpers ---

func (m *ProfilesModel) updateTable() {
	var rows []components.Row
	for _, p := range m.profiles {
		enabled := "yes"
		if !p.Enabled {
			enabled = "no"
		}
		rows = append(rows, components.Row{
			Key: p.Slug,
			Values: []string{
				p.Name,
				stateLabel(p.State, p.LastError, p.ResyncRequired),
				syncModeLabel(p.SyncMode),
				enabled,
				p.LocalDir,
				p.RemoteDir,
				fmt.Sprintf("%d", p.PendingChanges),
				formatTimePtr(p.LastSync, "never"),
			},
			Colors: cellColors(8, 1, stateColor(p.State, p.ResyncRequired)),
		})
	}
	m.table.SetRows(rows)
}

func (m ProfilesModel) findProfile(slug string) *api.ProfileStatusResponse {
	for i := range m.profiles {
		if m.profiles[i].Slug == slug {
			p := m.profiles[i]
			return &p
		}
	}
	return nil
}

// The choices of the profile form's mode field.
const (
	modeOptionTwoWay = "Two-way (recommended)"
	modeOptionMirror = "Mirror (one-way push/pull only)"
)

// syncModeHelp explains the two modes under the form's mode field.
const syncModeHelp = "Two-way: changes on either side are carried to the other. New and edited files\n" +
	"on both sides are kept; a file changed on both sides keeps both versions.\n" +
	"The first two-way sync is a resync: the union of both folders, nothing deleted.\n" +
	"Mirror: one-way push/pull only. Local changes are pushed and the remote is pulled\n" +
	"on the interval; the side that syncs last wins."

// modeOption is the form choice for a sync mode. A profile without a mode
// is a mirror (the backend's default for existing profiles).
func modeOption(mode api.SyncMode) string {
	if mode == api.SyncModeTwoWay {
		return modeOptionTwoWay
	}
	return modeOptionMirror
}

// modeFromOption maps the form choice back to the sync mode.
func modeFromOption(option string) api.SyncMode {
	if option == modeOptionMirror {
		return api.SyncModeMirror
	}
	return api.SyncModeTwoWay
}

// profileFormValues are the texts a profile form starts with.
type profileFormValues struct {
	name, localDir, remoteDir, filters string
	mode                               api.SyncMode
	pullInterval, debounce, retries    string
	args                               string
	bwlimit, window                    string
}

// valuesOf fills the edit form from a profile. A zero number stays empty,
// so an unset value is not sent back as 0.
func valuesOf(p api.ProfileResponse) profileFormValues {
	num := func(n int) string {
		if n == 0 {
			return ""
		}
		return strconv.Itoa(n)
	}
	return profileFormValues{
		name: p.Name, localDir: p.LocalDir, remoteDir: p.RemoteDir,
		filters: strings.Join(p.RcloneFilter, ", "), mode: p.SyncMode,
		pullInterval: num(p.PullIntervalMinutes), debounce: num(p.DebounceSeconds), retries: num(p.MaxRetries),
		args:    strings.Join(p.RcloneArgs, " "),
		bwlimit: derefOr(p.Bwlimit, ""), window: formatSyncWindow(p.SyncWindow),
	}
}

func derefOr(s *string, fallback string) string {
	if s == nil {
		return fallback
	}
	return *s
}

func profileFormFields(v profileFormValues) []components.Field {
	return []components.Field{
		{Name: "name", Label: "Name", Type: components.FieldText, Value: v.name, Required: true},
		{Name: "local_dir", Label: "Local Dir", Type: components.FieldText, Value: v.localDir, Required: true,
			Help: "Absolute path, e.g. /home/you/Documents. Ctrl+O: browse folders"},
		{Name: "remote_dir", Label: "Remote Dir", Type: components.FieldText, Value: v.remoteDir, Required: true,
			Help: "<remote>:<path>, e.g. gdrive:Backup/Documents. Ctrl+O: pick a remote and folder"},
		{Name: "rclone_filters", Label: "Filters", Type: components.FieldText, Value: v.filters,
			Help: "Comma-separated rclone filter rules, e.g. - *.tmp, - .cache/**"},
		{Name: "sync_mode", Label: "Mode", Type: components.FieldDropdown, Value: modeOption(v.mode),
			Options: []string{modeOptionTwoWay, modeOptionMirror}, Help: syncModeHelp},
		{Name: "pull_interval_minutes", Label: "Pull every (min)", Type: components.FieldText, Value: v.pullInterval,
			Help: "Minutes between automatic pulls (1-10080). Empty: the default (5)"},
		{Name: "debounce_seconds", Label: "Debounce (s)", Type: components.FieldText, Value: v.debounce,
			Help: "Seconds to wait after a local change before pushing (1-3600). Empty: the default (5)"},
		{Name: "max_retries", Label: "Max retries", Type: components.FieldText, Value: v.retries,
			Help: "Attempts for a failed sync (1-10). Empty: the default (3)"},
		{Name: "rclone_args", Label: "rclone args", Type: components.FieldText, Value: v.args,
			Help: "Extra rclone flags separated by spaces, e.g. --transfers 8"},
		{Name: "bwlimit", Label: "Bandwidth limit", Type: components.FieldText, Value: v.bwlimit,
			Help: "rclone --bwlimit: 10M, 512k, or a timetable such as 08:00,512k 19:00,10M 23:00,off. Empty: no limit"},
		{Name: "sync_window", Label: "Sync window", Type: components.FieldText, Value: v.window,
			Help: "When automatic syncs may run (server time), e.g. 22:00-06:00 or Mon-Fri 19:00-07:00. Empty: any time"},
	}
}

// profileNumbers are the optional numeric fields (nil means "not given")
// and the parsed sync window (nil: none).
type profileNumbers struct {
	pullInterval, debounce, retries *int
	window                          *api.SyncWindow
}

// parseProfileNumbers reads the numeric fields, or returns an error text
// for the form.
func parseProfileNumbers(vals map[string]string) (profileNumbers, string) {
	var out profileNumbers
	for _, f := range []struct {
		key, label string
		dst        **int
	}{
		{"pull_interval_minutes", "Pull every (min)", &out.pullInterval},
		{"debounce_seconds", "Debounce (s)", &out.debounce},
		{"max_retries", "Max retries", &out.retries},
	} {
		text := strings.TrimSpace(vals[f.key])
		if text == "" {
			continue
		}
		n, err := strconv.Atoi(text)
		if err != nil || n < 1 {
			return out, f.label + " must be a whole number of at least 1"
		}
		*f.dst = &n
	}
	window, err := parseSyncWindow(vals["sync_window"])
	if err != nil {
		return out, err.Error()
	}
	out.window = window
	return out, ""
}

func parseFilters(s string) []string {
	filters := []string{}
	for _, part := range strings.Split(s, ",") {
		if f := strings.TrimSpace(part); f != "" {
			filters = append(filters, f)
		}
	}
	return filters
}

// --- API commands ---

func (m ProfilesModel) fetchProfiles() tea.Cmd {
	return fetchProfilesCmd(m.client, ViewProfiles)
}

func (m ProfilesModel) createProfile(vals map[string]string, nums profileNumbers) tea.Cmd {
	client := m.client
	req := api.ProfileCreateRequest{
		Name:         strings.TrimSpace(vals["name"]),
		LocalDir:     strings.TrimSpace(vals["local_dir"]),
		RemoteDir:    strings.TrimSpace(vals["remote_dir"]),
		RcloneFilter: parseFilters(vals["rclone_filters"]),
		RcloneArgs:   strings.Fields(vals["rclone_args"]),
		SyncMode:     modeFromOption(vals["sync_mode"]),
		SyncWindow:   nums.window,
	}
	if bw := strings.TrimSpace(vals["bwlimit"]); bw != "" {
		req.Bwlimit = &bw
	}
	if nums.pullInterval != nil {
		req.PullIntervalMinutes = *nums.pullInterval
	}
	if nums.debounce != nil {
		req.DebounceSeconds = *nums.debounce
	}
	if nums.retries != nil {
		req.MaxRetries = *nums.retries
	}
	return func() tea.Msg {
		_, err := client.CreateProfile(context.Background(), req)
		return ActionResultMsg{ViewID: ViewProfiles, Action: "create_profile", Err: err}
	}
}

// updateProfile saves the edit form. sync_mode is sent only when the user
// changed it: switching to two-way makes the next sync a resync, so an
// unchanged mode must not be sent again. rclone_args is sent only when its
// text changed, so arguments are never re-split behind the user's back.
func (m ProfilesModel) updateProfile(slug string, oldMode api.SyncMode, oldArgs string, vals map[string]string, nums profileNumbers) tea.Cmd {
	client := m.client
	name := strings.TrimSpace(vals["name"])
	localDir := strings.TrimSpace(vals["local_dir"])
	remoteDir := strings.TrimSpace(vals["remote_dir"])
	filters := parseFilters(vals["rclone_filters"])
	req := api.ProfileUpdateRequest{
		Name:                &name,
		LocalDir:            &localDir,
		RemoteDir:           &remoteDir,
		RcloneFilter:        &filters,
		PullIntervalMinutes: nums.pullInterval,
		DebounceSeconds:     nums.debounce,
		MaxRetries:          nums.retries,
		// "" clears the limit, a WindowUpdate without a window clears it.
		Bwlimit:    ptrTo(strings.TrimSpace(vals["bwlimit"])),
		SyncWindow: &api.WindowUpdate{Window: nums.window},
	}
	if args := strings.Fields(vals["rclone_args"]); strings.Join(args, " ") != oldArgs {
		req.RcloneArgs = &args
	}
	var switched api.SyncMode
	if mode := modeFromOption(vals["sync_mode"]); mode != oldMode {
		req.SyncMode = &mode
		switched = mode
	}
	return func() tea.Msg {
		_, err := client.UpdateProfile(context.Background(), slug, req)
		var data interface{}
		if switched != "" {
			data = switched
		}
		return ActionResultMsg{ViewID: ViewProfiles, Action: "update_profile", Err: err, Data: data}
	}
}

func (m ProfilesModel) deleteProfile(slug string) tea.Cmd {
	client := m.client
	return func() tea.Msg {
		err := client.DeleteProfile(context.Background(), slug)
		return ActionResultMsg{ViewID: ViewProfiles, Action: "delete_profile", Err: err}
	}
}

func (m ProfilesModel) toggleProfile(slug string) tea.Cmd {
	client := m.client
	enable := true
	if p := m.findProfile(slug); p != nil && p.Enabled {
		enable = false
	}
	return func() tea.Msg {
		var err error
		if enable {
			_, err = client.EnableProfile(context.Background(), slug)
		} else {
			_, err = client.DisableProfile(context.Background(), slug)
		}
		return ActionResultMsg{ViewID: ViewProfiles, Action: "toggle_profile", Err: err}
	}
}

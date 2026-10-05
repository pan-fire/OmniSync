package ui

import (
	"cmp"
	"context"
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/theme"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// syncAllOutcome is the result of one profile's sync started by
// "Push all" / "Pull all" (or the profile view): the start, and how the
// followed job ended.
type syncAllOutcome struct {
	Slug      string
	Direction api.SyncDirection
	Resp      *api.SyncStartResponse
	Result    *api.SyncOutcome
	// FollowErr: the sync started, but its job could not be followed.
	FollowErr error
}

// failed reports whether the sync was refused, failed or was stopped, and
// why (empty when the backend gave no reason).
func (o syncAllOutcome) failed() (bool, string) {
	if o.Result != nil {
		return o.Result.Failed, o.Result.Reason
	}
	return o.Resp != nil && o.Resp.State == api.SyncStateError, ""
}

// syncAllCheck is a "Push all" / "Pull all" request waiting for the
// per-profile previews (POST /profiles/{slug}/sync/preview) that its prompt
// lists. The profiles are captured when the user pressed the key.
type syncAllCheck struct {
	seq      int
	dir      api.SyncDirection
	targets  []api.ProfileStatusResponse
	previews map[string]syncAllPreview
}

// syncAllPreview is one profile's preview for a pending Push/Pull all; a
// nil resp or a non-nil err means its counts are unavailable.
type syncAllPreview struct {
	seq  int
	slug string
	resp *api.SyncPreviewResponse
	err  error
}

// DashboardModel is the Dashboard view.
type DashboardModel struct {
	client  *api.Client
	loading bool
	err     error // the aggregate status could not be read
	// profilesErr: the profile list could not be read. Kept apart from err
	// so that one answer does not clear the other's error.
	profilesErr error

	aggStatus    *api.AggregateStatusResponse
	health       *api.HealthResponse
	network      *api.NetworkHealthResponse
	networkErr   error
	remotes      *api.RemoteHealthResponse
	remotesErr   error
	profiles     []api.ProfileStatusResponse
	profileTable components.Table

	// Push/Pull all: the previews being counted, then the prompt and the
	// profiles captured when it opened.
	checkingAll    *syncAllCheck
	syncAllSeq     int
	confirm        components.Confirm
	pendingDir     api.SyncDirection
	pendingTargets []api.ProfileStatusResponse
	// Switch all mirror profiles to two-way: the profiles captured when the
	// prompt opened.
	pendingSwitchAll []api.ProfileStatusResponse

	width  int
	height int
}

// NewDashboardModel creates a new dashboard.
func NewDashboardModel(client *api.Client) DashboardModel {
	cols := []components.Column{
		{Title: "Name", Width: 16},
		{Title: "Mode", Width: 8},
		{Title: "State", Width: 24},
		{Title: "Local", Width: 20},
		{Title: "Remote", Width: 20},
		{Title: "Last Sync", Width: 19},
		{Title: "Pending", Width: 8},
	}
	return DashboardModel{
		client:       client,
		loading:      true,
		profileTable: components.NewTable(cols, 10),
	}
}

// ViewID returns the view identifier.
func (m DashboardModel) ViewID() ViewID {
	return ViewDashboard
}

// CapturesInput is true while Push/Pull all counts files or asks.
func (m DashboardModel) CapturesInput() bool {
	return m.confirm.Active || m.checkingAll != nil
}

// KeyHints names the keys of the current mode for the bottom bar.
func (m DashboardModel) KeyHints() string {
	switch {
	case m.confirm.Active:
		return confirmHints
	case m.checkingAll != nil:
		return "Esc:cancel"
	}
	if len(mirrorProfiles(m.profiles)) > 0 {
		return "p:push all  l:pull all  w:all to two-way  s:stop  z:pause all  u:resume all  Enter:detail  r:refresh"
	}
	return "p:push all  l:pull all  s:stop  z:pause all  u:resume all  Enter:detail  r:refresh"
}

// KeyBindings returns key bindings for the help overlay.
func (m DashboardModel) KeyBindings() []components.KeyBinding {
	return []components.KeyBinding{
		{Key: "p", Desc: "Push all enabled profiles (counts files, then asks)"},
		{Key: "l", Desc: "Pull all enabled profiles (counts files, then asks)"},
		{Key: "s", Desc: "Stop all running syncs"},
		{Key: "w", Desc: "Switch all mirror profiles to two-way sync (lists them, then asks)"},
		{Key: "z", Desc: "Pause automatic syncing of all profiles (kept until you resume; syncs you start still run)"},
		{Key: "u", Desc: "Resume all profiles you paused (pauses OmniSync set to protect files stay)"},
		{Key: "Up/Down, j/k", Desc: "Move in the profile table"},
		{Key: "n/N", Desc: "Next/previous page"},
		{Key: "Enter", Desc: "Open profile detail"},
		{Key: "r", Desc: "Refresh (including network and remote checks)"},
	}
}

// PollSpec returns the adaptive polling specification.
func (m DashboardModel) PollSpec() *PollSpec {
	return &PollSpec{
		FastInterval: FastPollInterval,
		SlowInterval: SlowTickInterval,
		IsActive: func(data interface{}) bool {
			if agg, ok := data.(*api.AggregateStatusResponse); ok && agg != nil {
				return agg.OverallState.Busy()
			}
			return m.aggStatus != nil && m.aggStatus.OverallState.Busy()
		},
	}
}

// Init fires initial data fetches.
func (m DashboardModel) Init() tea.Cmd {
	return tea.Batch(m.pollAll(), m.fetchNetwork(), m.fetchRemotes())
}

// Update handles messages.
func (m DashboardModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.profileTable.SetWidth(msg.Width)

	case PollResultMsg:
		return m.handlePollResult(msg)

	case ActionResultMsg:
		return m.handleActionResult(msg)

	case TickMsg:
		return m, m.pollAll()

	case components.ConfirmResultMsg:
		return m.handleConfirmResult(msg)

	case tea.KeyPressMsg:
		return m.handleKey(msg)
	}
	return m, nil
}

func (m DashboardModel) handlePollResult(msg PollResultMsg) (tea.Model, tea.Cmd) {
	switch data := msg.Data.(type) {
	case *api.AggregateStatusResponse:
		m.loading = false
		if msg.Err != nil {
			m.err = msg.Err
			return m, nil
		}
		m.err = nil
		m.aggStatus = data
	case *api.HealthResponse:
		if msg.Err == nil {
			m.health = data
		}
	case *api.NetworkHealthResponse:
		m.networkErr = msg.Err
		if msg.Err == nil {
			m.network = data
		}
	case *api.RemoteHealthResponse:
		m.remotesErr = msg.Err
		if msg.Err == nil {
			m.remotes = data
		}
	case []api.ProfileStatusResponse:
		m.profilesErr = msg.Err
		if msg.Err != nil {
			return m, nil
		}
		m.profiles = data
		m.updateProfileTable()
	default:
		if msg.Err != nil {
			m.err = msg.Err
		}
	}
	return m, nil
}

func (m DashboardModel) handleActionResult(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	switch msg.Action {
	case "sync_all_preview":
		return m.handleSyncAllPreview(msg)
	case "sync_all":
		out, _ := msg.Data.(syncAllOutcome)
		verb := directionVerb(out.Direction)
		if msg.Err != nil {
			return m, tea.Batch(m.pollAll(), errorFlash(fmt.Sprintf("%s of %s failed", verb, out.Slug), msg.Err))
		}
		if out.FollowErr != nil {
			return m, tea.Batch(m.pollAll(), errorFlash(fmt.Sprintf("Lost track of the %s of %s; check the profile", strings.ToLower(verb), out.Slug), out.FollowErr))
		}
		if failed, reason := out.failed(); failed {
			if reason != "" {
				return m, tea.Batch(m.pollAll(), flash(fmt.Sprintf("%s of %s ended in an error: %s", verb, out.Slug, reason), true))
			}
			return m, tea.Batch(m.pollAll(), flash(fmt.Sprintf("%s of %s ended in an error (see the profile)", verb, out.Slug), true))
		}
		return m, tea.Batch(m.pollAll(), flash(fmt.Sprintf("%s of %s finished", verb, out.Slug), false))
	case "switch_all_two_way":
		res, _ := msg.Data.(switchAllResult)
		return m, tea.Batch(m.pollAll(), switchAllFlash(res))
	case "pause_all", "resume_all":
		resp, _ := msg.Data.(*api.PauseAllResponse)
		return m, tea.Batch(m.pollAll(), pauseAllFlash(msg.Action, resp, msg.Err))
	case "stop_all":
		if msg.Err != nil {
			return m, tea.Batch(m.pollAll(), errorFlash("Stop failed", msg.Err))
		}
		return m, tea.Batch(m.pollAll(), flash("Sync stopped", false))
	}
	return m, nil
}

func (m DashboardModel) handleConfirmResult(msg components.ConfirmResultMsg) (tea.Model, tea.Cmd) {
	if msg.Tag == "switch_all_two_way" {
		targets := m.pendingSwitchAll
		m.pendingSwitchAll = nil
		if !msg.Confirmed || len(targets) == 0 {
			return m, flash("Switch to two-way cancelled; the profiles stay mirrors", false)
		}
		slugs := make([]string, 0, len(targets))
		for _, p := range targets {
			slugs = append(slugs, p.Slug)
		}
		return m, switchAllTwoWayCmd(m.client, slugs)
	}
	if msg.Tag != "sync_all" {
		return m, nil
	}
	targets, dir := m.pendingTargets, m.pendingDir
	m.pendingTargets = nil
	if !msg.Confirmed {
		return m, flash(directionVerb(dir)+" all cancelled", false)
	}
	cmds := []tea.Cmd{flash(fmt.Sprintf("%s started for %d profile(s)", directionVerb(dir), len(targets)), false)}
	for _, p := range targets {
		cmds = append(cmds, startConfirmedSyncCmd(m.client, ViewDashboard, "sync_all", p.Slug, dir))
	}
	return m, tea.Batch(cmds...)
}

// startConfirmedSyncCmd runs POST /profiles/{slug}/sync/start with
// force=true, so a profile whose intervals are paused syncs too. Use it only
// after the user confirmed this sync; the backend still runs every safety
// check.
func startConfirmedSyncCmd(client *api.Client, view ViewID, action, slug string, dir api.SyncDirection) tea.Cmd {
	return startSyncCmd(client, view, action, slug, dir, true)
}

// startSyncCmd runs POST /profiles/{slug}/sync/start, follows the started
// job until it ends (polling it at FastPollInterval, while the views keep
// polling the status) and reports the outcome as action. force must be
// false unless the user confirmed this sync.
func startSyncCmd(client *api.Client, view ViewID, action, slug string, dir api.SyncDirection, force bool) tea.Cmd {
	return func() tea.Msg {
		resp, err := client.StartProfileSync(context.Background(), slug, dir, force)
		return ActionResultMsg{ViewID: view, Action: action, Err: err,
			Data: followSync(client, slug, dir, resp, err)}
	}
}

// followSync waits for the sync that a start (resp, err) began.
func followSync(client *api.Client, slug string, dir api.SyncDirection, resp *api.SyncStartResponse, err error) syncAllOutcome {
	out := syncAllOutcome{Slug: slug, Direction: dir, Resp: resp}
	if err != nil || resp == nil {
		return out
	}
	out.Result, out.FollowErr = client.FollowSync(context.Background(), slug, resp, FastPollInterval)
	return out
}

func (m DashboardModel) handleKey(msg tea.KeyPressMsg) (tea.Model, tea.Cmd) {
	if m.confirm.Active {
		_, cmd := m.confirm.Update(msg)
		return m, cmd
	}
	if m.checkingAll != nil {
		if msg.String() == "esc" {
			dir := m.checkingAll.dir
			m.checkingAll = nil
			return m, flash(directionVerb(dir)+" all cancelled", false)
		}
		return m, nil
	}

	switch msg.String() {
	case "p":
		return m.askSyncAll(api.SyncDirectionPush)
	case "l":
		return m.askSyncAll(api.SyncDirectionPull)
	case "s":
		return m, m.stopAll()
	case "w":
		return m.askSwitchAll()
	case "z":
		return m, pauseAllCmd(m.client, ViewDashboard)
	case "u":
		return m, resumeAllCmd(m.client, ViewDashboard)
	case "r":
		m.loading = true
		return m, tea.Batch(m.pollAll(), m.fetchNetwork(), m.fetchRemotes())
	case "enter":
		if row := m.profileTable.SelectedRow(); row != nil {
			slug := row.Key
			return m, func() tea.Msg {
				return NavigateMsg{Target: ViewProfileDetail, Param: slug}
			}
		}
	default:
		m.profileTable.Update(msg)
	}
	return m, nil
}

// askSwitchAll lists every mirror profile and asks before switching them
// all to two-way; the answer acts on exactly the listed profiles.
func (m DashboardModel) askSwitchAll() (tea.Model, tea.Cmd) {
	targets := mirrorProfiles(m.profiles)
	if len(targets) == 0 {
		return m, flash("Every profile already syncs two-way", false)
	}
	m.pendingSwitchAll = targets
	m.confirm = components.NewConfirm(switchAllPrompt(targets), "switch_all_two_way")
	return m, nil
}

// askSyncAll starts "Push all" / "Pull all": it captures the enabled
// profiles and asks the backend to preview each one (the preview changes
// nothing). The single confirmation opens once every preview has answered;
// its answer acts on exactly the captured profiles.
func (m DashboardModel) askSyncAll(dir api.SyncDirection) (tea.Model, tea.Cmd) {
	var targets []api.ProfileStatusResponse
	for _, p := range m.profiles {
		if p.Enabled {
			targets = append(targets, p)
		}
	}
	if len(targets) == 0 {
		return m, flash("No enabled profiles to "+string(dir), true)
	}
	m.syncAllSeq++
	check := &syncAllCheck{seq: m.syncAllSeq, dir: dir, targets: targets, previews: map[string]syncAllPreview{}}
	m.checkingAll = check
	client := m.client
	cmds := make([]tea.Cmd, 0, len(targets))
	for _, p := range targets {
		slug, seq := p.Slug, check.seq
		cmds = append(cmds, func() tea.Msg {
			resp, err := client.PreviewProfileSync(context.Background(), slug)
			return ActionResultMsg{ViewID: ViewDashboard, Action: "sync_all_preview", Err: err,
				Data: syncAllPreview{seq: seq, slug: slug, resp: resp, err: err}}
		})
	}
	return m, tea.Batch(cmds...)
}

// handleSyncAllPreview records one profile's preview and opens the prompt
// when the last one is in. Previews of a cancelled request are dropped.
func (m DashboardModel) handleSyncAllPreview(msg ActionResultMsg) (tea.Model, tea.Cmd) {
	res, _ := msg.Data.(syncAllPreview)
	check := m.checkingAll
	if check == nil || res.seq != check.seq {
		return m, nil
	}
	check.previews[res.slug] = res
	if len(check.previews) < len(check.targets) {
		return m, nil
	}
	m.checkingAll = nil
	m.pendingDir = check.dir
	m.pendingTargets = check.targets
	prompt := syncAllPrompt(check)
	m.confirm = components.NewConfirm(prompt, "sync_all")
	for _, p := range check.targets {
		pv := check.previews[p.Slug]
		if syncPreviewFailed(check.dir, pv.resp, pv.err) {
			// Every confirmed sync is sent with force: a profile without a
			// preview needs the explicit acknowledgement.
			m.confirm = components.NewConfirmWord(prompt, "sync_all", ForceWord, ForceWordHint)
			break
		}
	}
	return m, nil
}

// syncAllPrompt lists, per profile, what the push or pull would delete and
// replace (from its preview) and its delete limit.
func syncAllPrompt(check *syncAllCheck) string {
	dir := check.dir
	side := "on the remote"
	if dir == api.SyncDirectionPull {
		side = "locally"
	}
	lines := make([]string, 0, len(check.targets))
	paused, stops, twoWay := 0, 0, 0
	for _, p := range check.targets {
		pv := check.previews[p.Slug]
		maxDelete := p.MaxDelete
		var counts string
		if pv.err != nil || pv.resp == nil {
			counts = "counts unavailable"
		} else {
			maxDelete = pv.resp.MaxDelete
			c := pv.resp.Counts(dir)
			counts = fmt.Sprintf("deletes %d, replaces %d %s", c.Deletes, c.Replaces, side)
			if c.ExceedsMaxDelete && maxDelete != nil {
				counts += " " + theme.Glyphs().Warning + " stops at the delete limit"
				stops++
			}
			if pv.resp.Error != nil && *pv.resp.Error != "" {
				counts += " (may be incomplete: the comparison reported a problem)"
			}
		}
		limit := "no delete limit"
		if maxDelete != nil {
			limit = fmt.Sprintf("delete limit %d files", *maxDelete)
		}
		line := fmt.Sprintf("%s (%s): %s; %s", safeLine(p.Name), safeLine(p.Slug), counts, limit)
		if p.TwoWay() {
			line += ", two-way"
			twoWay++
		}
		if p.IntervalsPaused {
			line += ", automatic syncs paused"
			paused++
		}
		lines = append(lines, line)
	}
	var what string
	if dir == api.SyncDirectionPush {
		what = "Each push makes the remote match the local folder: remote files that are\nnot in the local folder are DELETED on the remote."
	} else {
		what = "Each pull makes the local folder match the remote: local files that are\nnot on the remote are DELETED locally."
	}
	var b strings.Builder
	fmt.Fprintf(&b, "%s all %d enabled profile(s)?\n\n  %s\n\n%s\n", directionVerb(dir), len(check.targets), strings.Join(lines, "\n  "), what)
	b.WriteString("Files a sync deletes or replaces are moved to .omnisync-trash.\n")
	b.WriteString("A sync that would delete more files than its profile's delete limit\nstops at the limit and deletes no more; a profile without a limit\ndeletes every file it needs to.\n")
	if stops > 0 {
		fmt.Fprintf(&b, "%d profile(s) above would stop at their delete limit.\n", stops)
	}
	if paused > 0 {
		b.WriteString("Profiles whose automatic syncs are paused are synced as well.\n")
	}
	if twoWay > 0 {
		b.WriteString("For two-way profiles this is a one-way override: changes that exist only\non the other side are replaced or deleted.\n")
	}
	b.WriteString("The counts are from just now; each sync acts on the files as they are\nwhen it runs.")
	return b.String()
}

func (m DashboardModel) stopAll() tea.Cmd {
	var busy []string
	for _, p := range m.profiles {
		if p.State.Busy() {
			busy = append(busy, p.Slug)
		}
	}
	if len(busy) == 0 {
		return flash("No sync is running", false)
	}
	client := m.client
	var cmds []tea.Cmd
	for _, slug := range busy {
		cmds = append(cmds, func() tea.Msg {
			_, err := client.StopProfileSync(context.Background(), slug)
			return ActionResultMsg{ViewID: ViewDashboard, Action: "stop_all", Err: err}
		})
	}
	return tea.Batch(cmds...)
}

// View renders the dashboard.
func (m DashboardModel) View() tea.View {
	if m.width == 0 {
		return tea.NewView("")
	}

	var b strings.Builder

	if m.confirm.Active {
		b.WriteString(m.confirm.View())
		return tea.NewView(b.String())
	}
	if c := m.checkingAll; c != nil {
		fmt.Fprintf(&b, "  Counting what a %s of %d profile(s) would change (nothing is changed yet): %d of %d done...\n\n",
			strings.ToLower(directionVerb(c.dir)), len(c.targets), len(c.previews), len(c.targets))
		b.WriteString(mutedText("  Esc: cancel"))
		return tea.NewView(b.String())
	}

	if err := cmp.Or(m.err, m.profilesErr); err != nil {
		b.WriteString(errorLine(err))
	}

	if m.loading && m.aggStatus == nil && m.err == nil {
		b.WriteString("  Loading dashboard...")
		return tea.NewView(b.String())
	}

	b.WriteString(m.renderAggregatePanel())
	b.WriteString("\n")
	b.WriteString(m.renderHealthPanel())
	b.WriteString("\n")
	b.WriteString(m.renderProfilesSection())
	if notice := renderMirrorProfilesNotice(m.profiles); notice != "" {
		b.WriteString("\n")
		b.WriteString(notice)
	}

	return tea.NewView(b.String())
}

func (m DashboardModel) renderAggregatePanel() string {
	labelStyle := lipgloss.NewStyle().Foreground(theme.Current.Muted)
	valueStyle := lipgloss.NewStyle().Foreground(theme.Current.Foreground)

	var b strings.Builder
	b.WriteString(headerText("  Sync Status"))
	b.WriteString("\n")

	if m.aggStatus == nil {
		b.WriteString(labelStyle.Render("  No data"))
		b.WriteString("\n")
		return b.String()
	}

	s := m.aggStatus
	lastSync := ""
	activeCount := 0
	for _, ps := range s.ProfilesSummary {
		if ps.LastSync != nil && *ps.LastSync > lastSync {
			lastSync = *ps.LastSync
		}
		if ps.State.Busy() {
			activeCount++
		}
	}
	last := "never"
	if lastSync != "" {
		last = formatTime(lastSync)
	}

	fmt.Fprintf(&b, "  State: %s  Pending: %s\n",
		components.RenderBadge(string(s.OverallState)),
		valueStyle.Render(fmt.Sprintf("%d", s.TotalPendingChanges)))

	fmt.Fprintf(&b, "  Profiles: %s  Syncing: %s  Last sync: %s\n",
		valueStyle.Render(fmt.Sprintf("%d", len(s.ProfilesSummary))),
		valueStyle.Render(fmt.Sprintf("%d", activeCount)),
		labelStyle.Render(last))

	warn := lipgloss.NewStyle().Foreground(theme.Current.Warning)
	for _, p := range s.PausedProfiles {
		if p.UserPaused && p.PendingChanges == 0 {
			b.WriteString(labelStyle.Render(fmt.Sprintf("  %s: paused by you (u resumes all)", safeLine(p.Name))))
		} else {
			b.WriteString(warn.Render(fmt.Sprintf("  %s %s: intervals paused (%d unresolved differences)", theme.Glyphs().Warning, safeLine(p.Name), p.PendingChanges)))
		}
		b.WriteString("\n")
	}
	for _, p := range s.ProfilesSummary {
		if line := progressLine(p.Progress); line != "" && p.State.Busy() {
			fmt.Fprintf(&b, "  %s: %s\n", safeLine(p.Name), valueStyle.Render(line))
		}
	}
	for _, p := range s.ProfilesSummary {
		if p.ResyncRequired {
			why := ""
			if p.LastError != nil && *p.LastError != "" {
				why = " (" + safeLine(*p.LastError) + ")"
			}
			b.WriteString(warn.Render(fmt.Sprintf("  %s %s: resync required, two-way syncing is paused%s; open the profile and press R",
				theme.Glyphs().Warning, safeLine(p.Name), why)))
			b.WriteString("\n")
		}
	}

	return b.String()
}

func (m DashboardModel) renderHealthPanel() string {
	labelStyle := lipgloss.NewStyle().Foreground(theme.Current.Muted)

	var b strings.Builder
	b.WriteString(headerText("  Health"))
	b.WriteString("\n")

	if m.health == nil {
		b.WriteString(labelStyle.Render("  Checking..."))
		b.WriteString("\n")
		return b.String()
	}

	h := m.health
	status := checkMark(h.Healthy()) + " " + h.Status
	fmt.Fprintf(&b, "  status: %s  rclone: %s  database: %s  uptime: %s\n",
		status,
		checkMark(h.RcloneInstalled),
		checkMark(h.DatabaseOK),
		labelStyle.Render(formatUptime(h.UptimeSeconds)))

	b.WriteString(m.renderRemotesLine())

	switch {
	case m.network != nil:
		fmt.Fprintf(&b, "  dns: %s  internet: %s  rclone network: %s\n",
			checkMark(m.network.DNSGoogle.OK),
			checkMark(m.network.HttpxCloudflare.OK),
			checkMark(m.network.RcloneNetwork.OK))

	case m.networkErr != nil:
		b.WriteString(labelStyle.Render("  network check failed: " + safeLine(m.networkErr.Error())))
		b.WriteString("\n")
	default:
		b.WriteString(labelStyle.Render("  network: checking..."))
		b.WriteString("\n")
	}

	return b.String()
}

// renderRemotesLine shows GET /health/remotes: one mark per remote that a
// running profile uses (GET /health does not contact providers).
func (m DashboardModel) renderRemotesLine() string {
	labelStyle := lipgloss.NewStyle().Foreground(theme.Current.Muted)
	switch {
	case m.remotes != nil && len(m.remotes.Remotes) == 0:
		return labelStyle.Render("  remotes: none in use by a running profile") + "\n"
	case m.remotes != nil:
		parts := make([]string, 0, len(m.remotes.Remotes))
		for _, r := range m.remotes.Remotes {
			parts = append(parts, safeLine(r.Remote)+": "+checkMark(r.Accessible))
		}
		return "  remotes: " + strings.Join(parts, "  ") + "\n"
	case m.remotesErr != nil:
		return labelStyle.Render("  remotes: unknown (check failed: "+safeLine(m.remotesErr.Error())+")") + "\n"
	default:
		return labelStyle.Render("  remotes: checking...") + "\n"
	}
}

func (m DashboardModel) renderProfilesSection() string {
	var b strings.Builder
	b.WriteString(headerText("  Profiles"))
	b.WriteString("\n")
	b.WriteString(m.profileTable.View())

	errStyle := lipgloss.NewStyle().Foreground(theme.Current.Error)
	for _, p := range m.profiles {
		if p.State != api.SyncStateError {
			continue
		}
		reason := "no reason reported"
		if p.LastError != nil && *p.LastError != "" {
			reason = safeLine(*p.LastError)
		}
		b.WriteString(errStyle.Render(fmt.Sprintf("  %s %s: %s", theme.Glyphs().Cross, safeLine(p.Name), reason)))
		b.WriteString("\n")
	}

	return b.String()
}

func (m *DashboardModel) updateProfileTable() {
	var rows []components.Row
	for _, p := range m.profiles {
		rows = append(rows, components.Row{
			Key: p.Slug,
			Values: []string{
				p.Name,
				syncModeLabel(p.SyncMode),
				stateLabel(p.State, p.LastError, p.ResyncRequired),
				p.LocalDir,
				p.RemoteDir,
				formatTimePtr(p.LastSync, "never"),
				fmt.Sprintf("%d", p.PendingChanges),
			},
			Colors: cellColors(6, 1, stateColor(p.State, p.ResyncRequired)),
		})
	}
	m.profileTable.SetRows(rows)
}

// --- Poll commands ---

func (m DashboardModel) pollAll() tea.Cmd {
	return tea.Batch(
		m.fetchAggregate(),
		m.fetchHealth(),
		m.fetchProfiles(),
	)
}

func (m DashboardModel) fetchAggregate() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.AggregateStatus(context.Background())
		return PollResultMsg{ViewID: ViewDashboard, Data: data, Err: err}
	}
}

func (m DashboardModel) fetchHealth() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.Health(context.Background())
		return PollResultMsg{ViewID: ViewDashboard, Data: data, Err: err}
	}
}

// fetchNetwork runs the slow network diagnostics; only on open and on 'r'.
func (m DashboardModel) fetchNetwork() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.NetworkHealth(context.Background())
		return PollResultMsg{ViewID: ViewDashboard, Data: data, Err: err}
	}
}

// fetchRemotes checks the remotes of running profiles (provider calls); like
// the network probe, only on open and on 'r'.
func (m DashboardModel) fetchRemotes() tea.Cmd {
	client := m.client
	return func() tea.Msg {
		data, err := client.RemotesHealth(context.Background())
		return PollResultMsg{ViewID: ViewDashboard, Data: data, Err: err}
	}
}

func (m DashboardModel) fetchProfiles() tea.Cmd {
	return fetchProfilesCmd(m.client, ViewDashboard)
}

// fetchProfilesCmd lists profiles; each carries its own last_error.
func fetchProfilesCmd(client *api.Client, view ViewID) tea.Cmd {
	return func() tea.Msg {
		data, err := client.ListProfiles(context.Background())
		if err != nil {
			return PollResultMsg{ViewID: view, Data: []api.ProfileStatusResponse(nil), Err: err}
		}
		if data == nil {
			data = []api.ProfileStatusResponse{}
		}
		return PollResultMsg{ViewID: view, Data: data}
	}
}

func directionVerb(dir api.SyncDirection) string {
	switch dir {
	case api.SyncDirectionPull:
		return "Pull"
	case api.SyncDirectionTwoWay:
		return "Two-way sync"
	}
	return "Push"
}

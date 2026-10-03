package ui

import (
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/pan-fire/OmniSync/tui/internal/api"
)

// ViewID identifies each view in the application.
type ViewID int

const (
	ViewDashboard     ViewID = iota // key: 1
	ViewProfiles                    // key: 2
	ViewJobs                        // key: 3
	ViewLogs                        // key: 4
	ViewConflicts                   // key: 5
	ViewRemotes                     // key: 6
	ViewNotifications               // key: 7
	ViewConfig                      // key: 8
	ViewProfileDetail               // sub-view of Profiles
	ViewWizard                      // sub-view of Remotes
)

// ViewCount is the number of main views accessible by number keys.
const ViewCount = 8

// ViewLabels maps ViewID to display name.
var ViewLabels = map[ViewID]string{
	ViewDashboard:     "Dashboard",
	ViewProfiles:      "Profiles",
	ViewJobs:          "Jobs",
	ViewLogs:          "Logs",
	ViewConflicts:     "Conflicts",
	ViewRemotes:       "Remotes",
	ViewNotifications: "Notifications",
	ViewConfig:        "Config",
	ViewProfileDetail: "Profile Detail",
	ViewWizard:        "Wizard",
}

// PollResultMsg carries the result of a background API read. The app
// delivers it to the view named by ViewID, active or not.
type PollResultMsg struct {
	ViewID ViewID
	Data   interface{}
	Err    error
}

// ActionResultMsg carries the result of a user-initiated API action. The app
// delivers it to the view named by ViewID, so an action that finishes after
// the user moved on (a long sync, say) still reaches the view that started
// it.
type ActionResultMsg struct {
	ViewID ViewID
	Action string
	Err    error
	Data   interface{}
}

// FlashMsg triggers a flash message in the status bar.
type FlashMsg struct {
	Text    string
	IsError bool
}

// NavigateMsg requests navigation to a different view.
type NavigateMsg struct {
	Target ViewID
	Param  string // profile slug for ViewProfileDetail
	// Payload, if set, is delivered to the target view before it is shown
	// (e.g. ReconnectRemoteMsg for the wizard).
	Payload tea.Msg
}

// ReconnectRemoteMsg puts the wizard into reconnect mode for an existing
// OAuth remote: it signs the remote in again and stores only the new token.
type ReconnectRemoteMsg struct {
	Name       string
	ProviderID string
}

// OpenProfileMsg tells the Profile Detail view which profile to show. The
// app sends it before navigating, so it works whatever the view's receiver
// type is.
type OpenProfileMsg struct {
	Slug string
}

// ConnectionStatusMsg updates backend connection state.
type ConnectionStatusMsg struct {
	Connected bool
	LatencyMs int64
	// Healthy is the backend's own verdict (GET /health status == "ok").
	Healthy bool
	// OverallState is the aggregate sync state (GET /sync/status/aggregate)
	// for the top bar; "" when unknown.
	OverallState api.SyncState
	Err          error
}

// TickMsg is the app's single timer tick. The app polls the active view and
// re-checks backend health from it.
type TickMsg struct {
	Time time.Time
}

// PollSpec declares a view's polling behavior.
type PollSpec struct {
	FastInterval time.Duration               // interval while something is running
	SlowInterval time.Duration               // interval when idle
	IsActive     func(data interface{}) bool // picks the interval from the view's last poll data
}

// KeyHinter is implemented by views that name their keys in the bottom bar.
// KeyHints returns one short line for the view's current mode, e.g.
// "Enter:detail  f:filter  r:refresh".
type KeyHinter interface {
	KeyHints() string
}

// InputCapturer is implemented by views that can own the keyboard, for
// example while a form, text field or confirmation prompt is open. While
// CapturesInput is true the app passes every key except Ctrl+C to the view
// instead of treating q, ?, 1-8, Tab and Ctrl+T as global keys.
type InputCapturer interface {
	CapturesInput() bool
}

package ui

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/pan-fire/OmniSync/tui/internal/api"
	"github.com/pan-fire/OmniSync/tui/internal/ui/components"
)

// Settings forms of the channels that work on a headless server. Secrets
// are never shown: their field starts empty, and empty keeps the stored one.

const formChannelSettings = "channel_settings"

const secretKeptHelp = "•••• set — leave empty to keep"

var yesNo = []string{"no", "yes"}

var smtpSecurity = []string{"starttls", "tls", "none"}

// configurableChannel reports whether the channel has a settings form.
func configurableChannel(name string) bool {
	switch name {
	case "webhook", "ntfy", "email":
		return true
	}
	return false
}

func boolOption(v bool) string {
	if v {
		return "yes"
	}
	return "no"
}

func secretHelp(set bool, unset string) string {
	if set {
		return secretKeptHelp
	}
	return unset
}

// channelSettingsForm builds the form for the channel's current settings.
func channelSettingsForm(name string, cfg api.ChannelConfig) components.Form {
	var fields []components.Field
	title := ""
	switch name {
	case "webhook":
		title = "Webhook settings"
		s := cfg.Webhook
		if s == nil {
			s = &api.WebhookSettingsView{}
		}
		headerName, headerSet := "", false
		if len(s.Headers) > 0 {
			headerName, headerSet = s.Headers[0].Name, s.Headers[0].ValueSet
		}
		fields = []components.Field{
			{Name: "url", Label: "URL", Type: components.FieldText, Value: s.URL, Required: true,
				Help: "https://… — the JSON event is POSTed here (redirects are not followed)"},
			{Name: "allow_http", Label: "Allow http", Type: components.FieldDropdown,
				Value: boolOption(s.AllowHTTP), Options: yesNo,
				Help: "Plain http only to loopback or private addresses, e.g. Home Assistant on the LAN"},
			{Name: "header_name", Label: "Header name", Type: components.FieldText, Value: headerName,
				Help: "Optional, e.g. Authorization; empty removes it"},
			{Name: "header_value", Label: "Header value", Type: components.FieldPassword,
				Help: secretHelp(headerSet, "e.g. Bearer <token>; stored as a secret")},
		}
	case "ntfy":
		title = "ntfy settings"
		s := cfg.Ntfy
		if s == nil {
			s = &api.NtfySettingsView{Server: "https://ntfy.sh"}
		}
		fields = []components.Field{
			{Name: "server", Label: "Server", Type: components.FieldText, Value: s.Server, Required: true,
				Help: "https://ntfy.sh or your own server"},
			{Name: "topic", Label: "Topic", Type: components.FieldText, Value: s.Topic, Required: true,
				Help: "Letters, digits, - and _; anyone who knows a topic on ntfy.sh can read it"},
			{Name: "allow_http", Label: "Allow http", Type: components.FieldDropdown,
				Value: boolOption(s.AllowHTTP), Options: yesNo},
			{Name: "token", Label: "Access token", Type: components.FieldPassword,
				Help: secretHelp(s.TokenSet, "Optional (tk_…); or a user and password below")},
			{Name: "username", Label: "Username", Type: components.FieldText, Value: s.Username},
			{Name: "password", Label: "Password", Type: components.FieldPassword,
				Help: secretHelp(s.PasswordSet, "Optional")},
		}
	case "email":
		title = "Email (SMTP) settings"
		s := cfg.Email
		if s == nil {
			s = &api.EmailSettingsView{Port: 587, Security: "starttls"}
		}
		port := ""
		if s.Port > 0 {
			port = strconv.Itoa(s.Port)
		}
		fields = []components.Field{
			{Name: "host", Label: "SMTP host", Type: components.FieldText, Value: s.Host, Required: true},
			{Name: "port", Label: "Port", Type: components.FieldText, Value: port, Required: true,
				Help: "587 for STARTTLS, 465 for TLS, 25 without encryption"},
			{Name: "security", Label: "Security", Type: components.FieldDropdown, Value: s.Security, Options: smtpSecurity},
			{Name: "username", Label: "Username", Type: components.FieldText, Value: s.Username},
			{Name: "password", Label: "Password", Type: components.FieldPassword,
				Help: secretHelp(s.PasswordSet, "Optional; stored as a secret")},
			{Name: "from_addr", Label: "From", Type: components.FieldText, Value: s.FromAddr, Required: true},
			{Name: "to", Label: "To", Type: components.FieldText, Value: strings.Join(s.To, ", "), Required: true,
				Help: "One or more addresses, separated by commas"},
		}
	}
	form := components.NewFormWithID(formChannelSettings, title, fields)
	form.Intro = "Secrets are never shown; leave a secret empty to keep it."
	return form
}

func optString(v string) *string { return &v }

// channelSettingsUpdate turns the submitted form into a partial update.
func channelSettingsUpdate(name string, cfg api.ChannelConfig, values map[string]string) (api.ChannelConfigUpdate, error) {
	trim := func(key string) string { return strings.TrimSpace(values[key]) }
	var upd api.ChannelConfigUpdate
	switch name {
	case "webhook":
		allow := values["allow_http"] == "yes"
		var headers []api.WebhookHeaderUpdate
		if cfg.Webhook != nil {
			// The form edits the first header; the others are kept as they are.
			for i, h := range cfg.Webhook.Headers {
				if i > 0 {
					headers = append(headers, api.WebhookHeaderUpdate{Name: h.Name})
				}
			}
		}
		if hn := trim("header_name"); hn != "" {
			headers = append([]api.WebhookHeaderUpdate{{Name: hn, Value: values["header_value"]}}, headers...)
		}
		if headers == nil {
			headers = []api.WebhookHeaderUpdate{}
		}
		upd.Webhook = &api.WebhookSettingsUpdate{URL: optString(trim("url")), AllowHTTP: &allow, Headers: &headers}
	case "ntfy":
		allow := values["allow_http"] == "yes"
		n := &api.NtfySettingsUpdate{
			Server: optString(trim("server")), Topic: optString(trim("topic")), AllowHTTP: &allow,
			Username: optString(trim("username")),
		}
		if v := trim("token"); v != "" {
			n.Token = &v
		}
		if v := values["password"]; v != "" {
			n.Password = &v
		}
		if n.Username != nil && *n.Username == "" {
			n.Clear = append(n.Clear, "password") // no user: no password either
		}
		upd.Ntfy = n
	case "email":
		port, err := strconv.Atoi(trim("port"))
		if err != nil || port < 1 || port > 65535 {
			return upd, fmt.Errorf("port must be a number from 1 to 65535")
		}
		var to []string
		for _, addr := range strings.Split(values["to"], ",") {
			if a := strings.TrimSpace(addr); a != "" {
				to = append(to, a)
			}
		}
		if len(to) == 0 {
			return upd, fmt.Errorf("give at least one recipient")
		}
		security := values["security"]
		e := &api.EmailSettingsUpdate{
			Host: optString(trim("host")), Port: &port, Security: &security,
			Username: optString(trim("username")), FromAddr: optString(trim("from_addr")), To: &to,
		}
		if v := values["password"]; v != "" {
			e.Password = &v
		}
		if e.Username != nil && *e.Username == "" {
			e.Clear = []string{"password"}
		}
		upd.Email = e
	}
	return upd, nil
}

// channelSettingsSummary describes the stored settings in one line (no secrets).
func channelSettingsSummary(name string, cfg api.ChannelConfig) string {
	set := func(b bool) string {
		if b {
			return "set"
		}
		return "not set"
	}
	switch name {
	case "webhook":
		if cfg.Webhook == nil || cfg.Webhook.URL == "" {
			return "Not configured — press c"
		}
		var hs []string
		for _, h := range cfg.Webhook.Headers {
			hs = append(hs, h.Name+" ("+set(h.ValueSet)+")")
		}
		text := "URL: " + cfg.Webhook.URL
		if len(hs) > 0 {
			text += "  Headers: " + strings.Join(hs, ", ")
		}
		return text
	case "ntfy":
		if cfg.Ntfy == nil || cfg.Ntfy.Topic == "" {
			return "Not configured — press c"
		}
		return fmt.Sprintf("%s/%s  token: %s", strings.TrimRight(cfg.Ntfy.Server, "/"), cfg.Ntfy.Topic, set(cfg.Ntfy.TokenSet))
	case "email":
		if cfg.Email == nil || cfg.Email.Host == "" {
			return "Not configured — press c"
		}
		return fmt.Sprintf("%s:%d (%s) → %s  password: %s", cfg.Email.Host, cfg.Email.Port, cfg.Email.Security,
			strings.Join(cfg.Email.To, ", "), set(cfg.Email.PasswordSet))
	}
	return ""
}

package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/pan-fire/OmniSync/tui/internal/logging"
)

// maxLoggedBody is how much of a malformed response body the debug log
// keeps.
const maxLoggedBody = 2048

// DefaultReadTimeout bounds ordinary calls: listings, status reads and small
// updates that the backend answers right away.
const DefaultReadTimeout = 15 * time.Second

// ProbeTimeout bounds calls that make the backend reach out to a remote or
// the network before answering (remote tests, test syncs, /health/network).
const ProbeTimeout = 2 * time.Minute

// timeoutClass picks the deadline a call gets when its context has none of
// its own.
type timeoutClass int

const (
	// classRead uses the client's read timeout.
	classRead timeoutClass = iota
	// classProbe uses ProbeTimeout.
	classProbe
	// classLong has no deadline: the backend answers when the work is done
	// (a sync, a diff, a backup), which can take many minutes. Cancel the
	// context to stop waiting.
	classLong
)

// Client is the HTTP client for the OmniSync backend API.
type Client struct {
	baseURL     string
	httpClient  *http.Client
	apiKey      string
	version     string
	readTimeout time.Duration
}

// NewClient creates a new API client. Every request carries
// "Authorization: Bearer <apiKey>" when apiKey is set.
func NewClient(baseURL, apiKey, version string) *Client {
	return &Client{
		baseURL:     strings.TrimRight(baseURL, "/"),
		httpClient:  &http.Client{},
		apiKey:      apiKey,
		version:     version,
		readTimeout: DefaultReadTimeout,
	}
}

// SetReadTimeout changes the deadline used for ordinary (short) calls.
func (c *Client) SetReadTimeout(d time.Duration) {
	c.readTimeout = d
}

// BaseURL returns the backend address the client talks to, without a
// trailing slash.
func (c *Client) BaseURL() string {
	return c.baseURL
}

// SetBaseURL updates the base URL of the client.
func (c *Client) SetBaseURL(u string) {
	c.baseURL = strings.TrimRight(u, "/")
}

func (c *Client) withDeadline(ctx context.Context, class timeoutClass) (context.Context, context.CancelFunc, time.Duration) {
	var d time.Duration
	switch class {
	case classRead:
		d = c.readTimeout
	case classProbe:
		d = ProbeTimeout
	}
	if d <= 0 {
		inner, cancel := context.WithCancel(ctx)
		return inner, cancel, 0
	}
	inner, cancel := context.WithTimeout(ctx, d)
	return inner, cancel, d
}

// call performs one request. in (if non-nil) is sent as the JSON body; out
// (if non-nil) receives the decoded JSON response.
func (c *Client) call(ctx context.Context, class timeoutClass, method, path string, in, out any) error {
	if ctx == nil {
		ctx = context.Background()
	}
	ctx, cancel, limit := c.withDeadline(ctx, class)
	defer cancel()

	var body io.Reader
	if in != nil {
		data, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "osync-tui/"+c.version)
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}

	log := logging.L()
	start := time.Now()
	log.Debug("api request", "method", method, "path", path)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		log.Debug("api request failed", "method", method, "path", path, "error", err.Error())
		if limit > 0 && errors.Is(err, context.DeadlineExceeded) {
			return fmt.Errorf("%s %s: no answer within %s: %w", method, path, limit, err)
		}
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	log.Debug("api response", "method", method, "path", path, "status", resp.StatusCode,
		"ms", time.Since(start).Milliseconds())

	if resp.StatusCode >= 400 {
		apiErr := parseError(resp)
		log.Debug("api error", "method", method, "path", path, "status", resp.StatusCode, "detail", apiErr.Error())
		return apiErr
	}
	if out == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Debug("api response read failed", "method", method, "path", path, "error", err.Error())
		return fmt.Errorf("reading the response from %s: %w", path, err)
	}
	if err := json.Unmarshal(data, out); err != nil {
		// The raw body goes to the log only; the UI shows a short message.
		log.Warn("malformed api response", "method", method, "path", path, "status", resp.StatusCode,
			"error", err.Error(), "body", logging.Truncate(string(data), maxLoggedBody))
		return fmt.Errorf("unexpected response from %s: %w", path, err)
	}
	return nil
}

// fetch performs a call and decodes a JSON object into a new T.
func fetch[T any](ctx context.Context, c *Client, class timeoutClass, method, path string, in any) (*T, error) {
	var out T
	if err := c.call(ctx, class, method, path, in, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// fetchList performs a call and decodes a JSON array.
func fetchList[T any](ctx context.Context, c *Client, class timeoutClass, method, path string, in any) ([]T, error) {
	var out []T
	if err := c.call(ctx, class, method, path, in, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// parseError turns an error response into an *ApiError. The backend answers
// errors with {"detail": "<message>", "code": "<code>", "details": {...}}
// (docs/api-errors.md); any other body (a proxy's HTML page, an older
// backend's object detail) is kept as text.
func parseError(resp *http.Response) error {
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	apiErr := &ApiError{StatusCode: resp.StatusCode, Detail: strings.TrimSpace(string(body))}
	// A field of another type is skipped and the rest still decoded; a body
	// that is not JSON leaves the envelope empty.
	var envelope ErrorResponse
	_ = json.Unmarshal(body, &envelope)
	if envelope.Detail != "" {
		apiErr.Detail = envelope.Detail
	}
	apiErr.Code, apiErr.Details = envelope.Code, envelope.Details
	return apiErr
}

// escapedPath builds a URL path from a format string, escaping every string
// argument as a single path segment.
func escapedPath(format string, args ...any) string {
	escaped := make([]any, len(args))
	for i, a := range args {
		if s, ok := a.(string); ok {
			escaped[i] = url.PathEscape(s)
		} else {
			escaped[i] = a
		}
	}
	return fmt.Sprintf(format, escaped...)
}

// withQuery appends encoded query values to a path.
func withQuery(path string, q url.Values) string {
	if len(q) == 0 {
		return path
	}
	return path + "?" + q.Encode()
}

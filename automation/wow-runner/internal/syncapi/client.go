// Package syncapi implements the authenticated snapshot ingestion protocol.
// It never stores credentials or treats an accepted upload as publication.
package syncapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"
)

type Manifest struct {
	SchemaVersion    int    `json:"schema_version"`
	CompressedSHA256 string `json:"compressed_sha256"`
	SizeBytes        int64  `json:"size_bytes"`
	RegionID         int64  `json:"region_id"`
	RealmID          int64  `json:"realm_id"`
	CapturedAt       int64  `json:"captured_at"`
	RecordCount      int64  `json:"record_count"`
}

type Job struct {
	ID        string          `json:"id"`
	Phase     string          `json:"phase"`
	StatusURL string          `json:"status_url"`
	UploadURL string          `json:"upload_url"`
	Error     string          `json:"error,omitempty"`
	Result    json.RawMessage `json:"result,omitempty"`
}

var jobID = regexp.MustCompile(`^[a-f0-9]{32}$`)
var ErrBusy = errors.New("sync server busy")
var ErrAuthentication = errors.New("sync authentication rejected")

type BusyError struct{ RetryAfter time.Duration }

func (e *BusyError) Error() string { return fmt.Sprintf("%s; retry after %s", ErrBusy, e.RetryAfter) }
func (e *BusyError) Unwrap() error { return ErrBusy }

type Client struct {
	origin string
	token  string
	http   *http.Client
}

// New accepts an HTTPS origin only. Redirects are disabled to protect the token.
func New(origin, token string, transport http.RoundTripper) (*Client, error) {
	u, e := url.Parse(origin)
	if e != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return nil, fmt.Errorf("sync endpoint must be an HTTPS origin")
	}
	if strings.TrimSpace(token) == "" || strings.ContainsAny(token, "\r\n") {
		return nil, fmt.Errorf("missing or invalid sync token")
	}
	h := &http.Client{Transport: transport, Timeout: 10 * time.Minute, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	return &Client{origin: strings.TrimRight(u.String(), "/"), token: token, http: h}, nil
}

func (m Manifest) Validate() error {
	if m.SchemaVersion != 1 || !regexp.MustCompile(`^[a-f0-9]{64}$`).MatchString(m.CompressedSHA256) || m.SizeBytes <= 0 || m.SizeBytes > 128<<20 || m.RegionID < 1 || m.RegionID > 5 || m.RealmID < 1 || m.CapturedAt < 1 || m.RecordCount < 1 || m.RecordCount > 1_000_000 {
		return fmt.Errorf("invalid sync manifest")
	}
	return nil
}

func (c *Client) request(ctx context.Context, method, path, contentType string, body io.Reader, size int64) (Job, error) {
	var j Job
	req, e := http.NewRequestWithContext(ctx, method, c.origin+path, body)
	if e != nil {
		return j, e
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if size >= 0 {
		req.ContentLength = size
	}
	resp, e := c.http.Do(req)
	if e != nil {
		return j, fmt.Errorf("sync request failed: %w", e)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusConflict {
		delay := 10 * time.Second
		if n, e := time.ParseDuration(resp.Header.Get("Retry-After") + "s"); e == nil && n > 0 && n <= time.Hour {
			delay = n
		}
		return j, &BusyError{RetryAfter: delay}
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return j, ErrAuthentication
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return j, fmt.Errorf("sync server HTTP %d", resp.StatusCode)
	}
	d := json.NewDecoder(io.LimitReader(resp.Body, 1<<20))
	if e = d.Decode(&j); e != nil {
		return j, fmt.Errorf("invalid job response: %w", e)
	}
	if !jobID.MatchString(j.ID) {
		return j, fmt.Errorf("invalid job ID")
	}
	base := "/api/sync/jobs/" + j.ID
	if j.StatusURL != base || j.UploadURL != base+"/snapshot" {
		return j, fmt.Errorf("invalid job URLs")
	}
	return j, nil
}

func (c *Client) Create(ctx context.Context, m Manifest) (Job, error) {
	if e := m.Validate(); e != nil {
		return Job{}, e
	}
	b, e := json.Marshal(m)
	if e != nil {
		return Job{}, e
	}
	return c.request(ctx, http.MethodPost, "/api/sync/jobs", "application/json", strings.NewReader(string(b)), int64(len(b)))
}

func (c *Client) Status(ctx context.Context, id string) (Job, error) {
	if !jobID.MatchString(id) {
		return Job{}, fmt.Errorf("invalid job ID")
	}
	return c.request(ctx, http.MethodGet, "/api/sync/jobs/"+id, "", nil, 0)
}

func (c *Client) Upload(ctx context.Context, id, path string, m Manifest) (Job, error) {
	if !jobID.MatchString(id) {
		return Job{}, fmt.Errorf("invalid job ID")
	}
	if e := m.Validate(); e != nil {
		return Job{}, e
	}
	f, e := os.Open(path)
	if e != nil {
		return Job{}, e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return Job{}, e
	}
	if !info.Mode().IsRegular() || info.Size() != m.SizeBytes {
		return Job{}, fmt.Errorf("snapshot size changed")
	}
	h := sha256.New()
	if _, e = io.Copy(h, f); e != nil {
		return Job{}, e
	}
	if hex.EncodeToString(h.Sum(nil)) != m.CompressedSHA256 {
		return Job{}, fmt.Errorf("snapshot checksum changed")
	}
	if _, e = f.Seek(0, io.SeekStart); e != nil {
		return Job{}, e
	}
	return c.request(ctx, http.MethodPut, "/api/sync/jobs/"+id+"/snapshot", "application/gzip", io.LimitReader(f, m.SizeBytes), m.SizeBytes)
}

// Package syncqueue maintains a durable, single-worker snapshot upload outbox.
package syncqueue

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/snapshot"
	"wow-auction/automation/wow-runner/internal/syncapi"
)

type Config struct {
	Enabled   bool   `json:"enabled"`
	Server    string `json:"server"`
	TokenFile string `json:"tokenFile"`
	QueueDir  string `json:"queueDir"`
	ScanRoot  string `json:"-"`
}
type Job struct {
	Version     int             `json:"version"`
	SnapshotSHA string          `json:"snapshotSha256"`
	ArchiveSHA  string          `json:"archiveSha256"`
	Source      string          `json:"source"`
	Archive     string          `json:"archive"`
	CapturedAt  int64           `json:"capturedAt"`
	Phase       string          `json:"phase"`
	PayloadSHA  string          `json:"payloadSha256,omitempty"`
	RemoteID    string          `json:"remoteId,omitempty"`
	Failures    int             `json:"failures,omitempty"`
	NextAttempt time.Time       `json:"nextAttempt,omitempty"`
	LastError   string          `json:"lastError,omitempty"`
	Result      json.RawMessage `json:"result,omitempty"`
}

func hashFile(path string) (string, error) {
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	if _, e = io.Copy(h, f); e != nil {
		return "", e
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func validSHA(s string) bool {
	b, e := hex.DecodeString(s)
	return e == nil && len(b) == 32 && s == strings.ToLower(s)
}

func atomicJSON(path string, v any, exclusive bool) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".sync-*.tmp")
	if e != nil {
		return e
	}
	tmp := f.Name()
	defer os.Remove(tmp)
	if e = f.Chmod(0600); e == nil {
		_, e = f.Write(append(b, '\n'))
	}
	if e == nil {
		e = f.Sync()
	}
	closeErr := f.Close()
	if e != nil {
		return e
	}
	if closeErr != nil {
		return closeErr
	}
	if exclusive {
		return os.Link(tmp, path)
	}
	return os.Rename(tmp, path)
}

// Enqueue is idempotent and publishes the job only after its complete record is saved.
func Enqueue(root, source, archive, snapshotSHA, archiveSHA string, capturedAt int64) error {
	if !filepath.IsAbs(root) || !filepath.IsAbs(source) || !filepath.IsAbs(archive) || !validSHA(snapshotSHA) || !validSHA(archiveSHA) || capturedAt <= 0 {
		return fmt.Errorf("invalid sync queue entry")
	}
	dir := filepath.Join(root, snapshotSHA)
	if e := os.MkdirAll(dir, 0700); e != nil {
		return e
	}
	path := filepath.Join(dir, "job.json")
	j := Job{Version: 1, SnapshotSHA: snapshotSHA, ArchiveSHA: archiveSHA, Source: source, Archive: archive, Phase: "pending", CapturedAt: capturedAt}
	if e := atomicJSON(path, j, true); e != nil {
		if !os.IsExist(e) {
			return e
		}
		old, err := load(path)
		if err != nil {
			return err
		}
		if old.SnapshotSHA != j.SnapshotSHA || old.CapturedAt != j.CapturedAt {
			return fmt.Errorf("queue identity conflict")
		}
	}
	return nil
}

func load(path string) (Job, error) {
	var j Job
	b, e := os.ReadFile(path)
	if e != nil {
		return j, e
	}
	if e = json.Unmarshal(b, &j); e != nil {
		return j, e
	}
	if j.Version != 1 || !validSHA(j.SnapshotSHA) || !validSHA(j.ArchiveSHA) || !filepath.IsAbs(j.Source) || !filepath.IsAbs(j.Archive) || j.CapturedAt <= 0 {
		return j, fmt.Errorf("invalid queue job")
	}
	return j, nil
}

type Worker struct {
	Config    Config
	Transport func(string) (*syncapi.Client, error)
	Now       func() time.Time
}

func (w *Worker) client() (*syncapi.Client, error) {
	secret, e := os.ReadFile(w.Config.TokenFile)
	if e != nil {
		return nil, fmt.Errorf("sync token unavailable: %w", e)
	}
	if w.Transport != nil {
		return w.Transport(strings.TrimSpace(string(secret)))
	}
	return syncapi.New(w.Config.Server, strings.TrimSpace(string(secret)), nil)
}
func (w *Worker) now() time.Time {
	if w.Now != nil {
		return w.Now()
	}
	return time.Now()
}

// Step advances a single job by at most one network operation. A crash leaves
// both its immutable payload and job ID available for idempotent recovery.
func (w *Worker) Step(ctx context.Context, path string) error {
	j, e := load(path)
	if e != nil {
		return e
	}
	if j.Phase == "succeeded" || j.Phase == "attention" || w.now().Before(j.NextAttempt) {
		return nil
	}
	dir := filepath.Dir(path)
	payload := filepath.Join(dir, "snapshot.jsonl.gz")
	if j.PayloadSHA == "" {
		archiveHash, err := hashFile(j.Archive)
		if err != nil {
			return w.deferJob(path, &j, err, 5*time.Minute)
		}
		if archiveHash != j.ArchiveSHA {
			return w.attention(path, &j, "archive checksum mismatch")
		}
		source := j.Source
		if sourceHash, hashErr := hashFile(source); hashErr != nil || sourceHash != j.SnapshotSHA {
			source, err = recoverSource(dir, j.Archive, j.SnapshotSHA)
			if err != nil {
				return w.deferJob(path, &j, fmt.Errorf("recover source from archive: %w", err), 5*time.Minute)
			}
		}
		if _, err = os.Stat(payload); os.IsNotExist(err) {
			if err = snapshot.ExportJSONLExpected(source, payload, j.SnapshotSHA); err != nil {
				return w.deferJob(path, &j, err, 5*time.Minute)
			}
		} else if err != nil {
			return w.deferJob(path, &j, err, 5*time.Minute)
		}
		m, err := syncapi.Inspect(payload)
		if err != nil {
			return w.attention(path, &j, "invalid prepared snapshot: "+err.Error())
		}
		if m.CapturedAt != j.CapturedAt {
			return w.attention(path, &j, "prepared capture timestamp differs from archive")
		}
		j.PayloadSHA = m.CompressedSHA256
		j.Phase = "prepared"
		j.LastError = ""
		j.NextAttempt = time.Time{}
		return atomicJSON(path, j, false)
	}
	m, e := syncapi.Inspect(payload)
	if e != nil {
		return w.attention(path, &j, "prepared file unavailable or invalid")
	}
	if m.CompressedSHA256 != j.PayloadSHA {
		return w.attention(path, &j, "prepared file checksum changed")
	}
	c, e := w.client()
	if e != nil {
		return w.deferJob(path, &j, e, 5*time.Minute)
	}
	var remote syncapi.Job
	if j.RemoteID == "" {
		remote, e = c.Create(ctx, m)
	} else {
		remote, e = c.Status(ctx, j.RemoteID)
	}
	if e != nil {
		var busy *syncapi.BusyError
		delay := 30 * time.Second
		if errors.As(e, &busy) {
			delay = busy.RetryAfter
		}
		if errors.Is(e, syncapi.ErrAuthentication) {
			delay = 5 * time.Minute
		}
		return w.deferJob(path, &j, e, delay)
	}
	j.RemoteID = remote.ID
	j.Phase = remote.Phase
	j.Result = remote.Result
	j.LastError = ""
	j.NextAttempt = time.Time{}
	// Persist the id before transferring data; unknown outcomes are resumed by status.
	if e = atomicJSON(path, j, false); e != nil {
		return e
	}
	switch remote.Phase {
	case "succeeded":
		return nil
	case "publication_unknown":
		return w.deferJob(path, &j, fmt.Errorf("publication result unknown; waiting for server resolution"), time.Minute)
	case "failed":
		j.Failures++
		if j.Failures >= 3 {
			return w.attention(path, &j, "server failed three times; inspect job "+j.RemoteID)
		}
		j.RemoteID = ""
		return w.deferJob(path, &j, fmt.Errorf("server job failed; will request a new job"), time.Minute)
	case "awaiting_upload":
		remote, e = c.Upload(ctx, j.RemoteID, payload, m)
		if e != nil {
			return w.deferJob(path, &j, fmt.Errorf("upload outcome unknown: %w", e), 30*time.Second)
		}
		j.Phase = remote.Phase
		j.Result = remote.Result
		j.NextAttempt = w.now().Add(3 * time.Second)
		return atomicJSON(path, j, false)
	case "uploading", "queued", "importing", "publishing":
		j.NextAttempt = w.now().Add(3 * time.Second)
		return atomicJSON(path, j, false)
	default:
		return w.attention(path, &j, "unexpected remote job state: "+remote.Phase)
	}
}

func (w *Worker) deferJob(path string, j *Job, e error, delay time.Duration) error {
	j.LastError = e.Error()
	j.NextAttempt = w.now().Add(delay)
	return atomicJSON(path, j, false)
}
func (w *Worker) attention(path string, j *Job, message string) error {
	j.Phase = "attention"
	j.LastError = message
	return atomicJSON(path, j, false)
}

func (w *Worker) Run(ctx context.Context) {
	var lastDiscovery time.Time
	for ctx.Err() == nil {
		if time.Since(lastDiscovery) >= time.Minute {
			if e := w.Discover(); e != nil {
				fmt.Fprintln(os.Stderr, "sync discovery:", e)
			}
			lastDiscovery = time.Now()
		}
		entries, e := os.ReadDir(w.Config.QueueDir)
		if e == nil {
			type candidate struct {
				path string
				at   int64
			}
			var candidates []candidate
			for _, entry := range entries {
				if ctx.Err() != nil {
					break
				}
				if !entry.IsDir() || !validSHA(entry.Name()) {
					continue
				}
				path := filepath.Join(w.Config.QueueDir, entry.Name(), "job.json")
				j, err := load(path)
				if os.IsNotExist(err) {
					continue
				}
				if err != nil {
					fmt.Fprintln(os.Stderr, "sync queue:", err)
					continue
				}
				if j.Phase != "succeeded" {
					candidates = append(candidates, candidate{path, j.CapturedAt})
				}
			}
			sort.Slice(candidates, func(i, j int) bool {
				if candidates[i].at == candidates[j].at {
					return candidates[i].path < candidates[j].path
				}
				return candidates[i].at < candidates[j].at
			})
			// Preserve snapshot chronology: a failed older upload holds newer ones.
			if len(candidates) > 0 {
				if e = w.Step(ctx, candidates[0].path); e != nil && !os.IsNotExist(e) {
					fmt.Fprintln(os.Stderr, "sync queue:", e)
				}
			}
		} else if !os.IsNotExist(e) {
			fmt.Fprintln(os.Stderr, "sync queue:", e)
		}
		t := time.NewTimer(5 * time.Second)
		select {
		case <-ctx.Done():
			t.Stop()
			return
		case <-t.C:
		}
	}
}

// Discover repairs a missed enqueue after a crash between archive and queue write.
func (w *Worker) Discover() error {
	if w.Config.ScanRoot == "" {
		return nil
	}
	_, e := os.Stat(w.Config.ScanRoot)
	if os.IsNotExist(e) {
		return nil
	}
	if e != nil {
		return e
	}
	return filepath.WalkDir(w.Config.ScanRoot, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".lua.manifest.json") {
			return nil
		}
		b, e := os.ReadFile(path)
		if e != nil {
			return e
		}
		var m struct {
			SnapshotSHA string `json:"snapshotSha256"`
			ArchiveSHA  string `json:"archiveSha256"`
			CapturedAt  int64  `json:"lastScanTime"`
		}
		if e = json.Unmarshal(b, &m); e != nil {
			return e
		}
		archive := strings.TrimSuffix(path, ".manifest.json") + ".tgz"
		source := filepath.Join(filepath.Dir(path), "snapshot.lua")
		if _, e = os.Stat(archive); e != nil {
			return e
		}
		return Enqueue(w.Config.QueueDir, source, archive, m.SnapshotSHA, m.ArchiveSHA, m.CapturedAt)
	})
}

// Recover an absent or changed Lua copy from the immutable, checked archive.
func recoverSource(dir, archive, expected string) (string, error) {
	output := filepath.Join(dir, "recovered.lua")
	if hash, e := hashFile(output); e == nil {
		if hash == expected {
			return output, nil
		}
		return "", fmt.Errorf("existing recovered source checksum mismatch")
	} else if !os.IsNotExist(e) {
		return "", e
	}
	f, e := os.Open(archive)
	if e != nil {
		return "", e
	}
	defer f.Close()
	z, e := gzip.NewReader(f)
	if e != nil {
		return "", e
	}
	defer z.Close()
	t := tar.NewReader(z)
	header, e := t.Next()
	if e != nil || !header.FileInfo().Mode().IsRegular() || filepath.Ext(header.Name) != ".lua" || header.Size < 0 || header.Size > 2<<30 {
		return "", fmt.Errorf("invalid archive member")
	}
	tmp, e := os.CreateTemp(dir, ".recovered-*.tmp")
	if e != nil {
		return "", e
	}
	name := tmp.Name()
	defer os.Remove(name)
	defer tmp.Close()
	h := sha256.New()
	n, e := io.Copy(io.MultiWriter(tmp, h), t)
	if e != nil {
		return "", e
	}
	if n != header.Size || hex.EncodeToString(h.Sum(nil)) != expected {
		return "", fmt.Errorf("archived snapshot checksum mismatch")
	}
	if _, e = t.Next(); e != io.EOF {
		return "", fmt.Errorf("unexpected archive member")
	}
	if _, e = io.Copy(io.Discard, z); e != nil {
		return "", e
	}
	if e = tmp.Sync(); e != nil {
		return "", e
	}
	if e = tmp.Close(); e != nil {
		return "", e
	}
	if e = os.Link(name, output); e != nil {
		return "", e
	}
	return output, nil
}

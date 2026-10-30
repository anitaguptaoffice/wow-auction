// sync-once submits one previously validated JSONL gzip snapshot.
package main

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"wow-auction/automation/wow-runner/internal/syncapi"
)

func main() {
	source := flag.String("source", "", "validated snapshot.jsonl.gz")
	tokenFile := flag.String("token-file", "", "file holding the bearer token")
	server := flag.String("server", "https://wowplayer.fun", "HTTPS origin")
	timeout := flag.Duration("timeout", 30*time.Minute, "overall timeout")
	flag.Parse()
	if *source == "" || *tokenFile == "" || *timeout <= 0 {
		fmt.Fprintln(os.Stderr, "source, token-file and positive timeout are required")
		os.Exit(2)
	}
	if e := run(*source, *tokenFile, *server, *timeout); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}

func run(source, tokenPath, server string, timeout time.Duration) error {
	secret, e := os.ReadFile(tokenPath)
	if e != nil {
		return fmt.Errorf("cannot read sync token: %w", e)
	}
	c, e := syncapi.New(server, strings.TrimSpace(string(secret)), nil)
	if e != nil {
		return e
	}
	f, e := os.Open(source)
	if e != nil {
		return e
	}
	defer f.Close()
	info, e := f.Stat()
	if e != nil {
		return e
	}
	z, e := gzip.NewReader(f)
	if e != nil {
		return e
	}
	line, e := bufio.NewReader(io.LimitReader(z, 65537)).ReadBytes('\n')
	closeErr := z.Close()
	if e != nil || closeErr != nil || len(line) > 65536 {
		return fmt.Errorf("invalid snapshot header")
	}
	var h struct {
		Type          string `json:"type"`
		Complete      bool   `json:"complete"`
		SchemaVersion int    `json:"schema_version"`
		RegionID      int64  `json:"region_id"`
		RealmID       int64  `json:"realm_id"`
		CapturedAt    int64  `json:"captured_at"`
		RecordCount   int64  `json:"record_count"`
	}
	if e = json.Unmarshal(line, &h); e != nil || h.Type != "snapshot" || !h.Complete {
		return fmt.Errorf("invalid snapshot metadata")
	}
	if _, e = f.Seek(0, io.SeekStart); e != nil {
		return e
	}
	hash := sha256.New()
	if _, e = io.Copy(hash, f); e != nil {
		return e
	}
	m := syncapi.Manifest{SchemaVersion: h.SchemaVersion, CompressedSHA256: hex.EncodeToString(hash.Sum(nil)), SizeBytes: info.Size(), RegionID: h.RegionID, RealmID: h.RealmID, CapturedAt: h.CapturedAt, RecordCount: h.RecordCount}
	if e = m.Validate(); e != nil {
		return e
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	var j syncapi.Job
	for {
		j, e = c.Create(ctx, m)
		if !errors.Is(e, syncapi.ErrBusy) {
			break
		}
		var busy *syncapi.BusyError
		delay := 10 * time.Second
		if errors.As(e, &busy) {
			delay = busy.RetryAfter
		}
		fmt.Fprintf(os.Stderr, "服务器忙，%s 后重试申请\n", delay)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
	}
	if e != nil {
		return e
	}
	printJob(j)
	if j.Phase == "awaiting_upload" {
		j, e = c.Upload(ctx, j.ID, source, m)
		if e != nil {
			return fmt.Errorf("upload outcome unknown; retry the same source to recover its job: %w", e)
		}
		printJob(j)
	}
	last := j.Phase
	for {
		switch j.Phase {
		case "succeeded":
			return nil
		case "failed", "publication_unknown":
			return fmt.Errorf("job %s ended in %s; inspect its receipt before retrying", j.ID, j.Phase)
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("job %s still pending; query its status before retry: %w", j.ID, ctx.Err())
		case <-time.After(3 * time.Second):
		}
		j, e = c.Status(ctx, j.ID)
		if e != nil {
			return fmt.Errorf("job %s status unknown: %w", j.ID, e)
		}
		if j.Phase != last {
			printJob(j)
			last = j.Phase
		}
	}
}

func printJob(j syncapi.Job) { _ = json.NewEncoder(os.Stdout).Encode(j) }

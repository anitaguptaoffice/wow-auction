package syncqueue

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Explicit opt-in: replay a previously published payload, never a new snapshot.
func TestLiveExistingSnapshotReplay(t *testing.T) {
	if os.Getenv("WOW_SYNC_LIVE_REPLAY") != "1" {
		t.Skip("explicit live replay not requested")
	}
	source := os.Getenv("WOW_REPLAY_SOURCE")
	archive := os.Getenv("WOW_REPLAY_ARCHIVE")
	manifest := os.Getenv("WOW_REPLAY_MANIFEST")
	expectedPayload := os.Getenv("WOW_REPLAY_PAYLOAD")
	token := os.Getenv("WOW_REPLAY_TOKEN_FILE")
	b, e := os.ReadFile(manifest)
	if e != nil {
		t.Fatal(e)
	}
	var m struct {
		SnapshotSHA string `json:"snapshotSha256"`
		ArchiveSHA  string `json:"archiveSha256"`
		CapturedAt  int64  `json:"lastScanTime"`
	}
	if e = json.Unmarshal(b, &m); e != nil {
		t.Fatal(e)
	}
	root := filepath.Join(t.TempDir(), "queue")
	if e = Enqueue(root, source, archive, m.SnapshotSHA, m.ArchiveSHA, m.CapturedAt); e != nil {
		t.Fatal(e)
	}
	w := Worker{Config: Config{QueueDir: root, TokenFile: token, Server: "https://wowplayer.fun"}}
	path := filepath.Join(root, m.SnapshotSHA, "job.json")
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	if e = w.Step(ctx, path); e != nil {
		t.Fatal(e)
	}
	j, e := load(path)
	if e != nil || j.Phase != "prepared" {
		t.Fatalf("prepare: %+v %v", j, e)
	}
	expected, e := hashFile(expectedPayload)
	if e != nil {
		t.Fatal(e)
	}
	if j.PayloadSHA != expected {
		t.Fatal("replay payload differs from previously published bytes; refusing network request")
	}
	if e = w.Step(ctx, path); e != nil {
		t.Fatal(e)
	}
	j, e = load(path)
	if e != nil || j.Phase != "succeeded" || j.RemoteID == "" {
		t.Fatalf("replay did not return existing successful receipt: %+v %v", j, e)
	}
	var result struct {
		Changed bool `json:"changed"`
		Added   int  `json:"added_scans"`
	}
	if e = json.Unmarshal(j.Result, &result); e != nil || result.Changed || result.Added != 0 {
		t.Fatalf("unexpected replay result: %+v %v", result, e)
	}
	t.Logf("existing receipt %s; added scans: %d", j.RemoteID, result.Added)
}

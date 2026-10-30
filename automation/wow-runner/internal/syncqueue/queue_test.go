package syncqueue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"wow-auction/automation/wow-runner/internal/snapshot"

	"wow-auction/automation/wow-runner/internal/syncapi"
)

const testJobID = "0123456789abcdef0123456789abcdef"

func fixture() (string, int64) {
	timestamp := int64(1700000000)
	m := `["itemCount"]=1,["recordCount"]=1,["linkedItemCount"]=1,["missingCoreCount"]=0,["apiErrorCount"]=0,["incompleteInfoCount"]=0,`
	return fmt.Sprintf(`AuctionSearchDB={["lastScan"]={%s},["lastScanTime"]=%d,["auctions"]={["2023-11-15"]={["scans"]={{%s["timestamp"]=%d,["realmID"]=887,["regionID"]=5,["realmName"]="遗忘海岸",["regionName"]="CN",["normalizedRealmName"]="遗忘海岸",["marketScopeUnknownCount"]=0,["items"]={{["itemID"]=42,["quantity"]=2,["name"]="甲",["hasAllInfo"]=true,["minBid"]=0}},["itemMarketScopes"]={[42]="region"}}}}}}`, m, timestamp, m, timestamp), timestamp
}
func digest(data []byte) string { v := sha256.Sum256(data); return hex.EncodeToString(v[:]) }
func setup(t *testing.T) (string, string, string, string, string, int64) {
	t.Helper()
	d := t.TempDir()
	source := filepath.Join(d, "snapshot.lua")
	archive := filepath.Join(d, "auction.lua.tgz")
	token := filepath.Join(d, "token")
	root := filepath.Join(d, "queue")
	body, stamp := fixture()
	a := []byte("archive fixture")
	for p, b := range map[string][]byte{source: []byte(body), archive: a, token: []byte("test-token")} {
		if e := os.WriteFile(p, b, 0600); e != nil {
			t.Fatal(e)
		}
	}
	sha := digest([]byte(body))
	if e := Enqueue(root, source, archive, sha, digest(a), stamp); e != nil {
		t.Fatal(e)
	}
	return root, source, archive, token, sha, stamp
}

func TestDurableWorkerUploadsAndResumesByJobID(t *testing.T) {
	root, source, archive, token, sha, stamp := setup(t)
	archiveSHA, _ := hashFile(archive)
	if e := Enqueue(root, source, archive, sha, archiveSHA, stamp); e != nil {
		t.Fatal(e)
	}
	phase := "awaiting_upload"
	counts := map[string]int{}
	base := "/api/sync/jobs/" + testJobID
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Error("bad auth")
		}
		counts[r.Method]++
		if r.Method == http.MethodPut {
			b, _ := io.ReadAll(r.Body)
			if len(b) == 0 {
				t.Error("empty upload")
			}
			phase = "queued"
		}
		if r.Method == http.MethodGet {
			phase = "succeeded"
		}
		_ = json.NewEncoder(w).Encode(syncapi.Job{ID: testJobID, Phase: phase, StatusURL: base, UploadURL: base + "/snapshot", Result: json.RawMessage(`{"changed":true}`)})
	}))
	defer srv.Close()
	now := time.Unix(stamp, 0)
	w := Worker{Config: Config{QueueDir: root, TokenFile: token, Server: srv.URL}, Now: func() time.Time { return now }, Transport: func(secret string) (*syncapi.Client, error) {
		return syncapi.New(srv.URL, secret, srv.Client().Transport)
	}}
	path := filepath.Join(root, sha, "job.json")
	if e := w.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, e := load(path)
	if e != nil || j.Phase != "prepared" || j.PayloadSHA == "" {
		t.Fatalf("prepare: %+v %v", j, e)
	}
	// Crash after publishing the payload but before saving its checksum.
	preparedSHA := j.PayloadSHA
	j.PayloadSHA = ""
	j.Phase = "pending"
	if e = atomicJSON(path, j, false); e != nil {
		t.Fatal(e)
	}
	if e = w.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, _ = load(path)
	if j.PayloadSHA != preparedSHA || j.Phase != "prepared" || len(counts) != 0 {
		t.Fatal("did not recover prepared payload without network")
	}
	if e = w.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, _ = load(path)
	if j.Phase != "queued" || j.RemoteID != testJobID {
		t.Fatalf("upload: %+v", j)
	}
	// New worker instance recovers from persisted job ID; it must not POST/PUT again.
	now = now.Add(4 * time.Second)
	w2 := w
	if e = w2.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, _ = load(path)
	if j.Phase != "succeeded" || counts["POST"] != 1 || counts["PUT"] != 1 || counts["GET"] != 1 {
		t.Fatalf("resume: %+v %+v", j, counts)
	}
	if _, e = os.Stat(archive); e != nil {
		t.Fatal("original archive lost")
	}
}

func TestBusyAndCorruptionNeverLoseArchive(t *testing.T) {
	root, source, archive, token, sha, _ := setup(t)
	path := filepath.Join(root, sha, "job.json")
	now := time.Now()
	w := Worker{Config: Config{QueueDir: root, TokenFile: token}, Now: func() time.Time { return now }}
	if e := os.WriteFile(source, []byte("modified"), 0600); e != nil {
		t.Fatal(e)
	}
	if e := w.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, e := load(path)
	if e != nil || j.Phase != "pending" || j.LastError == "" || !j.NextAttempt.After(now) {
		t.Fatalf("corruption: %+v %v", j, e)
	}
	if _, e = os.Stat(archive); e != nil {
		t.Fatal("archive removed")
	}
}

func TestDiscoveryRepairsMissingQueueEntry(t *testing.T) {
	d := t.TempDir()
	dir := filepath.Join(d, "cycles", "cycle-one", "character-01", "attempt-1")
	if e := os.MkdirAll(dir, 0700); e != nil {
		t.Fatal(e)
	}
	body, stamp := fixture()
	source := filepath.Join(dir, "snapshot.lua")
	archive := filepath.Join(dir, "auction-a.lua.tgz")
	if e := os.WriteFile(source, []byte(body), 0600); e != nil {
		t.Fatal(e)
	}
	if e := os.WriteFile(archive, []byte("archive"), 0600); e != nil {
		t.Fatal(e)
	}
	meta := fmt.Sprintf(`{"snapshotSha256":"%s","archiveSha256":"%s","lastScanTime":%d}`, digest([]byte(body)), digest([]byte("archive")), stamp)
	if e := os.WriteFile(filepath.Join(dir, "auction-a.lua.manifest.json"), []byte(meta), 0600); e != nil {
		t.Fatal(e)
	}
	root := filepath.Join(d, "queue")
	w := Worker{Config: Config{QueueDir: root, ScanRoot: filepath.Join(d, "cycles")}}
	if e := w.Discover(); e != nil {
		t.Fatal(e)
	}
	if e := w.Discover(); e != nil {
		t.Fatal(e)
	}
	if j, e := load(filepath.Join(root, digest([]byte(body)), "job.json")); e != nil || j.CapturedAt != stamp {
		t.Fatalf("discovery: %+v %v", j, e)
	}
}

func TestBusyWaitAndUnknownPublicationNeverResubmits(t *testing.T) {
	root, _, _, token, sha, _ := setup(t)
	path := filepath.Join(root, sha, "job.json")
	mode := "busy"
	posts, gets := 0, 0
	base := "/api/sync/jobs/" + testJobID
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			posts++
			if mode == "busy" {
				w.Header().Set("Retry-After", "17")
				w.WriteHeader(409)
				return
			}
		}
		if r.Method == http.MethodGet {
			gets++
		}
		phase := "awaiting_upload"
		if mode == "unknown" {
			phase = "publication_unknown"
		}
		if mode == "success" {
			phase = "succeeded"
		}
		_ = json.NewEncoder(w).Encode(syncapi.Job{ID: testJobID, Phase: phase, StatusURL: base, UploadURL: base + "/snapshot"})
	}))
	defer srv.Close()
	now := time.Now()
	worker := Worker{Config: Config{QueueDir: root, TokenFile: token}, Now: func() time.Time { return now }, Transport: func(secret string) (*syncapi.Client, error) {
		return syncapi.New(srv.URL, secret, srv.Client().Transport)
	}}
	if e := worker.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	if e := worker.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, _ := load(path)
	if !j.NextAttempt.Equal(now.Add(17*time.Second)) || posts != 1 {
		t.Fatalf("busy timing: %+v", j)
	}
	if e := worker.Step(context.Background(), path); e != nil || posts != 1 {
		t.Fatal("retried before server deadline")
	}
	now = now.Add(18 * time.Second)
	j.RemoteID = testJobID
	j.NextAttempt = time.Time{}
	if e := atomicJSON(path, j, false); e != nil {
		t.Fatal(e)
	}
	mode = "unknown"
	if e := worker.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, _ = load(path)
	if j.Phase != "publication_unknown" || j.RemoteID != testJobID {
		t.Fatalf("unknown result lost: %+v", j)
	}
	now = now.Add(61 * time.Second)
	mode = "success"
	if e := worker.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, _ = load(path)
	if j.Phase != "succeeded" || posts != 1 || gets != 2 {
		t.Fatalf("unknown response resubmitted: %+v POST=%d GET=%d", j, posts, gets)
	}
}

func TestMissingLuaRecoveredFromValidatedArchive(t *testing.T) {
	d := t.TempDir()
	input := filepath.Join(d, "snapshot.lua")
	body, stamp := fixture()
	if e := os.WriteFile(input, []byte(body), 0600); e != nil {
		t.Fatal(e)
	}
	report, e := snapshot.ValidateFile(input, time.Time{})
	if e != nil {
		t.Fatal(e)
	}
	archive, e := snapshot.ArchiveValidatedSnapshot(input, d, report)
	if e != nil {
		t.Fatal(e)
	}
	root := filepath.Join(d, "queue")
	if e = Enqueue(root, input, archive.ArchivePath, archive.SnapshotSHA256, archive.ArchiveSHA256, stamp); e != nil {
		t.Fatal(e)
	}
	if e = os.Remove(input); e != nil {
		t.Fatal(e)
	}
	path := filepath.Join(root, archive.SnapshotSHA256, "job.json")
	worker := Worker{Config: Config{QueueDir: root}}
	if e = worker.Step(context.Background(), path); e != nil {
		t.Fatal(e)
	}
	j, e := load(path)
	if e != nil || j.Phase != "prepared" {
		t.Fatalf("recovery: %+v %v", j, e)
	}
	if hash, e := hashFile(filepath.Join(root, archive.SnapshotSHA256, "recovered.lua")); e != nil || hash != archive.SnapshotSHA256 {
		t.Fatalf("recovered source: %s %v", hash, e)
	}
}

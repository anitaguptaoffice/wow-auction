//go:build windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"wow-auction/automation/wow-runner/internal/snapshot"
)

func TestRecoveryUsesValidatedArchiveNotProgressCounter(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "character-01", "attempt-1")
	if e := os.MkdirAll(dir, 0700); e != nil {
		t.Fatal(e)
	}
	now := time.Now().Unix()
	metadata := `["itemCount"]=1,["recordCount"]=1,["linkedItemCount"]=1,["missingCoreCount"]=0,["apiErrorCount"]=0,["incompleteInfoCount"]=0,`
	body := fmt.Sprintf(`AuctionSearchDB={["lastScan"]={%s},["lastScanTime"]=%d,["auctions"]={["day"]={["scans"]={{%s["timestamp"]=%d,["runID"]="run1",["characterName"]="角色",["characterGUID"]="Player-1",["realmName"]="服务器",["marketScopeUnknownCount"]=0,["items"]={{["itemID"]=1}}}}}}}`, metadata, now, metadata, now)
	source := filepath.Join(dir, "snapshot.lua")
	if e := os.WriteFile(source, []byte(body), 0600); e != nil {
		t.Fatal(e)
	}
	report, e := snapshot.ValidateFile(source, time.Time{})
	if e != nil {
		t.Fatal(e)
	}
	archive, e := snapshot.ArchiveValidatedSnapshot(source, dir, report)
	if e != nil {
		t.Fatal(e)
	}
	ch := collectCharacter{Name: "角色", Realm: "服务器"}
	if ok, e := hasVerifiedArchive(root, 0, ch, time.Unix(now-1, 0)); e != nil || !ok {
		t.Fatalf("valid archive ignored: %v", e)
	}
	if ok, e := hasVerifiedArchive(root, 0, collectCharacter{Name: "别人", Realm: "服务器"}, time.Unix(now-1, 0)); e != nil || ok {
		t.Fatalf("wrong character accepted: %v", e)
	}
	if ok, e := hasVerifiedArchive(root, 0, ch, time.Unix(now+1, 0)); e != nil || ok {
		t.Fatalf("previous cycle accepted: %v", e)
	}
	if e = os.WriteFile(archive.ArchivePath, []byte("damaged"), 0600); e != nil {
		t.Fatal(e)
	}
	if ok, e := hasVerifiedArchive(root, 0, ch, time.Unix(now-1, 0)); e != nil || ok {
		t.Fatalf("damaged archive accepted: %v", e)
	}
}

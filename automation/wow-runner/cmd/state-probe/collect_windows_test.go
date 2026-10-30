//go:build windows

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"hash/adler32"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	lua "github.com/yuin/gopher-lua"
)

func TestStatusPublicationRaceIsRetriedButSessionChangeRejected(t *testing.T) {
	b := statusBinding{session: "live", seq: 3, changed: time.Now()}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	calls := 0
	f, e := b.next(ctx, func() (addonFrame, error) {
		calls++
		if calls == 1 {
			return addonFrame{}, fmt.Errorf("pointer changed during read")
		}
		return addonFrame{Session: "live", Seq: 4}, nil
	})
	if e != nil || f.Seq != 4 || calls != 2 {
		t.Fatalf("race recovery failed: %+v %v", f, e)
	}
	if _, e = b.next(ctx, func() (addonFrame, error) { return addonFrame{Session: "other", Seq: 5}, nil }); e == nil {
		t.Fatal("accepted a different session")
	}
	b.changed = time.Now().Add(-6 * time.Second)
	if _, e = b.next(ctx, func() (addonFrame, error) { return addonFrame{Session: "live", Seq: 4}, nil }); e == nil {
		t.Fatal("accepted stopped heartbeat")
	}
}

func TestColdAddonEnablePreservesOtherSettingsAndBacksUp(t *testing.T) {
	dir := t.TempDir()
	charDir := filepath.Join(dir, "realm", "character")
	if e := os.MkdirAll(charDir, 0700); e != nil {
		t.Fatal(e)
	}
	path := filepath.Join(charDir, "AddOns.txt")
	original := []byte("OtherAddon: disabled\r\nAuctionSearchExample: disabled\r\nAnother: enabled\r\n")
	if e := os.WriteFile(path, original, 0600); e != nil {
		t.Fatal(e)
	}
	var logs bytes.Buffer
	c := collector{ctx: context.Background(), config: collectConfig{Output: dir, Characters: []collectCharacter{{Source: filepath.Join(charDir, "SavedVariables", "AuctionSearchExample.lua")}}}, log: json.NewEncoder(&logs)}
	pid, e := c.prepareAddons(0)
	if e != nil || pid != 0 {
		t.Fatalf("setup: %v", e)
	}
	updated, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	if string(updated) != strings.Replace(string(original), "AuctionSearchExample: disabled", "AuctionSearchExample: enabled", 1) {
		t.Fatal("unrelated settings changed")
	}
	backup, e := os.ReadFile(filepath.Join(dir, "AddOns-before-01.txt"))
	if e != nil || !bytes.Equal(backup, original) {
		t.Fatal("original backup missing")
	}
	logs.Reset()
	if _, e = c.prepareAddons(0); e != nil || logs.Len() != 0 {
		t.Fatal("setup was not idempotent")
	}
}

func statusPacket(payload string) []byte {
	return []byte(fmt.Sprintf("%s|adler=%d\n", payload, adler32.Checksum([]byte(payload))))
}

func TestStatusRejectsCorruptionAndDuplicateFields(t *testing.T) {
	valid := statusPacket("WAST1|session|1|1700000000|123|character=缚龙%7C截江%25|world=1")
	f, e := parseStatus(valid)
	if e != nil || f.Values["character"] != "缚龙|截江%" {
		t.Fatalf("parse: %v %+v", e, f)
	}
	bad := append([]byte(nil), valid...)
	bad[10] = 'x'
	for _, b := range [][]byte{bad, valid[:len(valid)-1], statusPacket("WAST1|s|1|1|1|world=1|world=0"), statusPacket("WAST1|s|1|1|1|world=%ZZ"), statusPacket("WAST1|s|0|1|1|world=1"), statusPacket("WAST1|s|1|1|1|world=" + strings.Repeat("a", 2048))} {
		if _, e := parseStatus(b); e == nil {
			t.Fatal("accepted corrupt/ambiguous frame")
		}
	}
}

func TestLogoutRequiresSameCompleteRun(t *testing.T) {
	base := addonFrame{Session: "current", Values: map[string]string{"phase": "logout_ready", "validated": "1", "run": "run1", "guid": "player1", "missingCore": "0", "incomplete": "0", "apiErrors": "0", "unknownScope": "0", "processed": "123", "total": "123"}}
	if !sameCompletedRun(base, base) {
		t.Fatal("valid snapshot rejected")
	}
	for key, value := range map[string]string{"validated": "0", "phase": "warning", "run": "run2", "guid": "player2", "unknownScope": "1", "total": "124", "processed": "0"} {
		candidate := addonFrame{Session: base.Session, Values: map[string]string{}}
		for k, v := range base.Values {
			candidate.Values[k] = v
		}
		candidate.Values[key] = value
		if sameCompletedRun(base, candidate) {
			t.Fatalf("accepted changed %s", key)
		}
	}
	other := base
	other.Session = "old"
	if sameCompletedRun(base, other) {
		t.Fatal("accepted stale session")
	}
}

func TestAddonPreparationAndScanGates(t *testing.T) {
	l := lua.NewState()
	defer l.Close()
	must := func(code string) {
		t.Helper()
		if e := l.DoString(code); e != nil {
			t.Fatal(e)
		}
	}
	must(`
time=function() return 1700000000 end
GetTime=function() return 42 end
GetRealmID=function() return 838 end
GetRealmName=function() return "熊猫酒仙" end
UnitGUID=function() return "Player-838-123" end
UnitName=function(unit) if unit=="player" then return "缚龙截江" else return targetName end end
UnitIsPlayer=function() return false end
InCombatLockdown=function() return combat end
IsMounted=function() return mounted end
UnitCastingInfo=function() return castName end
UnitChannelInfo=function() return nil end
GetCurrentKeyBoardFocus=function() return focus end
ChatEdit_GetActiveWindow=function() return edit end
C_MountJournal={GetMountIDs=function() return {1} end, GetMountInfoByID=function() return "雄壮商队雷龙", nil, nil, mounted end}
pending={}; tick=nil
C_Timer={After=function(_, fn) table.insert(pending,fn) end, NewTicker=function(_,fn) tick=fn;return {Cancel=function() tick=nil end} end}
AuctionSearchOverlay={SetPhase=function(phase) AuctionSearchStatus.Update({phase=phase}) end, SetProgress=function(n,total) AuctionSearchStatus.Update({processed=n,total=total}) end}
`)
	for _, file := range []string{"Preparation.lua", "StatusBridge.lua", "Automation.lua"} {
		if e := l.DoFile(filepath.Join("../../../../game/addons/AuctionSearchExample", file)); e != nil {
			t.Fatal(e)
		}
	}
	must(`
local p=AuctionSearchPreparation.Sample()
assert(p.prepReady==1 and p.mounted==0 and p.chatOpen==0)
combat=true;assert(AuctionSearchPreparation.Sample().prepReady==0);combat=false
mounted=true;targetName="收集者温塔"
assert(AuctionSearchPreparation.Sample().brutosaur==1 and AuctionSearchPreparation.Sample().targetAuctioneer==1)
edit={IsShown=function() return true end,GetText=function() return "/targetexact 收集者温塔" end};focus=edit
assert(AuctionSearchPreparation.Sample().chatCommand=="target")
edit.GetText=function() return "private user chat" end
assert(AuctionSearchPreparation.Sample().chatCommand=="other")
edit.GetText=function() error("unavailable") end
assert(AuctionSearchPreparation.Sample().prepReady==0)
edit=nil;focus=nil
AuctionSearchStatus.Start(); settings={};AuctionSearchAutomation.Init(settings)
requests=0
local request=function() requests=requests+1 end
AuctionSearchAutomation.OnHouseOpen(request);AuctionSearchAutomation.OnHouseClosed()
for _,fn in ipairs(pending) do fn() end;pending={};assert(requests==0)
AuctionSearchAutomation.OnHouseOpen(request);AuctionSearchAutomation.OnHouseOpen(request)
for _,fn in ipairs(pending) do fn() end;pending={};assert(requests==1)
AuctionSearchAutomation.OnHouseOpen(request);assert(#pending==0)
local run=AuctionSearchAutomation.RequestStarted(1)
scan={runID=run,itemCount=1,recordCount=1,items={{}},linkedItemCount=1,missingCoreCount=0,incompleteInfoCount=0,apiErrorCount=0,marketScopeUnknownCount=0}
scan.runID="stale";assert(not AuctionSearchAutomation.OnCommitted(scan));scan.runID=run
scan.marketScopeUnknownCount=1;assert(not AuctionSearchAutomation.OnCommitted(scan));scan.marketScopeUnknownCount=0
assert(AuctionSearchAutomation.OnCommitted(scan))
AuctionSearchAutomation.SetEnabled(false,settings)
assert(AuctionSearchStatusBridge[2]:find("validated=0",1,true))
AuctionSearchStatus.LeaveWorld();assert(AuctionSearchStatusBridge[2]:find("world=0",1,true))
AuctionSearchStatus.Start();tick()
assert(AuctionSearchStatusBridge[2]:find("world=1",1,true))
`)
	bridge := l.GetGlobal("AuctionSearchStatusBridge").(*lua.LTable)
	f, e := parseStatus([]byte(bridge.RawGetInt(2).String()))
	if e != nil {
		t.Fatal(e)
	}
	if f.Values["character"] != "缚龙截江" || f.Values["realm"] != "熊猫酒仙" || f.Values["validated"] != "0" {
		t.Fatalf("unexpected status: %+v", f)
	}
}

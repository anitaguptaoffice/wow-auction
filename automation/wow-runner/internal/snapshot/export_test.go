package snapshot

import (
	"bufio"
	"compress/gzip"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func exportFixture(scope string) string {
	meta := `["itemCount"]=2,["recordCount"]=2,["linkedItemCount"]=2,["missingCoreCount"]=0,["apiErrorCount"]=0,["incompleteInfoCount"]=0,`
	return fmt.Sprintf(`AuctionSearchDB={["lastScan"]={%s},["lastScanTime"]=1700000000,["auctions"]={["2023-11-15"]={["scans"]={{%s["timestamp"]=1700000000,["realmID"]=887,["regionID"]=5,["realmName"]="遗忘海岸",["regionName"]="CN",["normalizedRealmName"]="遗忘海岸",["marketScopeUnknownCount"]=0,["items"]={{["itemID"]=42,["quantity"]=2,["name"]="甲",["hasAllInfo"]=true,["minBid"]=0},{["itemID"]=43,["quantity"]=1,["name"]="乙",["usable"]=false}},["itemMarketScopes"]={[42]="region",[43]="%s"}}}}}}`, meta, meta, scope)
}

func TestExportStreamingScopesAfterItems(t *testing.T) {
	d := t.TempDir()
	source := filepath.Join(d, "snapshot.lua")
	out := filepath.Join(d, "snapshot.jsonl.gz")
	if e := os.WriteFile(source, []byte(exportFixture("realm")), 0600); e != nil {
		t.Fatal(e)
	}
	if e := ExportJSONL(source, out); e != nil {
		t.Fatal(e)
	}
	f, e := os.Open(out)
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	z, e := gzip.NewReader(f)
	if e != nil {
		t.Fatal(e)
	}
	defer z.Close()
	s := bufio.NewScanner(z)
	var rows []map[string]any
	for s.Scan() {
		var row map[string]any
		if e = json.Unmarshal(s.Bytes(), &row); e != nil {
			t.Fatal(e)
		}
		rows = append(rows, row)
	}
	if e = s.Err(); e != nil {
		t.Fatal(e)
	}
	if len(rows) != 3 || rows[0]["record_count"] != float64(2) || rows[0]["realm_id"] != float64(887) || rows[1]["marketScope"] != "region" || rows[2]["marketScope"] != "realm" || rows[2]["usable"] != false {
		t.Fatalf("bad export: %+v", rows)
	}
	if e = ExportJSONL(source, out); e == nil {
		t.Fatal("overwrote existing file")
	}
	second := filepath.Join(d, "second.jsonl.gz")
	if e = ExportJSONL(source, second); e != nil {
		t.Fatal(e)
	}
	a, e := os.ReadFile(out)
	if e != nil {
		t.Fatal(e)
	}
	b, e := os.ReadFile(second)
	if e != nil {
		t.Fatal(e)
	}
	if sha256.Sum256(a) != sha256.Sum256(b) {
		t.Fatal("same snapshot produced different compressed bytes")
	}
}

func TestExportRejectsUnknownOrMissingScopeWithoutPublishing(t *testing.T) {
	for name, body := range map[string]string{"unknown": exportFixture("unknown"), "missing": strings.Replace(exportFixture("realm"), `[43]="realm"`, "", 1)} {
		t.Run(name, func(t *testing.T) {
			d := t.TempDir()
			in := filepath.Join(d, "snapshot.lua")
			out := filepath.Join(d, "export.gz")
			if e := os.WriteFile(in, []byte(body), 0600); e != nil {
				t.Fatal(e)
			}
			if e := ExportJSONL(in, out); e == nil {
				t.Fatal("incomplete scope accepted")
			}
			if _, e := os.Stat(out); !os.IsNotExist(e) {
				t.Fatal("incomplete file published")
			}
		})
	}
}

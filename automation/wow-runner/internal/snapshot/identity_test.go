package snapshot

import (
	"strings"
	"testing"
)

func TestScanIdentityFromExactSnapshot(t *testing.T) {
	body := strings.Replace(validSnapshotLua(200, 200, 2), `["timestamp"] = 200,`, `["timestamp"] = 200, ["runID"]="run1", ["characterName"]="缚龙截江", ["characterGUID"]="Player-838-123", ["realmName"]="熊猫酒仙", ["marketScopeUnknownCount"]=0,`, 1)
	r, e := Parse(strings.NewReader(body))
	if e != nil {
		t.Fatal(e)
	}
	if r.Latest.RunID != "run1" || r.Latest.CharacterName != "缚龙截江" || r.Latest.RealmName != "熊猫酒仙" || r.Latest.MarketScopeUnknownCount == nil || *r.Latest.MarketScopeUnknownCount != 0 {
		t.Fatalf("identity lost: %+v", r.Latest)
	}
	for _, bad := range []string{strings.Replace(body, `["runID"]="run1",`, `["runID"]="run1",["runID"]="other",`, 1), strings.Replace(body, `["marketScopeUnknownCount"]=0`, `["marketScopeUnknownCount"]=-1`, 1)} {
		if _, e := Parse(strings.NewReader(bad)); e == nil {
			t.Fatal("accepted ambiguous scan identity/scope")
		}
	}
}

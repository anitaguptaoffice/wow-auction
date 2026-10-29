"""Full real-snapshot field comparison. Offline timing is not a game benchmark."""
import argparse
import json
from pathlib import Path
import time
from test_memory import Sender
from transport import Receiver

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
receiver = Receiver(args.output)
result = None

def accept(packet):
    global result
    result = receiver.accept(packet) or result

sender = Sender(sink=accept)
started = time.monotonic()
sender.start(args.source.read_bytes())
sender.drain()
assert result["transportComplete"]
received = sender.load(Path(result["file"]).read_bytes())
compare = sender.lua.eval(b"""
function(a,b)
    local source,target,count
    count=0
    for _,d in pairs(a.auctions) do for _,s in pairs(d.scans) do
        if not source or s.timestamp>source.timestamp then source=s end
    end end
    for _,d in pairs(b.auctions) do for _,s in pairs(d.scans) do target=s end end
    local function equal(a,b)
        assert(type(a)==type(b), "type mismatch")
        if type(a)~="table" then assert(a==b, "field mismatch"); count=count+1; return end
        for k,v in pairs(a) do equal(v,b[k]) end
        for k in pairs(b) do assert(a[k]~=nil, "unexpected field") end
    end
    equal(source,target)
    return count,#source.items,source.realmName
end
""")
fields, records, realm = compare(sender.database, received)
report = dict(allFieldsEqual=True, fieldsCompared=fields, records=records, realm=realm.decode(),
              offlineSeconds=time.monotonic()-started, source=str(args.source), transport=result)
(args.output / "benchmark.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(report, indent=2, ensure_ascii=False))

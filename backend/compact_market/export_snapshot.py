"""Convert one complete Lua/tgz observation to the public snapshot.jsonl.gz format."""
import argparse
import gzip
import json
from pathlib import Path
import tempfile

from app.services.auction_importer import decode_snapshot, validate_snapshot
from compact_market.build import extract_source


def export(source,output):
    if output.exists(): raise ValueError('Output already exists')
    with tempfile.TemporaryDirectory() as temporary:
        lua,_=extract_source(source,Path(temporary))
        scans=validate_snapshot(decode_snapshot(lua))
        if len(scans)!=1: raise ValueError('Export exactly one complete snapshot per file')
        scan=scans[0]
        meta=dict(type='snapshot',schema_version=1,complete=True,region_id=scan.region_id,
                  region_name=scan.region_name,realm_id=scan.realm_id,realm_name=scan.realm_name,
                  normalized_realm_name=scan.normalized_realm_name,captured_at=scan.timestamp,
                  source_date=scan.source_date,record_count=scan.record_count)
        scopes={r['item_id']:r['market_scope'] for r in scan.item_scopes}
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('xb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as compressed:
            compressed.write((json.dumps(meta,ensure_ascii=False,separators=(',',':'))+'\n').encode())
            for item in scan.items:
                row=dict(item,type='auction',marketScope=scopes[item['itemID']])
                if row['marketScope']=='unknown': raise ValueError('Unknown market scope; cannot export for sync')
                compressed.write((json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode())
    return meta


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();print(json.dumps(export(args.source,args.output),ensure_ascii=False))

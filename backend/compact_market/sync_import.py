"""Isolated server-side import: no network, no Docker socket, immutable input/base."""
import argparse
import json
from pathlib import Path

from app.services.auction_importer import sha256_file
from compact_market.jsonl import header
from compact_market.manage import stage_generation, write_json
from compact_market.update import prepare_icons, accept


def run(source, base, output, manifest):
    if sha256_file(source)!=manifest['compressed_sha256'] or source.stat().st_size!=manifest['size_bytes']:
        raise ValueError('Snapshot upload changed after receipt')
    meta=header(source)
    for key in ('schema_version','region_id','realm_id','captured_at','record_count'):
        if meta[key]!=manifest[key]:
            raise ValueError('Snapshot header disagrees with upload manifest: '+key)
    report=stage_generation(base,output,sources=[source],newer_only=True,memory_limit='512MB')
    prepare_icons(output,output.parent/'icon-map.json',
                  source_map=Path(__file__).parents[1]/'app'/'icon-map.json',
                  cache_dirs=[base.parent/'wow-icons',Path('/app/icon-assets')])
    report['icon_manifest_sha256']=sha256_file(output.parent/'icon-map.json')
    write_json(output.with_suffix('.report.json'),report)
    accept(output,report)
    return dict(added_scans=len(report['added_scans']),skipped=report['skipped'],
                database_sha256=report['database_sha256'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','base','output','manifest'): parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    result=run(args.source,args.base,args.output,json.loads(args.manifest.read_text())['manifest'])
    print(json.dumps(result))

#!/usr/bin/env python3
"""Upload ONE snapshot; the server owns all history, imports and publication."""
import argparse
import gzip
import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request


def sync(source, base, token, timeout=1800):
    base=base.rstrip('/')
    parsed=urllib.parse.urlsplit(base)
    if parsed.scheme!='https' or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('The sync server must be an HTTPS origin')
    with gzip.open(source,'rt',encoding='utf-8') as stream: meta=json.loads(stream.readline(65537))
    digest=hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): digest.update(block)
    manifest={key:meta[key] for key in ('schema_version','region_id','realm_id','captured_at','record_count')}
    manifest.update(compressed_sha256=digest.hexdigest(),size_bytes=source.stat().st_size)
    auth={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):
            raise ValueError('Refusing to forward a sync token through a redirect')
    opener=urllib.request.build_opener(NoRedirect)
    def request(path,body=None):
        req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=auth)
        with opener.open(req,timeout=30) as response: return json.load(response)
    job=request('/api/sync/jobs',manifest)
    print(json.dumps(job,ensure_ascii=False),flush=True)
    if job['phase']=='awaiting_upload':
        # http.client streams the file rather than reading a large body into RAM.
        connection=http.client.HTTPSConnection(parsed.hostname,parsed.port or 443,timeout=600)
        try:
            connection.putrequest('PUT',job['upload_url'])
            connection.putheader('Authorization','Bearer '+token)
            connection.putheader('Content-Type','application/gzip')
            connection.putheader('Content-Length',str(source.stat().st_size));connection.endheaders()
            with source.open('rb') as stream:
                for block in iter(lambda:stream.read(256*1024),b''): connection.send(block)
            response=connection.getresponse();body=response.read()
            if response.status not in (200,202): raise ValueError('Upload rejected: '+str(response.status)+' '+body.decode())
        finally: connection.close()
    deadline=time.monotonic()+timeout
    previous=None
    while time.monotonic()<deadline:
        job=request(job['status_url'])
        if job['phase']!=previous: print(json.dumps(job,ensure_ascii=False),flush=True);previous=job['phase']
        if job['phase']=='succeeded': return job
        if job['phase'] in ('failed','publication_unknown'): raise ValueError('Sync did not complete: '+json.dumps(job,ensure_ascii=False))
        time.sleep(3)
    raise TimeoutError('Sync is still running; query '+job['status_url']+' before uploading again')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path);parser.add_argument('--server',default='https://wowplayer.fun')
    parser.add_argument('--token-file',type=Path);parser.add_argument('--timeout',type=int,default=1800)
    args=parser.parse_args()
    token=args.token_file.read_text().strip() if args.token_file else os.getenv('WOW_SYNC_TOKEN','')
    if not token: parser.error('Set WOW_SYNC_TOKEN or pass --token-file; do not put credentials in a URL')
    try: sync(args.source,args.server,token,args.timeout)
    except Exception as exc: print(str(exc),file=sys.stderr);sys.exit(1)

"""Local-only ingress behind HTTPS Nginx. No Docker socket or database access."""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sync_state import Busy, Store, public


def create_app(root=None, token=None, switch_lock=None):
    root=root or os.environ['SYNC_ROOT']
    if token is None:
        token=Path(os.environ['SYNC_TOKEN_FILE']).read_text().strip()
    if len(token)<32: raise ValueError('Sync token must contain at least 32 characters')
    store=Store(root,switch_lock or os.getenv('SYNC_SWITCH_LOCK'))
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store

    async def authorize(request):
        provided=request.headers.get('Authorization','')
        if not hmac.compare_digest(provided.encode(),('Bearer '+token).encode()):
            raise HTTPException(401,'Invalid sync token',headers={'WWW-Authenticate':'Bearer'})

    @app.exception_handler(Busy)
    async def busy(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=409,headers={'Retry-After':'10'})

    @app.exception_handler(FileNotFoundError)
    async def missing(request,exc):
        return JSONResponse({'detail':'Unknown sync job'},status_code=404)

    @app.post('/api/sync/jobs')
    async def create(request:Request):
        await authorize(request)
        body=b''
        async for chunk in request.stream():
            body+=chunk
            if len(body)>4096: raise HTTPException(413,'Manifest too large')
        try:
            manifest=json.loads(body)
            keys={'schema_version','compressed_sha256','size_bytes','region_id','realm_id','captured_at','record_count'}
            if not isinstance(manifest,dict) or set(manifest)!=keys: raise ValueError('Invalid manifest fields')
            from compact_market.jsonl import integer
            if type(manifest['schema_version']) is not int or manifest['schema_version']!=1: raise ValueError('Unsupported schema version')
            sha=manifest['compressed_sha256']
            if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha): raise ValueError('Invalid SHA-256')
            integer(manifest['size_bytes'],'size_bytes',1,128*1024*1024)
            integer(manifest['record_count'],'record_count',0,1_000_000)
            integer(manifest['region_id'],'region_id',1,5)
            integer(manifest['realm_id'],'realm_id',1,2**31-1)
            integer(manifest['captured_at'],'captured_at',1,int(time.time())+600)
            job,created=await asyncio.to_thread(store.create,manifest)
        except (ValueError,TypeError,KeyError) as exc:
            raise HTTPException(422,str(exc)) from exc
        return JSONResponse(public(job),status_code=202 if created else 200)

    @app.get('/api/sync/jobs/{job_id}')
    async def status(job_id:str,request:Request):
        await authorize(request)
        return public(await asyncio.to_thread(store.job,job_id))

    @app.put('/api/sync/jobs/{job_id}/snapshot')
    async def upload(job_id:str,request:Request):
        await authorize(request)
        job=await asyncio.to_thread(store.job,job_id)
        if job['phase'] in ('queued','importing','publishing','succeeded','publication_unknown'):
            return JSONResponse(public(job),status_code=200)
        size=job['manifest']['size_bytes']
        if request.headers.get('content-encoding'):
            raise HTTPException(415,'Send gzip as application/gzip, without HTTP Content-Encoding')
        if request.headers.get('content-type','').split(';')[0]!='application/gzip':
            raise HTTPException(415,'Expected application/gzip')
        if request.headers.get('content-length')!=str(size):
            raise HTTPException(422,'Content-Length must match the declared compressed size')
        await asyncio.to_thread(store.transition,job_id,('awaiting_upload',),'uploading')
        folder=Path(root)/'jobs'/job_id
        temporary=folder/'upload.part'
        try:
            digest=hashlib.sha256();received=0
            async with asyncio.timeout(600):
                with temporary.open('xb') as output:
                    async for chunk in request.stream():
                        received+=len(chunk)
                        if received>size: raise ValueError('Upload exceeds declared size')
                        digest.update(chunk);output.write(chunk)
                    output.flush();os.fsync(output.fileno())
            if received!=size or digest.hexdigest()!=job['manifest']['compressed_sha256']:
                raise ValueError('Uploaded size or SHA-256 does not match manifest')
            target=folder/'snapshot.jsonl.gz'
            os.chmod(temporary,0o400);os.replace(temporary,target)
            job=await asyncio.to_thread(store.transition,job_id,('uploading',),'queued')
        except Exception as exc:
            if temporary.exists(): temporary.unlink()
            try: await asyncio.to_thread(store.transition,job_id,('uploading',),'failed',error='Upload failed or checksum mismatch')
            except Busy: pass
            raise HTTPException(422,'Upload failed or checksum mismatch') from exc
        return JSONResponse(public(job),status_code=202)

    return app


def app_factory():
    return create_app()

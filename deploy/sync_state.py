"""Durable single-job leases shared by the unprivileged HTTP ingress and host worker.

Keep this module compatible with the production host's Python 3.6 standard library.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

TERMINAL = ('succeeded', 'failed')


class Busy(Exception):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x', encoding='utf-8') as target:
        os.chmod(str(temporary), 0o600)
        if os.name!='nt' and os.geteuid()==0:
            parent=path.parent.stat();os.chown(str(temporary),parent.st_uid,parent.st_gid)
        json.dump(value, target, ensure_ascii=True, indent=2)
        target.flush(); os.fsync(target.fileno())
    os.replace(str(temporary), str(path))
    if os.name != 'nt':
        fd=os.open(str(path.parent), os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


@contextlib.contextmanager
def guard(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'guard.lock').open('a+b') as handle:
        if os.name!='nt' and os.geteuid()==0:
            parent=root.stat();os.chown(str(root/'guard.lock'),parent.st_uid,parent.st_gid)
        if os.name=='nt':
            import msvcrt
            handle.seek(0);handle.write(b'0');handle.flush();handle.seek(0)
            try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: raise Busy('Sync state is busy')
            try: yield
            finally: handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: raise Busy('Sync state is busy')
            try: yield
            finally: fcntl.flock(handle,fcntl.LOCK_UN)


class Store:
    def __init__(self, root, switch_lock=None, upload_ttl=900):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'jobs').mkdir(exist_ok=True)
        (self.root/'receipts').mkdir(exist_ok=True)
        self.switch_lock=Path(switch_lock) if switch_lock else None
        self.upload_ttl=upload_ttl

    def job(self, job_id):
        if len(job_id)!=32 or any(c not in '0123456789abcdef' for c in job_id):
            raise FileNotFoundError('Unknown sync job')
        return read(self.root/'jobs'/job_id/'job.json')

    def save(self, job):
        job['updated_at']=int(time.time())
        write(self.root/'jobs'/job['id']/'job.json',job)

    def active(self):
        path=self.root/'active.json'
        if not path.exists(): return None
        job=self.job(read(path)['id'])
        if job['phase'] in ('awaiting_upload','uploading') and job['upload_deadline']<time.time():
            job.update(phase='failed',error='Upload lease expired');self.save(job);path.unlink();return None
        if job['phase'] in TERMINAL:
            path.unlink();return None
        return job

    def create(self, manifest):
        with guard(self.root):
            active=self.active()
            receipt=self.root/'receipts'/(manifest['compressed_sha256']+'.json')
            previous=self.job(read(receipt)['id']) if receipt.exists() else None
            if previous and previous['manifest']!=manifest:
                raise ValueError('This checksum is already bound to another manifest')
            if previous and previous['phase']!='failed':
                if previous['phase'] not in TERMINAL and not active:
                    write(self.root/'active.json',{'id':previous['id']})
                return previous,False
            if active or (self.switch_lock and self.switch_lock.exists()):
                raise Busy('Another sync or dataset publication is active')
            job=dict(id=uuid.uuid4().hex,phase='awaiting_upload',manifest=manifest,
                     created_at=int(time.time()),upload_deadline=int(time.time())+self.upload_ttl)
            (self.root/'jobs'/job['id']).mkdir(mode=0o700)
            self.save(job)
            write(receipt,{'id':job['id']})
            write(self.root/'active.json',{'id':job['id']})
            return job,True

    def transition(self, job_id, expected, phase, **values):
        with guard(self.root):
            active=self.active()
            job=self.job(job_id)
            if not active or active['id']!=job_id or job['phase'] not in expected:
                raise Busy('Job is not in the expected phase')
            job.update(phase=phase,**values);self.save(job)
            if phase in TERMINAL:
                (self.root/'active.json').unlink()
            return job


def public(job):
    result={key:job[key] for key in ('id','phase','created_at','updated_at')}
    result['status_url']='/api/sync/jobs/'+job['id']
    result['upload_url']=result['status_url']+'/snapshot'
    for key in ('error','result'):
        if key in job: result[key]=job[key]
    return result

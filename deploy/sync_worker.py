"""Privileged host worker. Only this service can invoke Docker and switch Nginx.

Python 3.6 compatible. The HTTP container has no access to this config or Docker.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import time
from types import SimpleNamespace

import dataset
from sync_state import Store, guard, read, write


def live(config):
    import re
    ports=re.findall(rb'proxy_pass http://127\.0\.0\.1:(818[01]);',Path(config['nginx_config']).read_bytes())
    if len(ports)!=1: raise ValueError('Cannot identify the live API upstream')
    for name in dataset.command('docker','ps','--format','{{.Names}}').splitlines():
        info=json.loads(dataset.command('docker','inspect',name))[0]
        bindings=info['NetworkSettings']['Ports'].get('8000/tcp') or []
        if any(b['HostIp']=='127.0.0.1' and b['HostPort']==ports[0].decode() for b in bindings):
            return Path(next(m['Source'] for m in info['Mounts'] if m['Destination']=='/data/market.duckdb'))
    raise ValueError('Cannot identify the live API database')


def status(config,phase):
    directory=Path(config['runtime_dir']);directory.mkdir(parents=True,exist_ok=True)
    dataset.atomic_write(directory/'status.json',json.dumps(dict(phase=phase,updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()))).encode(),mode=0o644)


def retention_gc(config, job):
    from retention_gc import collect
    try:
        return collect(config, job)
    except Exception as exc:
        # A verified publication remains successful; expose reclamation failures separately.
        print('Retention artifact cleanup failed: '+str(exc), flush=True)
        return dict(error=str(exc))


def process(config,store,job):
    folder=store.root/'jobs'/job['id'];output=folder/'generation'/'market.duckdb'
    retention = job.get('kind') == 'retention'
    if job['phase']=='queued':
        base=live(config)
        if shutil.disk_usage(str(store.root)).free < base.stat().st_size*2+512*1024*1024:
            raise ValueError('Not enough free disk space for a recoverable candidate')
        extra = {}
        policy_file = store.root/'retention-policy.json'
        if not retention and policy_file.exists():
            from retention_policy import require_fresh
            extra['retention'] = read(policy_file)
            require_fresh(job['manifest']['captured_at'], extra['retention'])
        job=store.transition(job['id'],('queued',),'importing',base_database=str(base),base_sha256=dataset.sha256(base),**extra)
        status(config,'importing')
    if job['phase']=='importing':
        base=Path(job['base_database']);name='wow-auction-import-'+job['id']
        exists=subprocess.call(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)==0
        if not exists:
            if (folder/'generation').exists():
                # A crash before container creation leaves an ambiguous candidate;
                # fail closed rather than overwrite any existing generation.
                raise ValueError('Import candidate exists without its execution record')
            command=['docker','run','-d','--name',name,'--memory','2g','--memory-swap','2g','--cpus','2',
                     '--pids-limit','128','--network','none','--read-only','--cap-drop','ALL',
                     '--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,size=256m',
                     '-v',str(base.parent)+':/base:ro','-v',str(folder)+':/work:rw',
                     config['import_image'],'python','-m',
                     'compact_market.retention' if retention else 'compact_market.sync_import',
                     '--base','/base/'+base.name,
                     '--output','/work/generation/market.duckdb','--manifest','/work/job.json']
            if not retention:
                command += ['--source','/work/snapshot.jsonl.gz']
            dataset.command(*command)
        else:
            info=json.loads(dataset.command('docker','inspect',name))[0]
            if info['State']['Status']=='created': dataset.command('docker','start',name)
        exit_code=dataset.command('docker','wait',name)
        log=dataset.command('docker','logs',name)
        (folder/'import.log').write_text(log,encoding='utf-8')
        if exit_code!='0':
            raise ValueError('Snapshot validation/import failed; inspect the private worker log')
        report=read(output.with_suffix('.report.json'))
        changed = report.get('changed', bool(report['added_scans'])) if retention else bool(report['added_scans'])
        if (changed or not retention) and dataset.sha256(output)!=report['database_sha256']:
            raise ValueError('Import output checksum mismatch')
        if not changed:
            result = dict(changed=False,added_scans=0,skipped=report.get('skipped',[]),database_sha256=job['base_sha256'])
            if retention:
                result['retention'] = report['retention']
                result['artifact_cleanup'] = retention_gc(config, job)
            job=store.transition(job['id'],('importing',),'succeeded',result=result)
            status(config,'published')
            subprocess.call(['docker','rm',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            return
        report['database']=str(output)
        write(output.with_suffix('.report.json'),report)
        job=store.transition(job['id'],('importing',),'publishing')
    if job['phase'] in ('publishing','publication_unknown'):
        args=SimpleNamespace(database=str(output),report=str(output.with_suffix('.report.json')),
            config=config['nginx_config'],backup_dir=config['backup_dir'],
            expected_current_sha=job['base_sha256'],public_url=config['public_url'],runtime_dir=config['runtime_dir'])
        # Same guard as HTTP reservations and current CLI publishers. The long
        # switch uses the established dataset lock; no HTTP process needs root.
        lock=Path(config['switch_lock'])
        with guard(store.root):
            if lock.exists():
                pid=int(lock.read_text().strip())
                if pid<=1: raise ValueError('Invalid dataset lock owner; operator inspection required')
                try: os.kill(pid,0)
                except ProcessLookupError: lock.unlink()
            fd=os.open(str(lock),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            os.write(fd,str(os.getpid()).encode())
        try:
            result=dataset.activate(args)
            report=read(output.with_suffix('.report.json'))
            details = dict(changed=True,added_scans=len(report['added_scans']),publication=result)
            if retention:
                details.update(retention=report['retention'],removed_scans=len(report['removed_scans']))
                details['artifact_cleanup'] = retention_gc(config, job)
            store.transition(job['id'],('publishing','publication_unknown'),'succeeded',
                             result=details)
            subprocess.call(['docker','rm','wow-auction-import-'+job['id']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        finally:
            os.close(fd);lock.unlink()


def tick(config):
    store=Store(config['root'],config['switch_lock'])
    with guard(store.root): job=store.active()
    if not job or job['phase'] in ('awaiting_upload','uploading'): return
    try:
        process(config,store,job)
    except Exception as exc:
        current=store.job(job['id'])
        if current['phase'] in ('publishing','publication_unknown'):
            try: known_old=dataset.sha256(live(config))==current['base_sha256']
            except Exception: known_old=False
            if not known_old:
                store.transition(job['id'],('publishing','publication_unknown'),'publication_unknown',error='Publication outcome requires reconciliation')
                print('Publication pending reconciliation: '+job['id'],flush=True)
                return
        if current['phase'] not in ('succeeded','failed'):
            store.transition(job['id'],(current['phase'],),'failed',error=str(exc))
        status(config,'failed')
        print('Sync failed: '+job['id']+' '+str(exc),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args();config=read(args.config)
    import fcntl
    with (Path(config['root'])/'worker.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try: tick(config)
            except Exception as exc: print('Worker state error: '+str(exc),flush=True)
            time.sleep(5)

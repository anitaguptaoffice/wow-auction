"""Reclaim only managed terminal job artifacts; preserve receipts and rollback references."""
import json
from pathlib import Path
import shutil
import time

import dataset
from sync_state import read, write


def within(path, folder):
    try:
        Path(path).resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def collect(config, current_job, now=None, dry_run=False):
    """Caller must hold the active retention reservation through this operation."""
    now = time.time() if now is None else now
    cutoff = now - current_job['retention']['rollback_hours'] * 3600
    root = Path(config['root'])/'jobs'
    protected = []
    for path in Path(config['backup_dir']).glob('*.json'):
        record = read(path)
        # Incomplete switches always require manual reconciliation.
        if path.stat().st_mtime >= cutoff or record.get('status') == 'preparing':
            protected.extend(record[k] for k in ('database','previous_database') if record.get(k))
    containers = []
    names = dataset.command('docker','ps','-a','--format','{{.Names}}').splitlines()
    for name in names:
        info = json.loads(dataset.command('docker','inspect',name))[0]
        mounts = [m['Source'] for m in info['Mounts'] if m.get('Type') == 'bind']
        disposable = (not info['State']['Running'] and
                      name.startswith(('wow-auction-data-', 'wow-auction-import-')))
        if not disposable:
            protected.extend(mounts)
        containers.append((name, mounts, disposable))
    removed, skipped = [], []
    reclaimed = 0
    for folder in sorted(root.iterdir()):
        if folder.is_symlink() or not folder.is_dir() or folder.name == current_job['id']:
            continue
        manifest = folder/'job.json'
        if not manifest.is_file() or manifest.is_symlink():
            continue
        job = read(manifest)
        if job.get('phase') not in ('succeeded','failed') or job['updated_at'] >= cutoff:
            continue
        # Protect entire job directories if any live/recent rollback reference uses them.
        if any(within(p,folder) or within(folder,Path(p)) for p in protected):
            skipped.append(folder.name)
            continue
        artifacts = [folder/name for name in ('generation','snapshot.jsonl.gz','upload.part')
                     if (folder/name).exists() and not (folder/name).is_symlink()]
        if not artifacts:
            continue
        targets = [name for name,mounts,disposable in containers
                   if disposable and any(within(p,folder) for p in mounts)]
        size = sum(p.stat().st_size for artifact in artifacts
                   for p in (artifact.rglob('*') if artifact.is_dir() else [artifact])
                   if p.is_file() and not p.is_symlink())
        if not dry_run:
            # No force removal: a container unexpectedly running makes GC fail closed.
            for name in targets: dataset.command('docker','rm',name)
            for artifact in artifacts:
                if artifact.is_dir(): shutil.rmtree(str(artifact))
                else: artifact.unlink()
            job['artifacts_reclaimed_at'] = int(now)
            write(manifest,job)
        reclaimed += size
        removed.append(dict(job_id=folder.name,bytes=size,containers=targets))
    return dict(dry_run=dry_run,reclaimed_bytes=reclaimed,removed=removed,protected_jobs=skipped)

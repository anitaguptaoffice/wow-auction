"""Preview or enqueue periodic retention using the existing durable worker (Python 3.6)."""
import argparse
import json
from pathlib import Path
import tempfile
import subprocess

import dataset
from retention_policy import validate
from sync_state import Busy, Store, guard, read, write
from sync_worker import live


def preview(worker, policy):
    # The shared reservation prevents a publication or GC from removing this base.
    store = Store(worker['root'], worker['switch_lock'])
    with guard(store.root):
        if store.active() or Path(worker['switch_lock']).exists():
            raise Busy('Another sync or publication is active; retry preview later')
        base = live(worker)
        with tempfile.TemporaryDirectory(prefix='retention-preview-') as folder:
            manifest = Path(folder)/'job.json'
            manifest.write_text(json.dumps({'retention': policy}))
            # TemporaryDirectory is private by default; the container runs as nobody.
            import os
            os.chmod(folder, 0o755)
            manifest.chmod(0o644)
            command = ['docker', 'run', '--rm', '--network', 'none',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--memory', '512m', '--memory-swap', '512m', '--cpus', '1', '--pids-limit', '64',
                '-v', str(base.parent)+':/base:ro', '-v', folder+':/work:ro',
                worker['import_image'], 'python', '-m', 'compact_market.retention',
                '--base', '/base/'+base.name, '--manifest', '/work/job.json']
            # Docker may emit host capability warnings on stderr before the JSON.
            return json.loads(subprocess.check_output(command, universal_newlines=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Enqueue cleanup; default is preview only')
    args = parser.parse_args()
    config = read(args.config)
    worker = read(config['worker_config'])
    policy = validate(config.get('policy', {}))
    try:
        if args.apply:
            store = Store(worker['root'], worker['switch_lock'])
            # One policy is used by the timer, ingress and queued imports.
            with guard(store.root):
                write(store.root/'retention-policy.json', policy)
            job = store.schedule_retention(policy)
            result = dict(status='queued', job_id=job['id'], policy=policy)
        else:
            result = preview(worker, policy)
    except Busy as exc:
        result = dict(status='busy', detail=str(exc))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

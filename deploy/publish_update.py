"""Publish a verified data generation using an existing OpenSSH key/alias, never a password."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid
import tarfile
import tempfile


def publish(config, generation, expected_sha):
    generation = Path(generation).resolve()
    host = config['host']
    root = config['remote_root'].rstrip('/')
    if (not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.@-]*', host)
            or not re.fullmatch(r'/[a-zA-Z0-9_/-]+', root)
            or not re.fullmatch(r'[a-zA-Z0-9_-]+', generation.name)
            or not 1 <= int(config.get('port', 22)) <= 65535):
        raise ValueError('Invalid SSH host, port, root or generation name')
    report = json.loads((generation / 'market.report.json').read_text(encoding='utf-8'))
    target = root + '/' + generation.name
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-p', str(config.get('port', 22)), host]
    def remote(*args):
        return subprocess.run(ssh + [shlex.join(args)], check=True, capture_output=True, text=True,
                              encoding='utf-8', timeout=300)
    # Reuse the remote durable receipt after an interrupted reply without reuploading a mounted database.
    probe = remote('python3', '-c', 'import os,sys; print(int(os.path.exists(sys.argv[1])))', target)
    if probe.stdout.strip() == '0':
        staging = target + '.upload-' + uuid.uuid4().hex[:8]
        remote('mkdir', '-p', staging)
        files = [generation / name for name in ('market.duckdb', 'market.report.json', 'icon-map.json')]
        files.append(Path(__file__).with_name('dataset.py'))
        if (generation / 'wow-icons').is_dir():
            files.append(generation / 'wow-icons')
        # One compressed transfer avoids a round trip for every cached icon.
        with tempfile.TemporaryDirectory(prefix='wow-auction-publish-') as temporary:
            bundle = Path(temporary) / 'bundle.tar.gz'
            with tarfile.open(bundle, 'w:gz', compresslevel=1) as archive:
                for file in files:
                    archive.add(file, arcname=file.name)
            scp = ['scp', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-P', str(config.get('port', 22))]
            subprocess.run(scp + [str(bundle), host + ':' + staging + '/bundle.tar.gz'],
                           check=True, stdout=sys.stderr, stderr=sys.stderr, timeout=1800)
        remote('tar', '-xzf', staging + '/bundle.tar.gz', '-C', staging)
        remote('python3', '-c', 'import os,sys; os.unlink(sys.argv[1])', staging + '/bundle.tar.gz')
        remote('python3', '-c', 'import os,sys; os.rename(sys.argv[1],sys.argv[2])', staging, target)
    response = remote('python3', target + '/dataset.py', 'activate', '--database', target + '/market.duckdb',
                      '--report', target + '/market.report.json', '--config', config['nginx_config'],
                      '--backup-dir', config['backup_dir'], '--expected-current-sha', expected_sha,
                      '--public-url', config['public_url'])
    receipt = json.loads(response.stdout)
    if receipt.get('database_sha256') != report['database_sha256'] or receipt.get('status') != 'active':
        raise ValueError('Remote receipt does not confirm this generation')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--generation', required=True, type=Path)
    parser.add_argument('--expected-sha', required=True)
    args = parser.parse_args()
    print(json.dumps(publish(json.loads(args.config.read_text(encoding='utf-8')), args.generation, args.expected_sha)))

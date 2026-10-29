import argparse
import contextlib
import io
import json
from pathlib import Path
import shlex
from types import SimpleNamespace
import tempfile
import tarfile
import unittest
from unittest.mock import patch

from deploy import dataset, publish_update


class UpdatePublishTest(unittest.TestCase):
    def test_upload_is_staged_and_existing_generations_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            generation = Path(temporary) / 'generation-1'
            generation.mkdir()
            (generation / 'market.report.json').write_text(json.dumps(dict(database_sha256='verified')), encoding='utf-8')
            (generation / 'market.duckdb').write_bytes(b'verified candidate')
            (generation / 'icon-map.json').write_text('{}', encoding='utf-8')
            config = dict(host='wow-auction', port=9722, remote_root='/opt/wow-auction/data/automatic',
                          nginx_config='/etc/nginx/api.conf', backup_dir='/opt/wow-auction/backups', public_url='https://example.test')
            calls = []
            exists = False
            def run(args, **kwargs):
                calls.append(args)
                if args[0] == 'scp':
                    with tarfile.open(args[-2]) as bundle:
                        self.assertEqual(set(bundle.getnames()), {'market.duckdb', 'market.report.json', 'icon-map.json', 'dataset.py'})
                if args[0] == 'ssh':
                    remote = shlex.split(args[-1])
                    if remote[:2] == ['python3', '-c'] and 'os.path.exists' in remote[2]:
                        return SimpleNamespace(stdout='1' if exists else '0')
                    if 'activate' in remote:
                        return SimpleNamespace(stdout=json.dumps(dict(status='active', database_sha256='verified')))
                return SimpleNamespace(stdout='')
            with patch.object(publish_update.subprocess, 'run', side_effect=run):
                publish_update.publish(config, generation, 'old')
                scp = next(c for c in calls if c[0] == 'scp')
                self.assertIn('.upload-', scp[-1])
                self.assertTrue(any('os.rename' in c[-1] for c in calls if c[0] == 'ssh'))
                calls.clear()
                exists = True
                publish_update.publish(config, generation, 'old')
                self.assertFalse(any(c[0] == 'scp' for c in calls))
            config['host'] = '-oProxyCommand=bad'
            with self.assertRaises(ValueError):
                publish_update.publish(config, generation, 'old')

    def test_activation_discovers_current_container_and_saves_idempotent_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'automatic' / 'generation-1'
            folder.mkdir(parents=True)
            database = folder / 'market.duckdb'
            database.write_bytes(b'new')
            old = root / 'old.duckdb'
            old.write_bytes(b'old')
            config = root / 'api.conf'
            config.write_bytes(b'proxy_pass http://127.0.0.1:8180;')
            report = folder / 'market.report.json'
            report.write_text(json.dumps(dict(database_sha256=dataset.sha256(database), scans=[dict(scan_id=1)])), encoding='utf-8')
            args = argparse.Namespace(database=str(database), report=str(report), config=str(config),
                backup_dir=str(root / 'backups'), expected_current_sha=dataset.sha256(old), public_url='https://example.test')
            active_db = old
            def command(*command):
                if command[:2] == ('docker', 'ps'):
                    return 'old\nunrelated'
                if command[3] == '{{json .NetworkSettings.Ports}}':
                    return json.dumps({'8000/tcp': [{'HostIp':'127.0.0.1', 'HostPort':'8180'}]} if command[-1] == 'old' else {})
                if command[3] == '{{json .Mounts}}':
                    return json.dumps([dict(Source=str(active_db), Destination='/data/market.duckdb')])
                raise AssertionError(command)
            receipt = dict(status='active', database_sha256=dataset.sha256(database), rollback_record='saved.json')
            with patch.object(dataset, 'command', side_effect=command), patch.object(dataset, 'switch', return_value=receipt) as switch:
                self.assertEqual(dataset.activate(args), receipt)
                params = switch.call_args.args[0]
                self.assertEqual((params.previous_container, params.previous_port, params.port), ('old', 8180, 8181))
                active_db = database
                self.assertEqual(dataset.activate(args), receipt)
                self.assertEqual(switch.call_count, 1)
            self.assertEqual(json.loads((root / 'automatic/runtime/status.json').read_text())['phase'], 'published')

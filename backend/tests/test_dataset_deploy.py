"""Data-only switch refuses stale inputs and restores the old route on failure."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy import dataset


class DatasetDeployTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = self.root / 'old.duckdb'
        self.new = self.root / 'new.duckdb'
        self.old.write_bytes(b'old verified generation')
        self.new.write_bytes(b'new verified generation')
        self.config = self.root / 'api.conf'
        self.original = b'location /api/ {\n proxy_pass http://127.0.0.1:8180;\n}\n'
        self.config.write_bytes(self.original)
        self.scans = [dict(scan_id=4, region_id=5, realm_id=707)]
        self.report = self.root / 'report.json'
        self.report.write_text(json.dumps(dict(database_sha256=dataset.sha256(self.new), scans=self.scans,
            roundtrip_checks=[dict(all_modeled_fields_match=True)])), encoding='utf-8')
        self.args = argparse.Namespace(database=str(self.new), report=str(self.report), config=str(self.config),
            backup_dir=str(self.root / 'backups'), expected_current_sha=dataset.sha256(self.old), expect_scans=1,
            previous_container='old', container='new', previous_port=8180, port=8181, public_url='https://example.test')
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def run_command(self, *args):
        self.calls.append(args)
        if '{{.State.Running}}' in args:
            return 'true'
        if '{{json .Mounts}}' in args:
            return json.dumps([dict(Source=str(self.old), Destination='/data/market.duckdb')])
        if '{{.Image}}' in args:
            return 'sha256:original-image'
        return ''

    def test_catalog_checks_every_snapshot_and_realm(self):
        valid = dict(realms=[dict(regionID=5, realmID=0, scans=[dict(scanId=8)]),
                             dict(regionID=5, realmID=707, scans=[dict(scanId=9)])])
        dataset.verify_catalog(valid, self.scans)
        valid['realms'].append(dict(regionID=5, realmID=741, scans=[dict(scanId=7)]))
        with self.assertRaises(ValueError):
            dataset.verify_catalog(valid, self.scans)

    def test_switch_uses_same_image_and_rolls_back_without_rebuilding(self):
        with patch.object(dataset, 'command', side_effect=self.run_command), \
             patch.object(dataset, 'api_json', return_value={'status': 'ok'}), \
             patch.object(dataset, 'wait_ready'), patch.object(dataset, 'wait_matching_catalog'):
            result = dataset.switch(self.args)
            self.assertIn(b':8181;', self.config.read_bytes())
            run = next(c for c in self.calls if c[:2] == ('docker', 'run'))
            self.assertEqual(run[-1], 'sha256:original-image')
            self.assertNotIn('build', run)
            rollback = dataset.rollback(argparse.Namespace(record=result['rollback_record']))
            self.assertEqual(rollback['status'], 'rolled_back')
            self.assertEqual(self.config.read_bytes(), self.original)
            self.assertEqual(dataset.rollback(argparse.Namespace(record=result['rollback_record']))['already'], True)

    def test_bad_health_leaves_route_unchanged(self):
        with patch.object(dataset, 'command', side_effect=self.run_command), \
             patch.object(dataset, 'api_json', return_value={}), \
             patch.object(dataset, 'wait_ready', side_effect=RuntimeError('bad health')):
            with self.assertRaises(RuntimeError):
                dataset.switch(self.args)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertIn(('docker', 'stop', 'new'), self.calls)
        self.assertNotIn(('docker', 'stop', 'old'), self.calls)

    def test_failed_nginx_validation_restores_old_configuration(self):
        failed = False
        def command(*args):
            nonlocal failed
            if args == ('nginx', '-t') and not failed:
                failed = True
                raise RuntimeError('bad nginx')
            return self.run_command(*args)
        with patch.object(dataset, 'command', side_effect=command), \
             patch.object(dataset, 'api_json', return_value={}), patch.object(dataset, 'wait_ready'):
            with self.assertRaises(RuntimeError):
                dataset.switch(self.args)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertNotIn(('docker', 'stop', 'old'), self.calls)

    def test_stale_generation_and_changed_upstream_are_rejected(self):
        self.args.expected_current_sha = '0' * 64
        with patch.object(dataset, 'command', side_effect=self.run_command), self.assertRaises(ValueError):
            dataset.switch(self.args)
        self.assertFalse(any(c[:2] == ('docker', 'run') for c in self.calls))
        with self.assertRaises(ValueError):
            dataset.replace_upstream(self.original * 2, 8180, 8181)

    def test_later_deployment_cannot_be_overwritten_by_older_rollback(self):
        with patch.object(dataset, 'command', side_effect=self.run_command), \
             patch.object(dataset, 'api_json', return_value={}), \
             patch.object(dataset, 'wait_ready'), patch.object(dataset, 'wait_matching_catalog'):
            result = dataset.switch(self.args)
        self.config.write_bytes(b'newer deployment')
        with self.assertRaises(ValueError):
            dataset.rollback(argparse.Namespace(record=result['rollback_record']))

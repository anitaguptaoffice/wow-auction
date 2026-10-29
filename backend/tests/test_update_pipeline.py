"""Real candidate acceptance and durable failure/publication recovery contracts."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import subprocess
import os
import sys
import tarfile
from unittest.mock import patch

from compact_market.build import build
from compact_market.manage import stage_generation, _verify_scan
from compact_market.update import run_update, reconcile, discover_client_sources, prepare_icons
from tests.test_market_pipeline import _lua_snapshot


class UpdatePipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = self.root / 'old.lua'
        self.new = self.root / 'new.lua'
        self.old.write_text(_lua_snapshot(), encoding='utf-8')
        self.new.write_text(_lua_snapshot(timestamp=1700086400), encoding='utf-8')
        self.database = self.root / 'seed.duckdb'
        with contextlib.redirect_stdout(io.StringIO()):
            build([self.old], self.database)
        self.config = dict(root=str(self.root / 'updates'), seed_database=str(self.database), sources=[str(self.new)])

    def tearDown(self):
        self.temp.cleanup()

    def run_job(self, **kwargs):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            return run_update(self.config, **kwargs)

    def current(self):
        return json.loads((Path(self.config['root']) / 'current.json').read_text(encoding='utf-8'))

    def test_pipeline_accepts_exact_candidate_and_repeat_is_noop(self):
        job = self.run_job()
        self.assertEqual(job['phase'], 'ready')
        first = self.current()
        self.assertTrue(Path(first['database']).is_file())
        self.assertTrue(Path(first['database']).with_name('icon-map.json').is_file())
        self.assertEqual(self.run_job()['phase'], 'unchanged')
        self.assertEqual(self.current(), first)

    def test_bad_source_or_failed_acceptance_preserves_current_and_is_retryable(self):
        self.run_job()
        first = self.current()
        bad = self.root / 'bad.lua'
        bad.write_text('AuctionSearchDB={}', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.run_job(sources=[str(bad)])
        self.assertEqual(self.current(), first)
        later = self.root / 'later.lua'
        later.write_text(_lua_snapshot(timestamp=1700172800), encoding='utf-8')
        with patch('compact_market.update.accept', side_effect=ValueError('acceptance failed')):
            with self.assertRaises(ValueError):
                self.run_job(sources=[str(later)])
        self.assertEqual(self.current(), first)
        self.assertEqual(self.run_job(sources=[str(later)])['phase'], 'ready')

    def test_prepared_generation_can_be_published_and_unknown_reply_requires_reconciliation(self):
        self.run_job()
        prepared = self.current()
        self.config['publish_command'] = ['publisher', '{generation}']
        with patch('compact_market.update.hook', side_effect=TimeoutError('reply lost')):
            with self.assertRaises(TimeoutError):
                self.run_job(sources=[], publish=True)
        self.assertEqual(self.current(), prepared)
        with self.assertRaisesRegex(ValueError, 'outcome is unknown'):
            self.run_job(publish=True)
        receipt = dict(status='active', database_sha256=prepared['database_sha256'], rollback_record='retained.json')
        reconcile(self.config['root'], receipt)
        self.assertEqual(self.current()['publication'], receipt)
        self.assertEqual(self.run_job(publish=True)['phase'], 'unchanged')

    def test_publisher_receipt_must_confirm_exact_database(self):
        self.config['publish_command'] = ['publisher', '{generation}']
        def receipt(command, context):
            report = json.loads(Path(context['report']).read_text(encoding='utf-8'))
            return SimpleNamespace(stdout=json.dumps(dict(status='active', database_sha256=report['database_sha256'])))
        with patch('compact_market.update.hook', side_effect=receipt):
            self.assertEqual(self.run_job(publish=True)['phase'], 'published')
        self.assertEqual(self.current()['publication']['database_sha256'], self.current()['database_sha256'])

    def test_automatic_updates_do_not_resurrect_old_snapshots_or_add_unconfigured_realms(self):
        self.run_job()
        current = self.current()
        source = self.root / 'unexpected.lua'
        for content in (_lua_snapshot(timestamp=1700000600), _lua_snapshot(timestamp=1700172800, realm_id=456)):
            source.write_text(content, encoding='utf-8')
            with self.assertRaises(ValueError):
                self.run_job(sources=[str(source)])
            self.assertEqual(self.current(), current)

    def test_incremental_reuses_certified_rows_and_full_audit_remains_available(self):
        first = self.run_job()
        base = Path(first['candidate_database'])
        third = self.root / 'third.lua'
        third.write_text(_lua_snapshot(timestamp=1700172800), encoding='utf-8')
        with patch('compact_market.manage._verify_scan', wraps=_verify_scan) as verify, contextlib.redirect_stdout(io.StringIO()):
            report = stage_generation(base, self.root / 'incremental.duckdb', sources=[third])
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(report['inherited_scan_ids'], [1, 2])
        self.assertFalse(report['summaries_rebuilt'])
        with patch('compact_market.manage._verify_scan', wraps=_verify_scan) as verify, contextlib.redirect_stdout(io.StringIO()):
            audited = stage_generation(base, self.root / 'audited.duckdb', full_verify=True)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(audited['inherited_scan_ids'], [])

    def test_runner_compatible_entrypoint_returns_scan_reconciliation(self):
        config = self.root / 'config.json'
        config.write_text(json.dumps(self.config), encoding='utf-8')
        env = dict(os.environ, COMPACT_UPDATE_CONFIG=str(config), PYTHONPATH=str(Path(__file__).parents[1]))
        args = [sys.executable, '-X', 'utf8', str(Path(__file__).parents[1] / 'import_compact.py'), str(self.new), '--json']
        first = json.loads(subprocess.check_output(args, env=env, encoding='utf-8', stderr=subprocess.DEVNULL))
        again = json.loads(subprocess.check_output(args, env=env, encoding='utf-8', stderr=subprocess.DEVNULL))
        self.assertTrue(first['ok'])
        self.assertEqual((first['source_scan_count'], first['imported_scan_count'], first['skipped_scan_count']), (1, 1, 0))
        self.assertEqual((again['source_scan_count'], again['imported_scan_count'], again['skipped_scan_count']), (1, 0, 1))
        self.assertEqual(first['scan_ids'], again['scan_ids'])

    def test_client_discovery_ignores_other_realms_and_old_or_empty_files(self):
        client = self.root / 'client'
        for name, realm, timestamp in [('new', 123, 1700086400), ('old', 123, 1700000000), ('other', 456, 1700086400)]:
            source = client / 'account' / str(realm) / name / 'SavedVariables/AuctionSearchExample.lua'
            source.parent.mkdir(parents=True)
            source.write_text('AuctionSearchDB = { ["lastScan"] = { ["regionID"] = 5, ["realmID"] = %d }, ["lastScanTime"] = %d }' % (realm, timestamp), encoding='utf-8')
        self.config['client_roots'] = [str(client)]
        found = discover_client_sources(self.config)
        self.assertEqual(len(found['sources']), 1)
        self.assertIn('new', found['sources'][0])
        self.assertEqual({r['reason'] for r in found['skipped']}, {'not_newer_than_current', 'realm_not_configured'})
        archives = self.root / 'archives'
        archives.mkdir()
        with tarfile.open(archives / 'auction-new.lua.tgz', 'w:gz') as archive:
            archive.add(found['sources'][0], arcname='auction.lua')
        self.config['archive_roots'] = [str(archives)]
        self.assertEqual(len(discover_client_sources(self.config)['sources']), 2)

    def test_existing_icon_punctuation_is_preserved_but_paths_are_rejected(self):
        names = self.root / 'icons.json'
        output = self.root / 'icons/icon-map.json'
        output.parent.mkdir()
        names.write_text(json.dumps({'100': 'inv_misc_fork&knife', '200': 'inv_glove_mail_draenorcrafted_d_01_alliance]'}), encoding='utf-8')
        prepare_icons(self.database, output, names)
        self.assertEqual(json.loads(output.read_text())['100'], 'inv_misc_fork&knife')
        names.write_text(json.dumps({'100': '../escape'}), encoding='utf-8')
        with self.assertRaises(ValueError):
            prepare_icons(self.database, output, names)

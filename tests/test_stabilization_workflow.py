"""Saved-store stabilization ownership with real local FFmpeg and generated media.

Framework adapters reuse the project's guarded store fixtures. The encoder,
byte capture, cache verification, atomic publication and Undo are production.
"""
import asyncio
import copy
import hashlib
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch

import test_clip_attributes_workflow as base
import test_project_sync as store
from test_media_collection import run_async_check
import stabilization
from project_recovery import ProjectRecoveryRequired


class Stabilization(base.StoreClipAttributes):
    for _name in dir(base.StoreClipAttributes):
        if _name.startswith('test_'): locals()[_name] = None

    def inspect(self, **changes):
        return self.route('stabilization_inspect', {'media_id': 'm', 'shakiness': 5,
            'force': False, '_context': self.current(), 'actor': 'human', **changes})

    def analyze(self, review, **changes):
        return self.route('stabilize', {'media_id': review['requested_media_id'],
            **review['settings'], '_context': review['context'], 'fingerprint': review['fingerprint'],
            'actor': 'human', **changes})

    def test_readonly_inspect_real_windows_paths_one_undo_redo_and_cache(self):
        # Both the input and the library have apostrophes/non-ASCII. Explicit
        # TEMP in the runner also exercises the generated-input scratch path.
        target = self.root / "Émile's moving source.mkv"
        shutil.copyfile(self.source, target)
        p = self.project(); p['media']['m']['path'] = str(target); self.env['save_project'](p)
        before = self.project(); raw = (self.raw(), self.raw('b'))
        review = self.inspect(); self.assertEqual((self.raw(), self.raw('b')), raw)
        self.assertFalse((self.root / 'stab').exists())
        reply = self.analyze(review); after = self.project()
        self.assertTrue(reply['changed']); self.assertFalse(reply['cached'])
        self.assertEqual(after['sequences'], before['sequences'])
        self.assertEqual(self.raw('b'), raw[1])
        self.assertTrue(Path(reply['trf']).read_bytes().startswith(b'VID.STAB 1'))
        self.assertEqual(reply['analysis']['source']['sha256'], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 1)
        self.invoke('undo', {'_context': self.current()}); self.assertEqual(self.project()['media'], before['media'])
        self.assertTrue(Path(reply['trf']).exists())
        self.invoke('redo', {'_context': self.current()}); self.assertEqual(self.project()['media'], after['media'])
        cached = self.analyze(self.inspect())
        self.assertTrue(cached['cached']); self.assertFalse(cached['changed'])
        self.assertEqual(cached['trf'], reply['trf'])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 1)

    def test_force_regenerates_without_overwriting_cache_or_legacy(self):
        legacy = self.root / 'stab' / 'm.trf'; legacy.parent.mkdir(); legacy.write_bytes(b'legacy binary')
        p = self.project(); p['media']['m']['stab_trf'] = str(legacy); self.env['save_project'](p)
        first = self.analyze(self.inspect()); first_bytes = Path(first['trf']).read_bytes()
        forced = self.analyze(self.inspect(force=True))
        self.assertNotEqual(first['trf'], forced['trf']); self.assertFalse(forced['cached'])
        self.assertEqual(Path(first['trf']).read_bytes(), first_bytes)
        self.assertEqual(legacy.read_bytes(), b'legacy binary')
        self.invoke('undo', {'_context': self.current()})
        self.assertEqual(self.project()['media']['m']['stab_trf'], first['trf'])

    def test_tampered_result_or_settings_are_not_reused(self):
        first = self.analyze(self.inspect()); Path(first['trf']).write_bytes(b'VID.STAB 1\n# tampered\n')
        second = self.analyze(self.inspect()); self.assertFalse(second['cached'])
        self.assertNotEqual(first['trf'], second['trf'])
        third = self.analyze(self.inspect(shakiness=6)); self.assertFalse(third['cached'])
        self.assertNotEqual(second['trf'], third['trf'])

    def test_context_fingerprint_source_change_and_invalid_settings_refuse(self):
        review = self.inspect(); original = self.raw()
        for changes in ({'_context': None}, {'fingerprint': '0'*64}, {'shakiness': 6}, {'fingerprint': None}):
            with self.assertRaises(store.HTTPError): self.analyze(review, **changes)
            self.assertEqual(self.raw(), original)
        for settings in ({'shakiness': True}, {'shakiness': 11}, {'force': 1}):
            with self.assertRaises(store.HTTPError): self.inspect(**settings)
        self.source.write_bytes(self.source.read_bytes() + b'changed')
        with self.assertRaises(store.HTTPError) as error: self.analyze(review)
        self.assertEqual(error.exception.status_code, 409); self.assertEqual(self.raw(), original)
        self.assertFalse((self.root / 'stab').exists())

    def test_still_and_generated_media_refuse_without_writing(self):
        before = self.raw()
        for key in ('is_image', 'still', 'synthetic', 'sequence_frames', 'input_opts'):
            p = self.project(); p['media']['m'][key] = ['-framerate', '24'] if key == 'input_opts' else True; self.env['save_project'](p)
            raw = self.raw()
            with self.assertRaises(store.HTTPError): self.inspect()
            self.assertEqual(self.raw(), raw)
            p['media']['m'].pop(key); self.env['save_project'](p)
        self.assertFalse((self.root / 'stab').exists())

    def test_malformed_saved_input_options_require_recovery_without_analysis_or_rewrite(self):
        project = self.project(); project['media']['m']['input_opts'] = True
        self.env['save_project'](project); raw = self.raw()
        with self.assertRaises(ProjectRecoveryRequired): self.inspect()
        self.assertEqual(self.raw(), raw)
        self.assertFalse((self.root / 'stab').exists())

    def test_subclip_uses_physical_source_and_proposal_policy_refuses(self):
        p = self.project(); p['media']['sub'] = {**copy.deepcopy(p['media']['m']),
            'id': 'sub', 'subclip_of': 'm', 'sub_in': 1, 'duration': 2}
        self.env['save_project'](p)
        review = self.inspect(media_id='sub'); self.assertEqual(review['media_id'], 'm')
        (self.root / 'settings.json').write_text('{"agent_mode":"proposals_only"}')
        before = self.raw()
        with self.assertRaises(store.HTTPError): self.analyze(review, actor='agent')
        self.assertEqual(self.raw(), before)
        reply = self.analyze(review)
        self.assertEqual(set(reply['media']), {'m', 'sub'})
        self.assertEqual(self.project()['media']['sub']['stab_analysis'], self.project()['media']['m']['stab_analysis'])

    def test_nested_alias_receives_the_reviewed_physical_analysis(self):
        p = self.project(); p['media']['sub'] = {**copy.deepcopy(p['media']['m']), 'id': 'sub', 'subclip_of': 'm'}
        p['media']['nested'] = {**copy.deepcopy(p['media']['m']), 'id': 'nested', 'subclip_of': 'sub'}
        self.env['save_project'](p)
        review = self.inspect(media_id='nested')
        self.assertEqual(review['affected_media_ids'], ['m', 'nested', 'sub'])
        result = self.analyze(review)
        self.assertEqual(result['media'], review['affected_media_ids'])
        for mid in result['media']:
            self.assertEqual(self.project()['media'][mid]['stab_trf'], result['trf'])
            self.assertEqual(self.project()['media'][mid]['stab_analysis'], result['analysis'])

    def moving_source(self):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
            'testsrc2=size=128x72:rate=60:duration=32', '-c:v', 'ffv1', '-threads', '1',
            str(self.source)], check=True, capture_output=True, timeout=30)

    def in_flight(self, action, *, cancel=False):
        self.moving_source(); review = self.inspect(force=True)
        body = {'media_id': 'm', **review['settings'], '_context': review['context'],
                'fingerprint': review['fingerprint'], 'actor': 'human'}
        started = threading.Event(); children = []
        actual = stabilization.subprocess.Popen
        def child(*args, **kwargs):
            process = actual(*args, **kwargs); children.append(process); started.set(); return process
        async def scenario():
            pending = asyncio.create_task(self.env['stabilize'](store.Request(body)))
            for _ in range(20000):
                if started.is_set(): break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set(), 'Real FFmpeg child did not start')
            action()
            if cancel: pending.cancel()
            with self.assertRaises(asyncio.CancelledError if cancel else store.HTTPError) as error:
                await pending
            if not cancel: self.assertEqual(error.exception.status_code, 409)
        with patch.object(stabilization.subprocess, 'Popen', child): run_async_check(scenario())
        self.assertTrue(children); self.assertTrue(all(p.poll() is not None for p in children))
        self.assertEqual(list((self.root / 'stab').glob('.pending-*')), [])
        self.assertEqual(list((self.root / 'stab').glob('analysis-*')), [])

    def test_actual_encoder_in_flight_project_switch_never_changes_either_project(self):
        before = self.raw(), self.raw('b')
        self.in_flight(lambda: self.env['set_active_project']('b'))
        self.assertEqual((self.raw('a'), self.raw('b')), before)
        self.assertEqual(self.env['read_undo_history']('a')['undo'], [])
        self.assertEqual(self.env['read_undo_history']('b')['undo'], [])

    def test_actual_encoder_in_flight_source_replacement_refuses_captured_result(self):
        before = self.raw()
        replacement = self.root / 'replacement.mkv'; shutil.copyfile(self.source, replacement)
        self.in_flight(lambda: shutil.copyfile(replacement, self.source))
        self.assertEqual(self.raw(), before)

    def test_cancel_joins_actual_child_and_discards_unpublished_analysis(self):
        before = self.raw(); self.in_flight(lambda: None, cancel=True)
        self.assertEqual(self.raw(), before)

    def test_save_failure_retains_complete_published_evidence_without_metadata_change(self):
        review = self.inspect(); before = self.raw()
        with patch.dict(self.env, commit_edit=lambda *a, **kw: (_ for _ in ()).throw(OSError('forced precommit refusal'))):
            with self.assertRaises(store.HTTPError) as error: self.analyze(review)
        self.assertEqual(error.exception.status_code, 500)
        self.assertIn('Published analysis retained', str(error.exception))
        self.assertEqual(self.raw(), before)
        paths = list((self.root / 'stab').glob('analysis-*/transforms.trf'))
        self.assertEqual(len(paths), 1); self.assertTrue(paths[0].read_bytes().startswith(b'VID.STAB 1'))


if __name__ == '__main__': unittest.main()

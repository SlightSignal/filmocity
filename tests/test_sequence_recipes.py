"""Real render clocks, reference preservation and exact audio for sequence recipes."""
import array
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'tests')]
import sequence_recipes as recipes
from editing_workflow import transcript_basis, words_of
from render import clip_dur, seq_total, build_command
from render_context import RenderContext
from timeline_time import from_frames, to_frames
from test_recipe_plans import apply


def fixture(**changes):
    media = {'m': {'id': 'm', 'path': '/fixtures/source.mov', 'duration': 30, 'has_video': True, 'has_audio': True, 'width': 1920, 'height': 1080, 'fps': 30}}
    clip = {'id': 'picture', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': 8, 'speed': 1, **changes}
    graphic = {'id': 'hook', 'start': 0, 'in_': 0, 'out': 2, 'graphic': {'name': 'Hook', 'layers': [{'kind': 'shape'}, {'kind': 'text', 'text': 'Original headline', 'size': 96}, {'kind': 'text', 'text': 'Keep this label', 'size': 48}]}}
    seq = {'id': 's', 'name': 'Original', 'width': 1920, 'height': 1080, 'fps': 30,
           'tracks': [{'id': 'V1', 'kind': 'video', 'index': 0, 'clips': [clip]}, {'id': 'V3', 'kind': 'video', 'index': 2, 'clips': [graphic]}, {'id': 'A1', 'kind': 'audio', 'index': 0, 'clips': []}],
           'markers': [], 'captions': []}
    return {'version': 3, 'media': media, 'sequences': [seq]}, clip


def capture(project, mode='explainer', **options):
    body = {'sequence': 's', **({'chapters': False, 'end_card': 'Subscribe'} if mode == 'explainer' else {'hooks': ['New headline'], 'targets': [{'clip_id': 'hook', 'layer': 1}]}), **options}
    payload = recipes.capture(project, body, mode); payload.update(signature='captured', templates={})
    for key, path in [('lower_third', 'Lower_Third_-_Card'), ('chapter', 'Chapter_Title'), ('cta', 'CTA_-_Follow')]:
        payload['templates'][key] = json.loads((ROOT/'assets/templates'/f'{path}.json').read_text())
    return payload


def plan(project, payload, identity='sequence-recipe'):
    result = {'version': 1, 'kind': 'recipe', 'mode': payload['mode'], 'signature': payload['signature']}
    return recipes.plan(project, payload, result, identity)


class SequenceRecipes(unittest.TestCase):
    def test_explainer_preserves_locked_overlapped_tracks_and_unique_track_ids(self):
        p, c = fixture(); seq = p['sequences'][0]; seq['tracks'][0]['locked'] = True; seq['tracks'][1]['locked'] = True
        seq['tracks'][0]['clips'].append({**copy.deepcopy(c), 'id': 'overlap', 'start': 1, 'out': 6})
        seq['tracks'][1]['name'] = 'Explainer end cards'
        before = copy.deepcopy(p); payload = capture(p, lower_third={'name': 'Dr. Ada', 'role': 'Guest', 'at': 1}); planned = plan(p, payload); after = apply(p, planned)['sequences'][0]
        self.assertEqual(p, before); self.assertEqual(after['tracks'][:3], before['sequences'][0]['tracks']); self.assertEqual(len(planned['ops']), 1)
        self.assertEqual(len(after['tracks']), 5); self.assertEqual(len({t['id'] for t in after['tracks']}), 5)
        self.assertEqual([t['index'] for t in after['tracks'][3:]], [3, 4]); self.assertEqual(after['tracks'][-1]['name'], 'Explainer end cards 2')
        self.assertEqual(planned['summary']['locked_tracks'], ['V1', 'V3']); self.assertTrue(any('locked' in w for w in planned['summary']['warnings']))
        self.assertTrue(any('overlap' in w for w in planned['summary']['warnings']))

    def test_end_card_uses_render_ramp_hold_and_reverse_clocks(self):
        for changes, expected in [({'time_remap': [{'t': 0, 'v': 1}, {'t': 2, 'v': 3}]}, 10/3), ({'hold': True, 'speed': 2}, 8), ({'reverse': True, 'speed': 2}, 4)]:
            with self.subTest(changes=changes):
                p, c = fixture(**changes); payload = capture(p); planned = plan(p, payload); item = planned['summary']['placements'][0]
                self.assertAlmostEqual(seq_total(p['sequences'][0]), expected); self.assertAlmostEqual(item['end'], expected)
                self.assertAlmostEqual(item['start'], max(0, expected-3)); self.assertAlmostEqual(planned['summary']['achieved'], expected)

    def test_ntsc_cards_stay_inside_original_sequence_on_exact_grid(self):
        p, c = fixture(out=2.10001); seq = p['sequences'][0]; seq['fps'] = '30000/1001'
        seq['markers'] = [{'id': 'm1', 'type': 'chapter', 'name': 'First', 'time': .001}, {'id': 'm2', 'type': 'chapter', 'name': 'Almost end', 'time': 2.099}]
        payload = capture(p, chapters=True, lower_third={'name': 'Ada', 'at': .041, 'duration': 5}); planned = plan(p, payload)
        for item in planned['summary']['placements']:
            self.assertGreater(item['end'], item['start']); self.assertLessEqual(item['end'], 2.10001)
            self.assertEqual(item['start'], from_frames(to_frames(item['start'], seq['fps']), seq['fps']))
            self.assertEqual(item['end'], from_frames(to_frames(item['end'], seq['fps']), seq['fps']))
        self.assertTrue(any('Skipped chapter 2' in w for w in planned['summary']['warnings'])); self.assertTrue(any('shortened' in w for w in planned['summary']['warnings']))

    def test_nearby_chapters_are_clipped_without_overlap_or_invented_duration(self):
        p, _ = fixture(); p['sequences'][0]['markers'] = [{'id': str(i), 'type': 'chapter', 'name': str(i), 'time': t} for i, t in enumerate((1.001, 1.002, 1.04, 7.99))]
        planned = plan(p, capture(p, chapters=True, end_card='')); cards = planned['summary']['placements']
        self.assertEqual(len(cards), 2); self.assertTrue(all(a['end'] <= b['start'] for a, b in zip(cards, cards[1:])))
        self.assertEqual(len([w for w in planned['summary']['warnings'] if 'Skipped' in w]), 2)

    def test_empty_noop_and_no_available_frames_are_explicit(self):
        p, _ = fixture(); p['sequences'][0]['tracks'] = []
        planned = plan(p, capture(p, chapters=True, end_card='')); self.assertEqual(planned['ops'], []); self.assertEqual(planned['summary']['achieved'], 0)
        with self.assertRaisesRegex(ValueError, 'no complete picture frame'): plan(p, capture(p))
        p, c = fixture(out=.01); p['sequences'][0]['tracks'][1]['clips'] = []
        with self.assertRaisesRegex(ValueError, 'no complete picture frame'): plan(p, capture(p))
        p, _ = fixture()
        with self.assertRaisesRegex(ValueError, 'no complete picture frame'): plan(p, capture(p, lower_third={'name': 'Outside', 'at': 8}))

    def test_explicit_render_duration_caps_cards_without_changing_original_content(self):
        p, _ = fixture(); p['sequences'][0]['duration'] = 3.1
        planned = plan(p, capture(p)); after = apply(p, planned)['sequences'][0]
        self.assertEqual(after['tracks'][:3], p['sequences'][0]['tracks']); self.assertEqual(after['duration'], 3.1)
        self.assertEqual(planned['summary']['placements'][0]['end'], 3.1)

    def test_nested_sources_and_interpreted_subclip_dependencies_are_captured(self):
        p, c = fixture(); child = copy.deepcopy(p['sequences'][0]); child['id'] = 'child'; child['tracks'][1]['clips'] = []
        p['media']['m'].update(frame_rate='60000/1001', interpret_fps='30000/1001', duration=60)
        p['media']['sub'] = {**p['media']['m'], 'id': 'sub', 'subclip_of': 'm', 'sub_in': 10, 'duration': 12}
        child['tracks'][0]['clips'][0].update(media_id='sub', in_=2, out=10, reverse=True, time_remap=[{'t': 0, 'v': 1}, {'t': 2, 'v': 3}])
        c.pop('media_id'); c.update(sequence_id='child', in_=0, out=clip_dur(child['tracks'][0]['clips'][0])); p['sequences'].append(child)
        payload = capture(p); self.assertEqual(set(payload['media_basis']), {'m', 'sub'}); self.assertEqual(payload['sequence_dependencies'], {'child': child})
        planned = plan(p, payload); self.assertAlmostEqual(planned['summary']['achieved'], 10/3)
        p['sequences'][1]['tracks'][0]['clips'][0]['audio'] = {'gain_db': -6}
        with self.assertRaisesRegex(ValueError, 'dependency changed'): plan(p, payload)

    def test_dependency_cycles_missing_parents_and_duplicate_ids_fail_atomically(self):
        p, c = fixture(); before = copy.deepcopy(p)
        c['sequence_id'] = 's'
        with self.assertRaisesRegex(ValueError, 'cycle'): capture(p)
        p = before; p['sequences'][0]['tracks'].append(copy.deepcopy(p['sequences'][0]['tracks'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate track'): capture(p)
        p, c = fixture(); p['media']['m']['subclip_of'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'parents'): capture(p)

    def test_options_are_strict_finite_and_bounded(self):
        p, _ = fixture()
        for option in ({'chapters': 1}, {'end_card': None}, {'chapter_duration': 0}, {'end_duration': math.inf}, {'lower_third': {'name': 'N', 'at': True}}, {'lower_third': {'name': ''}}):
            with self.subTest(option=option), self.assertRaises(ValueError): capture(p, **option)
        for option in ({'hooks': []}, {'hooks': ['h']*21}, {'hooks': ['x'*501]}, {'targets': []}, {'targets': [{'clip_id': 'hook', 'layer': True}]}, {'targets': [{'clip_id': 'hook', 'layer': 0}]}, {'targets': [{'clip_id': 'hook', 'title': False}]}, {'targets': [{'clip_id': 'hook', 'layer': 1}]*2}):
            with self.subTest(option=option), self.assertRaises(ValueError): capture(p, 'variants', **option)
        p['sequences'][0]['markers'] = [{'id': str(i), 'type': 'chapter', 'name': str(i), 'time': i} for i in range(257)]
        with self.assertRaisesRegex(ValueError, '256'): capture(p, chapters=True)

    def test_variants_change_only_explicit_text_and_keep_original_untouched(self):
        p, _ = fixture(); p['sequences'][0]['tracks'][0]['locked'] = True
        p['sequences'][0]['captions'] = [{'id': 'cap', 'start': 0, 'end': 1, 'text': 'Original headline'}]; before = copy.deepcopy(p)
        payload = capture(p, 'variants', hooks=['First hook', 'Second hook']); planned = plan(p, payload); after = apply(p, planned)
        self.assertEqual(p, before); self.assertEqual(after['sequences'][0], before['sequences'][0]); self.assertEqual(len(planned['ops']), 2)
        for index, seq in enumerate(after['sequences'][1:]):
            layers = seq['tracks'][1]['clips'][0]['graphic']['layers']; self.assertEqual(layers[1]['text'], ['First\nhook', 'Second\nhook'][index])
            self.assertEqual(layers[2]['text'], 'Keep this label'); self.assertEqual(seq['captions'][0]['text'], 'Original headline'); self.assertTrue(seq['tracks'][0]['locked'])
            self.assertNotEqual(seq['captions'][0]['id'], 'cap')
        self.assertEqual(plan(p, payload), planned)
        self.assertTrue(set(c['id'] for t in after['sequences'][1]['tracks'] for c in t['clips']).isdisjoint(c['id'] for t in after['sequences'][2]['tracks'] for c in t['clips']))

    def test_no_unrelated_fallback_and_locked_text_target_refused(self):
        p, _ = fixture(); p['sequences'][0]['tracks'][1]['clips'][0]['graphic']['name'] = 'Lower third'
        with self.assertRaisesRegex(ValueError, 'explicit'): capture(p, 'variants', targets=None)
        p['sequences'][0]['tracks'][1]['locked'] = True
        with self.assertRaisesRegex(ValueError, 'Unlock'): capture(p, 'variants')
        self.assertEqual(p['sequences'][0]['tracks'][1]['clips'][0]['graphic']['layers'][1]['text'], 'Original headline')

    def test_title_target_and_nested_dependencies_remain_shared(self):
        p, c = fixture(); child = copy.deepcopy(p['sequences'][0]); child['id'] = 'child'; p['sequences'].append(child)
        c.pop('media_id'); c['sequence_id'] = 'child'; c['out'] = 8
        title = p['sequences'][0]['tracks'][1]['clips'][0]; title.pop('graphic'); title['title'] = {'text': 'Old', 'size': 60, 'color': '#ff0000'}
        planned = plan(p, capture(p, 'variants', targets=[{'clip_id': 'hook', 'title': True}], hooks=['New'])); after = apply(p, planned)
        self.assertEqual(after['sequences'][1], child); self.assertEqual(after['sequences'][-1]['tracks'][0]['clips'][0]['sequence_id'], 'child')
        self.assertEqual(after['sequences'][-1]['tracks'][1]['clips'][0]['title'], {'text': 'New', 'size': 60, 'color': '#ff0000'})
        self.assertTrue(any('shared' in w for w in planned['summary']['warnings']))

    def test_all_local_associations_mattes_review_ids_and_fresh_transcript_rebase(self):
        p, c = fixture(reverse=True, time_remap=[{'t': 0, 'v': 1}, {'t': 2, 'v': 3}], group='g', audio_detached_id='sound', rendered_from='picture', render_replace_task='old-bake')
        c['audio'] = {'linked': False, 'gain_db': -3, 'fade_window': {'version': 1, 'offset': 2, 'duration': 10}}
        c['source_edit_window'] = {'version': 1, 'ramp': {'offset': 2, 'points': [{'t': 0, 'v': 1}]}}
        c['fx_stack'] = [{'type': 'track_matte', 'enabled': False, 'params': {'track': 'V3', 'type': 'luma'}}]
        seq = p['sequences'][0]; seq['tracks'][2]['clips'] = [{'id': 'sound', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': 8, 'group': 'g', 'unlinked_from': 'picture'}]
        seq['markers'] = [{'id': 'marker', 'time': .5, 'clip_id': 'picture', 'track_id': 'V1'}]; seq['captions'] = [{'id': 'caption', 'start': 0, 'end': 1, 'text': 'Words'}]
        seq['workflow'] = {'caption_review_ids': ['caption'], 'analysis_task': 'old', 'recipe_tasks': {'old': {}}, 'sync_tasks': {'old': {}}, 'audio_tasks': {'old': {}}, 'render_replace': {'old-bake': {'clip_id': 'picture', 'media_id': 'm', 'sha256': 'captured-source'}}, 'transcript_task': 'old', 'custom_note': 'Preserve me'}
        seq['multicam_audio_track'] = 'A1'; seq['transcript'] = [{'w': 'Words', 's': 0, 'e': 1}]; seq['transcript_basis'] = transcript_basis(seq, p)
        planned = plan(p, capture(p, 'variants')); after = apply(p, planned); out = after['sequences'][-1]; picture, sound = out['tracks'][0]['clips'][0], out['tracks'][2]['clips'][0]
        self.assertEqual(picture['audio_detached_id'], sound['id']); self.assertEqual(sound['unlinked_from'], picture['id']); self.assertEqual(picture['group'], sound['group']); self.assertNotEqual(picture['group'], 'g')
        self.assertEqual(picture['rendered_from'], 'picture'); self.assertNotIn('render_replace_task', picture); self.assertEqual(picture['source_edit_window'], c['source_edit_window']); self.assertEqual(picture['audio'], c['audio'])
        self.assertEqual(picture['fx_stack'][0]['params']['track'], out['tracks'][1]['id']); self.assertEqual(out['multicam_audio_track'], out['tracks'][2]['id'])
        self.assertEqual(out['workflow'], {'caption_review_ids': [out['captions'][0]['id']], 'custom_note': 'Preserve me'})
        self.assertEqual(out['markers'][0]['clip_id'], picture['id']); self.assertEqual(out['markers'][0]['track_id'], out['tracks'][0]['id'])
        self.assertEqual(words_of(out, after), seq['transcript']); self.assertEqual(out['transcript_basis'], transcript_basis(out, after))

    def test_reference_scopes_with_colliding_nested_ids_and_source_history_are_preserved(self):
        p, c = fixture(); child = copy.deepcopy(p['sequences'][0]); child['id'] = 'child'; p['sequences'].append(child)
        c.pop('media_id'); c['sequence_id'] = 'child'; c['audio_detached_id'] = 'sound'
        p['sequences'][0]['tracks'][2]['clips'] = [{'id': 'sound', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': 8, 'unlinked_from': 'picture'}]
        c['source_edit_window'] = {'version': 1, 'annotation': {'sequence': 's', 'clip_id': 'picture'}}
        c['rendered_from'] = 'picture'; c['provenance'] = {'sequence_id': 's', 'clip_id': 'picture'}
        p['sequences'][0]['workflow'] = {'references': [
            {'sequence': 's', 'clip_id': 'picture', 'track_id': 'V1'},
            {'sequence': 'child', 'clip_id': 'picture', 'track_id': 'V1'},
            {'source_sequence_id': 'child', 'source_clip_id': 'picture', 'target_sequence_id': 's', 'target_clip_id': 'picture'},
            {'sequence_id': 'child', 'clip_ids': ['picture', 'hook'], 'custom_peer_id': 'picture'}]}
        planned = plan(p, capture(p, 'variants')); after = apply(p, planned); out = after['sequences'][-1]; picture = out['tracks'][0]['clips'][0]; sound = out['tracks'][2]['clips'][0]; refs = out['workflow']['references']
        self.assertEqual(refs[0], {'sequence': out['id'], 'clip_id': picture['id'], 'track_id': out['tracks'][0]['id']})
        self.assertEqual(refs[1], {'sequence': 'child', 'clip_id': 'picture', 'track_id': 'V1'})
        self.assertEqual(refs[2], {'source_sequence_id': 'child', 'source_clip_id': 'picture', 'target_sequence_id': out['id'], 'target_clip_id': picture['id']})
        self.assertEqual(refs[3], p['sequences'][0]['workflow']['references'][3])
        self.assertEqual(picture['sequence_id'], 'child'); self.assertEqual(picture['audio_detached_id'], sound['id']); self.assertEqual(sound['unlinked_from'], picture['id'])
        for field in ('source_edit_window', 'rendered_from', 'provenance'): self.assertEqual(picture[field], c[field])
        self.assertEqual(after['sequences'][1], child)

    def test_missing_association_matte_and_later_invalid_target_reject_without_partial_plan(self):
        p, c = fixture(audio_detached_id='missing'); before = copy.deepcopy(p)
        with self.assertRaisesRegex(ValueError, 'detached'): capture(p, 'variants')
        self.assertEqual(p, before)
        c.pop('audio_detached_id'); c['fx_stack'] = [{'type': 'track_matte', 'params': {'track': 'missing'}}]; payload = capture(p, 'variants', hooks=['First', 'Second'])
        before = copy.deepcopy(p)
        with self.assertRaisesRegex(ValueError, 'track-matte'): plan(p, payload)
        self.assertEqual(p, before)
        c.pop('fx_stack')
        with self.assertRaisesRegex(ValueError, 'no longer exists'): capture(p, 'variants', targets=[{'clip_id': 'hook', 'layer': 1}, {'clip_id': 'missing', 'title': True}])

    def test_stale_target_source_and_bad_result_are_refused(self):
        p, _ = fixture(); payload = capture(p, 'variants'); p['sequences'][0]['tracks'][1]['clips'][0]['graphic']['layers'][1]['text'] = 'Changed'
        with self.assertRaisesRegex(ValueError, 'dependency changed'): plan(p, payload)
        p, _ = fixture(); payload = capture(p); p['media']['m']['path'] = '/different.mov'
        with self.assertRaisesRegex(ValueError, 'source dependency'): plan(p, payload)
        with self.assertRaisesRegex(ValueError, 'does not match'): recipes.plan(p, payload, {'version': 1, 'kind': 'recipe', 'mode': 'variants', 'signature': 'captured'})

    def test_explainer_keeps_fresh_transcript_usable_after_adding_silent_graphics(self):
        p, _ = fixture(); seq = p['sequences'][0]; seq['transcript'] = [{'w': 'Speech', 's': 0, 'e': 1}]; seq['transcript_basis'] = transcript_basis(seq, p)
        planned = plan(p, capture(p)); after = apply(p, planned)
        self.assertEqual(words_of(after['sequences'][0], after), seq['transcript'])
        self.assertEqual(after['sequences'][0]['transcript_basis'], transcript_basis(after['sequences'][0], after))

    def test_stale_transcript_data_is_kept_stale_and_unknown_local_reference_refused(self):
        p, c = fixture(); seq = p['sequences'][0]; seq['transcript'] = [{'w': 'Speech', 's': 0, 'e': 1}]; seq['transcript_basis'] = 'stale-source-basis'
        planned = plan(p, capture(p, 'variants')); after = apply(p, planned)
        self.assertEqual(after['sequences'][-1]['transcript'], seq['transcript']); self.assertEqual(after['sequences'][-1]['transcript_basis'], 'stale-source-basis')
        with self.assertRaisesRegex(ValueError, 'changed after transcription'): words_of(after['sequences'][-1], after)
        self.assertTrue(any('stale' in w for w in planned['summary']['warnings']))
        c['custom_peer_id'] = 'hook'; payload = capture(p, 'variants')
        with self.assertRaisesRegex(ValueError, 'Unsupported local reference custom_peer_id'): plan(p, payload)

    def test_missing_late_template_rejects_all_cards_and_source_payload_is_immutable(self):
        p, _ = fixture(); before = copy.deepcopy(p); payload = capture(p, lower_third={'name': 'Ada'}); del payload['templates']['cta']; captured_before = copy.deepcopy(payload)
        with self.assertRaisesRegex(ValueError, 'cta template'): plan(p, payload)
        self.assertEqual(p, before); self.assertEqual(payload, captured_before)

    def test_real_pcm_reverse_ramp_fades_and_manual_automation_are_identical_in_variant(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); source = folder/'signal.wav'; samples = array.array('h', (int(12000*math.sin(i*.031)+2500*math.sin(i*.071)) for i in range(48000*4)))
            with wave.open(str(source), 'wb') as wav: wav.setparams((1, 2, 48000, 0, 'NONE', 'not compressed')); wav.writeframes(samples.tobytes())
            p, c = fixture(in_=.2, out=3.5, reverse=True, time_remap=[{'t': 0, 'v': .8}, {'t': 2, 'v': 2}], audio={'gain_db': -3, 'fade_in': .4, 'fade_out': .2}, keyframes={'audio.gain_db': [{'t': 0, 'v': -8}, {'t': 2, 'v': -2}]})
            p['media']['m'].update(path=str(source), duration=4, has_video=False, channels=1, sample_rate=48000)
            seq = p['sequences'][0]; seq['tracks'][0]['clips'] = []; seq['tracks'][2]['clips'] = [c]
            planned = plan(p, capture(p, 'variants')); after = apply(p, planned); outputs = []
            for sid in ('s', after['sequences'][-1]['id']):
                output = folder/(sid+'.wav')
                with RenderContext() as context:
                    command, _ = build_command(after, sid, str(output), {'format': 'audio', 'audio_codec': 'pcm_f32le'}, context=context)
                    completed = subprocess.run(command, capture_output=True, timeout=30); self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors='replace'))
                raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 'f32le', '-acodec', 'pcm_f32le', '-'], capture_output=True, timeout=10)
                self.assertEqual(raw.returncode, 0); outputs.append(raw.stdout)
            self.assertGreater(len(outputs[0]), 48000*4); self.assertEqual(outputs[0], outputs[1])


if __name__ == '__main__': unittest.main()

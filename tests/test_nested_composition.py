"""Actual FFV1/PNG/float-PCM nesting oracles; no native browser DSP claim."""
import array
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import render


class NestedComposition(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='filmocity-nested-composition-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.serial = 0
        self.sources = {}
        self.media = {}
        self.image('red', Image.new('RGB', (64, 48), (200, 0, 0)))
        self.parent = self.sequence('parent', [self.track('background', 1, [self.clip('background', 'red')]),
                                               self.track('foreground', 2, [])])
        self.child = self.sequence('child', [self.track('child-picture', 1, [])])
        self.project = {'id': 'nested-composition', 'media': self.media, 'sequences': [self.parent, self.child]}
        self.wrapper = self.clip('wrapper', None, sequence_id='child', audio={'linked': False})
        self.parent['tracks'][1]['clips'] = [self.wrapper]
        self.records = []

    @staticmethod
    def clip(id, media_id=None, start=0, duration=3, **kwargs):
        return {'id': id, 'media_id': media_id, 'start': start, 'in_': 0, 'out': duration, **kwargs}

    @staticmethod
    def track(id, index, clips, kind='video', **kwargs):
        return {'id': id, 'kind': kind, 'index': index, 'clips': clips, **kwargs}

    @staticmethod
    def sequence(id, tracks):
        return {'id': id, 'name': id, 'width': 64, 'height': 48, 'fps': 10,
                'duration': 3, 'tracks': tracks, 'master': {}}

    def image(self, id, image):
        path = self.root/(id+'.png'); image.save(path)
        self.sources[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.media[id] = {'id': id, 'name': id, 'path': str(path), 'duration': 3, 'has_video': True,
                          'has_audio': False, 'is_image': True, 'width': 64, 'height': 48}
        return id

    def run_command(self, args):
        result = subprocess.run(args, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:].decode(errors='replace'))
        return result.stdout

    def pixels(self, path):
        return self.run_command(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
                                 '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])

    def pcm(self, path):
        raw = self.run_command(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                               '-f', 'f32le', '-acodec', 'pcm_f32le', '-'])
        result = array.array('f'); result.frombytes(raw)
        if sys.byteorder != 'little': result.byteswap()
        return result

    def render(self, mode='rgb', context=None):
        self.serial += 1
        before = copy.deepcopy(self.project)
        output = self.root/f'result-{self.serial}.mkv'
        own = context is None
        context = context or render.RenderContext(scratch_parent=str(self.root))
        try:
            command, graph = render.build_command(self.project, self.project['sequences'][0]['id'], str(output),
                {'vcodec': 'ffv1', 'acodec': 'pcm_f32le', 'color_processing': mode}, context=context)
            self.run_command(list(command))
            records = []
            for key, record in context.nested_completed.items():
                probe = json.loads(self.run_command(['ffprobe', '-v', 'error', '-show_streams',
                                                     '-of', 'json', record['path']]))
                records.append({'key': list(key), 'path': record['path'], 'streams': probe['streams']})
            self.records.append({'mode': mode, 'output': str(output), 'nested': records})
            (self.root/f'graph-{self.serial}.txt').write_text(graph)
            self.assertEqual(self.project, before, 'Renderer changed saved project')
            return self.pixels(output), output
        finally:
            if own: context.close()

    def pixel(self, data, frame, x, y, expected, tolerance=0):
        self.assertEqual(len(data), 30*64*48*3)
        offset = (frame*64*48+y*64+x)*3
        actual = tuple(data[offset:offset+3])
        self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)), tolerance,
                             (frame, x, y, actual, expected))

    def tearDown(self):
        for path, digest in self.sources.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_transparent_source_and_interior_gap_reveal_parent_on_exact_frame_edges(self):
        image = Image.new('RGBA', (64, 48), (0, 0, 0, 0)); image.paste((0, 200, 0, 255), (16, 12, 48, 36))
        self.image('overlay', image)
        self.child['tracks'][0]['clips'] = [self.clip('a', 'overlay', 0, 1), self.clip('b', 'overlay', 2, 1)]
        data, _ = self.render()
        for n in range(30):
            self.pixel(data, n, 0, 0, (200, 0, 0))
            self.pixel(data, n, 32, 24, (0, 200, 0) if n<10 or n>=20 else (200, 0, 0))

    def test_blank_nested_sequence_remains_transparent_including_first_and_last_frames(self):
        data, _ = self.render()
        self.assertEqual(data, bytes((200, 0, 0))*64*48*30)

    def test_layered_graphics_and_wrapper_transform_preserve_straight_alpha(self):
        graphic = {'layers': [
            {'kind': 'box', 'x': .25, 'y': .25, 'w': .5, 'h': .5, 'color': '#00C800', 'opacity': .5},
            {'kind': 'box', 'x': .5, 'y': .25, 'w': .25, 'h': .5, 'color': '#0000C8', 'opacity': .5}]}
        self.child['tracks'][0]['clips'] = [self.clip('graphic', graphic=graphic)]
        self.wrapper['transform'] = {'scale': .5, 'x': 16, 'opacity': .5}
        data, _ = self.render()
        self.pixel(data, 5, 8, 24, (200, 0, 0))
        self.pixel(data, 5, 42, 24, (150, 50, 0), tolerance=3)
        self.pixel(data, 5, 52, 24, (125, 25, 50), tolerance=3)
        self.pixel(data, 5, 48, 4, (200, 0, 0))

    def test_two_nested_levels_multiply_opacity_without_dark_color_fringes(self):
        self.image('half-green', Image.new('RGBA', (64, 48), (0, 200, 0, 128)))
        self.child['tracks'][0]['clips'] = [self.clip('green', 'half-green')]
        middle = self.sequence('middle', [self.track('middle-picture', 1,
            [self.clip('middle-wrapper', sequence_id='child', transform={'opacity': .5}, audio={'linked': False})])])
        self.project['sequences'].append(middle)
        self.wrapper.update(sequence_id='middle', transform={'opacity': .5})
        data, _ = self.render()
        for n in (0, 5, 29): self.pixel(data, n, 32, 24, (175, 25, 0), tolerance=3)

    def test_nested_matte_preserves_underlying_parent_and_consumer_opacity(self):
        self.image('green', Image.new('RGB', (64, 48), (0, 200, 0)))
        consumer = self.clip('green', 'green', fx_stack=[{'type': 'track_matte', 'params': {'track': 'mask', 'type': 'alpha'}}])
        mask = self.clip('mask', graphic={'layers': [{'kind': 'box', 'x': 0, 'y': 0, 'w': .5, 'h': 1, 'color': 'white'}]})
        self.child['tracks'][0]['clips'] = [consumer]
        self.child['tracks'].append(self.track('mask', 2, [mask], muted=True))
        self.wrapper['transform'] = {'opacity': .5}
        data, _ = self.render()
        self.pixel(data, 5, 16, 24, (100, 100, 0), tolerance=2)
        self.pixel(data, 5, 48, 24, (200, 0, 0))

    def test_rgb_and_explicit_legacy_cache_entries_preserve_distinct_output_policies(self):
        with render.RenderContext(scratch_parent=str(self.root)) as context:
            rgb, _ = self.render(context=context)
            legacy, _ = self.render('legacy', context=context)
            self.pixel(rgb, 5, 32, 24, (200, 0, 0))
            self.pixel(legacy, 5, 32, 24, (0, 0, 0), tolerance=2)
            self.assertEqual(len(context.nested_completed), 2)
            self.assertEqual({(row['key'][3], row['key'][4]) for row in self.records[-1]['nested']},
                             {('rgb', True), ('legacy', False)})
            formats = {row['key'][3]: next(s['pix_fmt'] for s in row['streams'] if s['codec_type']=='video')
                       for row in self.records[-1]['nested']}
            self.assertEqual(formats, {'rgb': 'bgra', 'legacy': 'yuv420p'})

    def source_sequence(self, sar='2/1', rotation=0):
        import media_metadata
        import sequence_creation
        tag = sar.replace('/', '-')+'-'+str(rotation)
        picture = self.root/('bands-'+tag+'.png')
        image = Image.new('RGB', (64, 48), (200, 0, 0)); image.paste((0, 200, 0), (32, 0, 64, 48)); image.save(picture)
        base = self.root/('bands-'+tag+'-base.mov')
        self.run_command(['ffmpeg', '-v', 'error', '-y', '-loop', '1', '-framerate', '10', '-i', str(picture),
            '-t', '1', '-vf', 'setsar='+sar, '-c:v', 'png', '-pix_fmt', 'rgb24', str(base)])
        source = base
        if rotation:
            source = self.root/('bands-'+tag+'.mov')
            self.run_command(['ffmpeg', '-v', 'error', '-y', '-display_rotation:v:0', str(rotation),
                              '-i', str(base), '-c', 'copy', str(source)])
        probe = json.loads(self.run_command(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(source)]))
        media = media_metadata.summarize(probe); media.update(id='source', name='Anamorphic', path=str(source))
        self.assertEqual(media['rotation'], rotation)
        self.sources[source] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.project = {'id': 'aspect', 'media': {'source': media}, 'sequences': [self.sequence('template', [])]}
        plan = sequence_creation.plan(self.project, {'_context': {'workspace': 'w', 'project': 'p', 'revision': 'r'},
            'mode': 'source', 'sequence': 'template', 'media_id': 'source'}, identity='a'*32)
        self.project['sequences'] = [plan['sequence']]
        self.parent = plan['sequence']
        return plan

    @staticmethod
    def at(data, width, frame, x, y, height):
        offset = (frame*width*height+y*width+x)*3
        return tuple(data[offset:offset+3])

    def test_source_matched_sequences_fit_decoded_sar_and_rotation_without_bars(self):
        for sar, rotation, expected in [('2/1', 0, (128, 48)), ('2/1', 90, (48, 128)),
                                        ('1/2', 0, (32, 48)), ('1/2', 90, (48, 32)),
                                        ('1/1', 0, (64, 48)), ('1/1', 90, (48, 64))]:
            with self.subTest(sar=sar, rotation=rotation):
                plan = self.source_sequence(sar, rotation)
                width, height = self.parent['width'], self.parent['height']
                self.assertEqual((width, height), expected)
                data, _ = self.render()
                self.assertEqual(len(data), width*height*3*10)
                points = [(width//4, height//4), (width*3//4, height*3//4)]
                colors = [(0, 200, 0), (200, 0, 0)] if rotation else [(200, 0, 0), (0, 200, 0)]
                for point, color in zip(points, colors):
                    actual = self.at(data, width, 5, *point, height)
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(actual, color)), 3, (actual, color))
                # Far corners remain filled, not the old coded-aspect pillarbox.
                for x,y in [(1,1),(width-2,height-2)]:
                    self.assertGreater(max(self.at(data, width, 5, x, y, height)), 190)

    def test_non_square_source_contain_cover_and_blur_fill_use_display_geometry(self):
        self.source_sequence()
        self.parent.update(width=64, height=64)
        clip = self.parent['tracks'][1]['clips'][0]
        for fit in ('contain', 'cover', 'blur_fill'):
            with self.subTest(fit=fit):
                clip['fit'] = fit
                data, _ = self.render()
                self.assertEqual(len(data), 64*64*3*10)
                for x,color in [(8,(200,0,0)),(56,(0,200,0))]:
                    actual = self.at(data, 64, 5, x, 32, 64)
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,color)), 4)
                top = self.at(data, 64, 5, 8, 4, 64)
                if fit == 'contain': self.assertEqual(top, (0,0,0))
                else: self.assertGreater(max(top), 50)

    def audio_source(self):
        source = self.root/'quarter.wav'
        with wave.open(str(source), 'wb') as stream:
            stream.setparams((2, 2, 48000, 0, 'NONE', '')); stream.writeframes((8192).to_bytes(2, 'little')*2*3*48000)
        self.sources[source] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.media['audio'] = {'id': 'audio', 'name': 'audio', 'path': str(source), 'duration': 3,
                               'has_audio': True, 'has_video': False, 'channels': 2, 'sample_rate': 48000}
        return source

    def plan(self, selected):
        import sequence_nesting
        before = copy.deepcopy(self.project)
        result = sequence_nesting.plan(self.project, {'_context': {'project': 'audit', 'workspace': 'audit', 'revision': 'r'},
            'sequence': 'parent', 'clip_ids': selected, 'name': 'Sound and picture'})
        self.assertEqual(self.project, before)
        return result

    def apply_plan(self, result):
        self.assertTrue(result['ok'], result['issues'])
        self.assertEqual([op['op'] for op in result['ops']], ['set', 'insert'])
        for op in result['ops']:
            self.assertTrue(op['path'].startswith('/sequences/'))
            index = int(op['path'].split('/')[-1])
            if op['op'] == 'set': self.project['sequences'][index] = copy.deepcopy(op['value'])
            else: self.project['sequences'].insert(index, copy.deepcopy(op['value']))

    def test_planned_picture_fragments_keep_unselected_gap_after_later_normalization(self):
        from overlap_normalization import normalize_tracks
        overlay = Image.new('RGBA', (64, 48), (0,0,0,0)); overlay.paste((0,200,0,255), (16,12,48,36))
        self.image('overlay', overlay); self.image('blue', Image.new('RGB', (64,48), (0,0,200)))
        first = self.clip('first', 'overlay', 0, 1); gap = self.clip('gap', 'blue', 1, 1); last = self.clip('last', 'overlay', 2, 1)
        self.parent['tracks'][1]['clips'] = [first, gap, last]
        result = self.plan(['first','last']); self.apply_plan(result)
        normalize_tracks(self.project)
        main = self.project['sequences'][0]
        self.assertEqual(next(c for t in main['tracks'] for c in t['clips'] if c['id']=='gap'), gap)
        data, _ = self.render()
        self.pixel(data, 5, 0, 0, (200,0,0)); self.pixel(data, 5, 32, 24, (0,200,0))
        self.pixel(data, 15, 0, 0, (0,0,200)); self.pixel(data, 15, 32, 24, (0,0,200))
        self.pixel(data, 25, 0, 0, (200,0,0)); self.pixel(data, 25, 32, 24, (0,200,0))

    def test_planned_fractional_origin_keeps_numbered_native_frame_identity_and_tail(self):
        rate = 30000/1001
        raw = self.root/'numbered.rgb'
        raw.write_bytes(b''.join(bytes([40+10*n])*64*48*3 for n in range(12)))
        movie = self.root/'numbered.mkv'
        self.run_command(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24',
            '-video_size','64x48','-framerate','30000/1001','-i',str(raw),'-c:v','ffv1','-pix_fmt','bgr0',str(movie)])
        self.media['numbered'] = {'id':'numbered','name':'Numbered','path':str(movie),'duration':12/rate,
            'width':64,'height':48,'fps':rate,'frame_rate':'30000/1001','has_video':True,'has_audio':False}
        self.sources[movie] = hashlib.sha256(movie.read_bytes()).hexdigest()
        self.parent.update(fps=rate,duration=.3)
        self.parent['tracks'][0]['clips'][0]['out'] = .3
        self.parent['tracks'][1]['clips'] = [self.clip('numbered','numbered',.0501,.12)]
        before, _ = self.render()
        result = self.plan(['numbered']); self.apply_plan(result)
        after, _ = self.render()
        self.assertEqual(len(before),9*64*48*3)
        # The first visible parent tick is ceil(.0501*30000/1001)=2.
        # The four retained source pictures are independently encoded as
        # 40,50,60,70; source frame3 must survive at parent tick5.
        expected = [200,200,40,50,60,70,200,200,200]
        self.assertEqual([before[n*64*48*3] for n in range(9)], expected)
        self.assertEqual([after[n*64*48*3] for n in range(9)], expected)
        self.assertEqual(after, before)

    def test_planned_nest_keeps_track_gain_once_through_neutral_wrapper(self):
        self.audio_source()
        self.parent['tracks'][1]['clips'] = []
        self.parent['tracks'].append(self.track('sound', 1, [self.clip('sound', 'audio')], kind='audio', gain_db=20*math.log10(.5)))
        self.apply_plan(self.plan(['sound']))
        _, output = self.render()
        samples = self.pcm(output)
        self.assertEqual(len(samples), 3*48000*2)
        self.assertTrue(all(abs(v-.125)<1e-7 for v in samples[48000:96000]))

    def compressed_selection(self):
        source = self.audio_source()
        movie = self.root/'peer.mkv'
        self.run_command(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=64x48:r=10:d=3',
                          '-i', str(source), '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(movie)])
        self.media['peer'] = {**self.media['audio'], 'id': 'peer', 'name': 'peer', 'path': str(movie),
                              'has_video': True, 'width': 64, 'height': 48, 'fps': 10, 'frame_rate': '10/1'}
        self.sources[movie] = hashlib.sha256(movie.read_bytes()).hexdigest()
        self.parent['tracks'][1]['clips'] = [self.clip('peer', 'peer')]
        self.parent['tracks'].append(self.track('sound', 1, [self.clip('sound', 'audio')], kind='audio',
            audio_fx={'comp': {'enabled': True, 'threshold_db': -18, 'ratio': 4, 'attack_ms': 1, 'release_ms': 20, 'makeup_db': 0}}))
        return source

    def test_planned_complete_compressor_bus_matches_independent_combined_signal(self):
        source = self.compressed_selection()
        self.apply_plan(self.plan(['sound', 'peer']))
        _, output = self.render()
        samples = self.pcm(output)
        oracle = self.root/'independent-combined.wav'
        # Two independent quarter-amplitude contributors sum to one half before
        # the shared compressor. No Filmocity renderer/helper constructs this graph.
        threshold = 10**(-18/20)
        self.run_command(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-af',
                          f'volume=2,aformat=sample_fmts=dblp,acompressor=threshold={threshold:.12g}:ratio=4:attack=1:release=20:makeup=1',
                          '-c:a', 'pcm_f32le', str(oracle)])
        expected = self.pcm(oracle)
        self.assertEqual(len(samples), len(expected))
        self.assertLess(max(abs(x-y) for x,y in zip(samples[96000:], expected[96000:])), 1e-7)
        self.assertAlmostEqual(sum(samples[96000:192000])/96000, .1777225285768509, delta=1e-7)

    def test_planned_partial_compressor_bus_refuses_without_operations(self):
        self.compressed_selection()
        result = self.plan(['sound'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['ops'], [])
        self.assertIn('audio_bus_boundary', [issue['code'] for issue in result['issues']])

    def test_rgb_alpha_intermediate_preserves_float_audio_headroom(self):
        source = self.root/'headroom.wav'
        with wave.open(str(source), 'wb') as stream:
            stream.setparams((2, 2, 48000, 0, 'NONE', '')); stream.writeframes((8192).to_bytes(2, 'little')*2*3*48000)
        self.sources[source] = hashlib.sha256(source.read_bytes()).hexdigest()
        self.media['audio'] = {'id': 'audio', 'name': 'audio', 'path': str(source), 'duration': 3,
                               'has_audio': True, 'has_video': False, 'channels': 2, 'sample_rate': 48000}
        self.child['tracks'].append(self.track('sound', 1, [self.clip('sound', 'audio')], kind='audio', gain_db=24))
        self.wrapper['audio'] = {'linked': True, 'gain_db': -24}
        with render.RenderContext(scratch_parent=str(self.root)) as context:
            _, output = self.render(context=context)
            final = self.pcm(output)
            child_path = next(iter(context.nested_completed.values()))['path']
            child = self.pcm(child_path)
            self.assertEqual(len(final), 3*48000*2)
            self.assertGreater(max(child), 3.9)
            self.assertAlmostEqual(final[48000], .25, delta=1e-7)
            codecs = {s['codec_type']: s['codec_name'] for s in self.records[-1]['nested'][0]['streams']}
            self.assertEqual(codecs, {'video': 'ffv1', 'audio': 'pcm_f32le'})


if __name__ == '__main__': unittest.main()

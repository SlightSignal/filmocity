"""Project argv admission and real numbered-source fidelity in disposable files."""
import copy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from input_options import validated_input_options, numbered_input_options
import project_recovery
import project_package
import preflight
import render
import audio_measurement
import media_preparation
import proxy_media
from frame_source import source_project
from render_context import RenderContext
from task_inputs import source_stamp


class SourceInputOptions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity inputs É's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.wav'
        with wave.open(str(self.source), 'wb') as output:
            output.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
            output.writeframes(b'\0\0' * 4800)
        self.sentinel = self.root / 'unrequested-output.wav'
        self.sentinel.write_bytes(b'previous file must remain unchanged')
        # Exact argument shape from the preserved real extra-output overwrite.
        self.attack = ['-f', 'lavfi', '-i', 'sine=frequency=880:duration=0.1',
                       '-f', 'wav', str(self.sentinel)]
        self.project = {'version': 3, 'media': {'m': {'id': 'm', 'path': str(self.source),
            'has_video': False, 'has_audio': True, 'channels': 1, 'sample_rate': 48000,
            'duration': .1, 'width': 64, 'height': 48, 'fps': 24}}, 'sequences': [
            {'id': 's', 'name': 'Inputs', 'width': 64, 'height': 48, 'fps': 24,
             'duration': .1, 'tracks': [{'id': 'A1', 'kind': 'audio', 'index': 1,
                'clips': [{'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': .1}]}]}]}

    def attacked(self):
        result = copy.deepcopy(self.project)
        result['media']['m']['input_opts'] = list(self.attack)
        return result

    def unchanged(self):
        self.assertEqual(self.sentinel.read_bytes(), b'previous file must remain unchanged')

    def test_supported_options_preserve_order_clock_and_return_fresh_argv(self):
        for options in (None, [], (), ['-start_number', '7', '-framerate', '24000/1001'],
                        ['-framerate', '23.976', '-loop', '1'], ['-start_number', '000007'],
                        ['-loop', '0'], ['-framerate', '1000']):
            with self.subTest(options=options):
                result = validated_input_options(options)
                self.assertEqual(result, list(options or []))
                if options is not None: self.assertIsNot(result, options)

    def test_numbered_producer_rejects_before_save_and_preserves_small_or_precise_clock(self):
        for rate, start in ((24,7),(1000,2147483647),(1e-5,0),(23.976023976023978,7)):
            with self.subTest(rate=rate):
                options = numbered_input_options(rate,start)
                self.assertNotIn('e',options[1])
                self.assertEqual(Fraction(options[1]),Fraction(str(rate)))
                self.assertEqual(options[3],str(start))
                self.assertEqual(validated_input_options(options),options)
        for rate, start in ((True,0),(0,0),(-1,0),(1001,0),(float('nan'),0),
                           (float('inf'),0),(24,-1),(24,2147483648),(24,True)):
            with self.subTest(rate=rate,start=start),self.assertRaises(ValueError):
                numbered_input_options(rate,start)

    def test_commands_files_protocols_and_pacing_are_not_ingest_options(self):
        for options in (self.attack, ['-i', 'external.wav'], ['-f', 'lavfi'],
                ['-y', '1'], ['-filter_complex', 'movie=external'], ['-map', '0'],
                ['-report', '1'], ['-dump_attachment', str(self.sentinel)],
                ['-protocol_whitelist', 'file,http'], ['-attach', str(self.sentinel)],
                ['-ss', '1'], ['-re'], ['-stream_loop', '-1'], ['-framerate=24'],
                ['-framerate', '24', str(self.sentinel), '1']):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validated_input_options(options)

    def test_malformed_duplicate_nonstring_and_out_of_range_values_refuse(self):
        for options in ('-framerate 24', {}, 0, False, ['-framerate'],
                ['-framerate', 24], ['-start_number', True], ['-loop', '2'],
                ['-start_number', '-1'], ['-start_number', '1.5'], ['-start_number', '2147483648'],
                ['-framerate', '0'], ['-framerate', '-24'], ['-framerate', '1001'],
                ['-framerate', '1/0'], ['-framerate', '0/1'], ['-framerate', 'nan'],
                ['-framerate', '1e2'], ['-framerate', '24 -i elsewhere'],
                ['-framerate', '24\0'], ['-framerate', '２４'], ['-framerate', '9'*65],
                ['-framerate', '24', '-framerate', '25']):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validated_input_options(options)

    def test_saved_project_rejects_unsafe_unused_and_parent_media_without_mutation(self):
        for options in (self.attack, False, ['-framerate', '1/0']):
            project = copy.deepcopy(self.project)
            project['media']['unused'] = {'path': str(self.source), 'input_opts': options}
            raw = json.dumps(project).encode()
            with self.subTest(options=options), self.assertRaisesRegex(project_recovery.RecoveryError, 'input options'):
                project_recovery.parse_project(raw)
            self.assertEqual(json.loads(raw), project)

    def test_preflight_rejects_before_any_tool_capability_inspection(self):
        project = self.attacked()
        with patch.object(render, 'has_filter') as capability:
            report = preflight.inspect_resources(project, 's')
        self.assertFalse(report['ok'])
        self.assertEqual([issue['code'] for issue in report['issues']], ['invalid_input_options'])
        self.assertEqual(report['issues'][0]['media_id'], 'm')
        capability.assert_not_called(); self.unchanged()

    def test_export_audio_frame_and_incremental_refuse_without_spawning_or_overwriting(self):
        with patch.object(render.subprocess, 'Popen') as spawn:
            functions = [
                lambda: render.build_command(self.attacked(), 's', self.root/'export.mp4'),
                lambda: render.build_command(self.attacked(), 's', self.root/'export.wav', {'format':'audio','acodec':'wav'}),
                lambda: render.render_frame(self.attacked(), 's', 0, str(self.root/'frame.png')),
                lambda: render.render_incremental(self.attacked(), 's', str(self.root/'cached.mp4'), cache_dir=str(self.root/'cache'))]
            for function in functions:
                with self.subTest(function=function), self.assertRaises(preflight.ResourceError): function()
            spawn.assert_not_called()
        self.unchanged()
        self.assertFalse(any((self.root/name).exists() for name in ('export.mp4','export.wav','frame.png','cached.mp4','cache')))

    def test_nested_and_subclip_parent_options_cannot_escape_outer_preflight(self):
        project = self.attacked()
        child = copy.deepcopy(project['sequences'][0]); child['id'] = 'child'
        project['sequences'].append(child)
        project['sequences'][0]['tracks'][0]['clips'] = [{'id':'nested','sequence_id':'child','start':0,'in_':0,'out':.1}]
        with patch.object(render.subprocess, 'Popen') as spawn, self.assertRaises(preflight.ResourceError):
            render.build_command(project, 's', self.root/'nested.wav', {'format':'audio'})
        spawn.assert_not_called(); self.unchanged()
        project = self.attacked(); project['media']['sub'] = {'id':'sub','subclip_of':'m','sub_in':0}
        project['sequences'][0]['tracks'][0]['clips'][0]['media_id'] = 'sub'
        with self.assertRaises(preflight.ResourceError): render.build_command(project, 's', self.root/'sub.wav', {'format':'audio'})

    def test_source_monitor_frame_uses_same_admission(self):
        project = self.attacked(); project['media']['m']['has_video'] = True
        snapshot = source_project(project, 'm')
        with patch.object(render.subprocess, 'Popen') as spawn, self.assertRaises(preflight.ResourceError):
            render.render_frame(snapshot, 'source', 0, str(self.root/'source.png'))
        spawn.assert_not_called(); self.unchanged()

    def test_audio_measurement_cannot_inherit_unsafe_project_options(self):
        with RenderContext(scratch_parent=str(self.root)) as context:
            with patch.object(render.subprocess, 'Popen') as spawn, self.assertRaises(preflight.ResourceError):
                audio_measurement._pcm({'duration':.1,'project':self.attacked(),'sequence':'s'}, context)
            spawn.assert_not_called()
        self.unchanged()

    def test_derivative_worker_refuses_before_directories_updates_or_processes(self):
        payload = {'path':str(self.source),'info':{'input_opts':self.attack}}
        with patch.object(media_preparation, '_run_ffmpeg') as encode:
            with self.assertRaises(ValueError):
                media_preparation._prepare(self.root, payload, None, None, None,
                    ffmpeg='ffmpeg', ffprobe='ffprobe', proxy_encoder='libx264')
            encode.assert_not_called()
        self.assertFalse((self.root/'thumbs').exists()); self.assertFalse((self.root/'proxies').exists())
        self.unchanged()

    def test_proxy_metadata_and_frame_probes_refuse_before_process_or_owned_files(self):
        for frames in (False, True):
            with self.subTest(frames=frames), RenderContext(scratch_parent=str(self.root)) as context:
                with patch.object(proxy_media.subprocess, 'Popen') as spawn, self.assertRaises(ValueError):
                    proxy_media.inspect_file(self.source, context, input_opts=self.attack, frames=frames)
                spawn.assert_not_called()
        self.unchanged()

    def test_portable_package_uses_same_value_and_flag_contract(self):
        for options in (self.attack, ['-start_number','-1'], ['-framerate','1/0'], ['-loop','2'], False):
            with self.subTest(options=options), self.assertRaises(project_package.PackageError):
                project_package.validate_options({'input_opts':options})
        project_package.validate_options({'input_opts':['-start_number','7','-framerate','24000/1001']})

    def test_source_stamp_admits_options_before_filesystem_analysis_capture(self):
        with patch.object(preflight.os, 'stat') as stat, self.assertRaises(ValueError):
            source_stamp({'path':str(self.source),'input_opts':self.attack})
        stat.assert_not_called()

    def numbered(self, rate='24'):
        from PIL import Image
        for index, color in enumerate(('red', 'green', 'blue'), 7):
            Image.new('RGB', (64,48), color).save(self.root/f'frame{index:04d}.png')
        rate_number = Fraction(rate); duration = float(3/rate_number)
        media = {'id':'m','path':str(self.root/'frame%04d.png'),'has_video':True,'has_audio':False,
                 'width':64,'height':48,'fps':float(rate_number),'duration':duration,'sequence_frames':3,
                 'input_opts':['-framerate',rate,'-start_number','7']}
        project = copy.deepcopy(self.project); project['media'] = {'m':media}
        sequence = project['sequences'][0]; sequence.update(fps=float(rate_number),duration=duration)
        sequence['tracks'][0].update(id='V1',kind='video')
        sequence['tracks'][0]['clips'][0]['out'] = duration
        return project

    def require_tools(self):
        for tool in ('ffmpeg','ffprobe'):
            if not shutil.which(tool): self.fail(f'Existing {tool} is required; cannot qualify numbered input fidelity')

    def test_real_numbered_export_keeps_start_frame_order_and_complete_duration(self):
        self.require_tools(); project = self.numbered()
        original = {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.glob('frame*.png')}
        output = self.root/'numbered.mp4'
        render.render(project, 's', str(output), {'x264_preset':'ultrafast'})
        decoded = subprocess.run(['ffmpeg','-v','error','-i',str(output),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'], capture_output=True,check=True).stdout
        stride = 64*48*3; self.assertEqual(len(decoded),stride*3)
        for index, channel in enumerate((0,1,2)):
            pixel = decoded[index*stride:index*stride+3]
            self.assertGreater(pixel[channel], 100)
            self.assertGreater(pixel[channel], max(pixel[(channel+1)%3],pixel[(channel+2)%3])+80)
        self.assertEqual(original, {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in self.root.glob('frame*.png')})

    def test_real_fractional_numbered_probe_keeps_numeric_options_and_native_clock(self):
        self.require_tools(); project = self.numbered('24000/1001'); media = project['media']['m']
        self.assertEqual(preflight.media_files(media), [str(self.root/f'frame{i:04d}.png') for i in range(7,10)])
        with RenderContext(scratch_parent=str(self.root)) as context:
            info = proxy_media.inspect_file(media['path'], context, input_opts=media['input_opts'])
            frames = proxy_media.inspect_file(media['path'], context, input_opts=media['input_opts'], frames=True)
            stamps = list(proxy_media.timestamps(frames))
        video = next(stream for stream in info['streams'] if stream['codec_type']=='video')
        self.assertEqual(video['r_frame_rate'],'24000/1001'); self.assertEqual(len(stamps),3)
        for index, stamp in enumerate(stamps): self.assertAlmostEqual(stamp,index*1001/24000,places=6)
        raw = json.dumps(project).encode(); self.assertEqual(project_recovery.parse_project(raw),project)


if __name__ == '__main__': unittest.main(verbosity=2)

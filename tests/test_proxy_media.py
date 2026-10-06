"""Real H.264 timing qualification plus proxy publication/lifecycle failures.

Linux uses explicitly selected OpenH264 when x264 is not installed. Windows must
qualify the production x264 profile independently; no silent codec substitution.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import proxy_media as proxy
import media_preparation
from render_context import RenderContext
from frame_source import source_project
from background_tasks import TaskManager, MediaTaskBusy
from task_inputs import source_stamp
from project_sync import workspace_id
from test_background_tasks import wait_for


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=30)
    if result.returncode: raise AssertionError(result.stderr.decode(errors='replace'))
    return result.stdout


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


class Proxies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        listed = run(['ffmpeg', '-hide_banner', '-encoders']).decode()
        cls.encoder = 'libx264' if 'libx264 ' in listed else 'libopenh264'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Filmocity proxy É '); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, rate='30000/1001', *, vfr=False, offset=0, audio=0, size='160x96', sar=None):
        path = self.root / ('source-' + str(len(list(self.root.iterdir()))) + '.mov')
        args = ['ffmpeg', '-v', 'error', '-y', *(['-itsoffset', str(abs(audio))] if audio < 0 else []), '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate={rate}:duration=1']
        if audio: args += [*(['-itsoffset', str(audio)] if audio > 0 else []), '-f', 'lavfi', '-i', 'sine=frequency=1000:sample_rate=48000:duration=0.5']
        filters = []
        if vfr: filters += ["select='not(mod(n,3))+not(mod(n,5))'"]
        if sar: filters += ['setsar=' + sar]
        if filters: args += ['-vf', ','.join(filters)]
        args += ['-fps_mode:v', 'passthrough', '-c:v', 'png', '-threads', '1', '-c:a', 'pcm_s16le']
        if offset: args += ['-output_ts_offset', str(offset)]
        run([*args, '-metadata', 'title=日本語 É', str(path)]); return path

    def encode(self, path, policy=None):
        info = proxy.summarize(probe(path)); output = self.root / 'proxy.mp4'
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        run(['ffmpeg', '-v', 'error', '-y', '-copyts', '-start_at_zero', '-i', str(path),
             *proxy.options(info, policy, encoder=self.encoder), '-threads', '1', str(output)])
        with RenderContext(scratch_parent=str(self.root)) as owned:
            checked = proxy.check_proxy(probe(path), probe(output), proxy.inspect_file(path, owned, frames=True),
                proxy.inspect_file(output, owned, frames=True), info, policy)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))
        return checked

    def test_integer_and_fractional_sources_keep_every_picture_timestamp(self):
        for rate in ('24', '25', '24000/1001', '30000/1001', '60000/1001'):
            with self.subTest(rate=rate):
                checked = self.encode(self.source(rate))
                self.assertLess(checked['max_timestamp_error_seconds'], .000002)
                self.assertEqual(checked['frames'], round(float(__import__('fractions').Fraction(rate))))

    def test_irregular_source_is_not_regularized_to_thirty_fps(self):
        checked = self.encode(self.source(vfr=True))
        self.assertEqual(checked['frames'], 14)
        self.assertLess(checked['max_timestamp_error_seconds'], .000002)

    def test_nonzero_container_origin_is_shifted_once(self):
        checked = self.encode(self.source(offset=5, audio=.2))
        self.assertLess(checked['audio_start_error_seconds'], 1024/48000+.002)

    def test_delayed_audio_keeps_its_offset_and_sample_rate(self):
        checked = self.encode(self.source(audio=.25))
        self.assertEqual(checked['sample_rate'], 48000)
        self.assertLess(checked['audio_start_error_seconds'], 1024/48000+.002)

    def test_audio_leading_video_and_delayed_audio_keep_decoded_tone_position(self):
        import array
        for offset in (-.25,.25):
            with self.subTest(offset=offset):
                path=self.source(audio=offset);self.encode(path)
                starts=[]
                for f in (path,self.root/'proxy.mp4'):
                    d=probe(f);a=next(s for s in d['streams'] if s['codec_type']=='audio')
                    samples=array.array('f',run(['ffmpeg','-v','error','-i',str(f),'-map','0:a:0','-f','f32le','-']))
                    audible=next(i for i,v in enumerate(samples) if abs(v)>.01)
                    starts.append(float(a['start_time'])-float(d['format']['start_time'])+audible/48000)
                self.assertLess(abs(starts[0]-starts[1]),.001)

    def test_portrait_and_anamorphic_inputs_fit_the_long_edge_without_stretching(self):
        for size, sar, expected in [('96x160', None, (96,160)), ('160x96','2/1',(320,96)), ('960x540',None,(640,360))]:
            with self.subTest(size=size,sar=sar):
                checked = self.encode(self.source(size=size,sar=sar), {'max_edge':640})
                self.assertEqual((checked['width'],checked['height']), expected)

    def test_display_rotation_is_applied_once_in_proxy_and_source_still(self):
        source = self.source(); rotated = self.root/'rotated.mov'
        run(['ffmpeg','-v','error','-y','-display_rotation:v:0','90','-i',str(source),'-c','copy',str(rotated)])
        info = proxy.summarize(probe(rotated));self.assertEqual((info['width'],info['height']),(96,160))
        project = source_project({'media':{'m':{**info,'path':str(rotated)}}}, 'm')
        self.assertEqual((project['sequences'][0]['width'],project['sequences'][0]['height']),(96,160))
        checked = self.encode(rotated);self.assertEqual((checked['width'],checked['height']),(96,160))
        from render import render_frame
        from PIL import Image
        still=self.root/'source-still.png';render_frame(project,'source',0,str(still))
        with Image.open(still) as image:self.assertEqual(image.size,(96,160))

    def test_malformed_settings_hdr_and_unqualified_encoder_fail_explicitly(self):
        for policy in ({'max_edge':720},{'quality':'unknown'},[],{'max_edge':True},{'encoder':'anything'}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):proxy.settings(policy)
        with self.assertRaisesRegex(ValueError,'HDR'):proxy.options({'hdr':True})
        with self.assertRaisesRegex(ValueError,'encoder'):proxy.options({'width':160,'height':96},encoder='mpeg4')

    def test_wrong_count_timing_or_audio_is_not_accepted(self):
        path=self.source(audio=.25);self.encode(path);src=probe(path);out=probe(self.root/'proxy.mp4');info=proxy.summarize(src)
        with RenderContext(scratch_parent=str(self.root)) as owned:
            source_frames=proxy.inspect_file(path,owned,frames=True);output_frames=proxy.inspect_file(self.root/'proxy.mp4',owned,frames=True)
            bad=owned.write_text('0\n', '.txt')
            with self.assertRaisesRegex(ValueError,'count'):proxy.check_proxy(src,out,source_frames,bad,info,None)
            bad=owned.write_text(Path(output_frames).read_text().replace('0.000000','0.020000',1),'.txt')
            with self.assertRaisesRegex(ValueError,'timestamps'):proxy.check_proxy(src,out,source_frames,bad,info,None)
            broken=copy.deepcopy(out);next(s for s in broken['streams'] if s['codec_type']=='audio')['start_time']='0.5'
            with self.assertRaisesRegex(ValueError,'sync'):proxy.check_proxy(src,broken,source_frames,output_frames,info,None)
            for duration in ('0.450000','0.580000'):
                broken=copy.deepcopy(out);next(s for s in broken['streams'] if s['codec_type']=='audio')['duration']=duration
                with self.subTest(duration=duration),self.assertRaisesRegex(ValueError,'duration'):
                    proxy.check_proxy(src,broken,source_frames,output_frames,info,None)

    def test_cancelled_inspection_does_not_launch_or_leave_scratch(self):
        with RenderContext(scratch_parent=str(self.root)) as owned:
            owned.holder['cancelled']=True
            with patch.object(subprocess,'Popen') as popen,self.assertRaisesRegex(RuntimeError,'cancelled'):
                proxy.inspect_file('unused',owned)
            popen.assert_not_called()
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_running_inspection_cancellation_reaps_its_child_and_retires_files(self):
        import threading
        created=[];ready=threading.Event();original=subprocess.Popen
        def launch(*args,**kwargs):
            # The venv python.exe shim spawns a second process on Windows.
            # Use a real paced FFmpeg leaf so cancellation tests its owned child.
            child=original(['ffmpeg','-nostdin','-v','error','-re','-f','lavfi','-i','anullsrc=r=8000:cl=mono','-t','60','-f','null','-'],**kwargs)
            created.append(child);ready.set();return child
        with RenderContext(scratch_parent=str(self.root)) as owned:
            def cancel():
                if ready.wait(3):owned.holder['cancelled']=True
            worker=threading.Thread(target=cancel);worker.start()
            with patch.object(subprocess,'Popen',side_effect=launch),self.assertRaisesRegex(RuntimeError,'cancelled'):
                proxy.inspect_file('source',owned)
            worker.join(3);self.assertFalse(worker.is_alive());self.assertIsNotNone(created[0].poll());self.assertNotIn('proc',owned.holder)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))
        self.assertNotIn('scratch_diagnostics',owned.holder)

    def test_additional_audio_streams_are_reported_and_first_stream_is_selected(self):
        source=self.source(audio=.25);multi=self.root/'two-audios.mov'
        run(['ffmpeg','-v','error','-y','-i',str(source),'-f','lavfi','-i','anullsrc=r=44100:cl=stereo','-t','1',
             '-map','0','-map','1:a','-c:v','copy','-c:a','pcm_s16le',str(multi)])
        checked=self.encode(multi);self.assertEqual(checked['audio_streams_omitted'],1)
        self.assertEqual(checked['sample_rate'],48000);self.assertEqual(checked['channels'],1)

    def preparation(self, *, update=None, encoder=None):
        path=self.source();info=proxy.summarize(probe(path));updates=[]
        def publish(payload,fields):
            if update:return update(payload,fields)
            updates.append(fields);return True
        payload={'media_id':'m','path':str(path),'info':info,'token':'source','stamp':source_stamp({'path':str(path)}),'proxy_settings':{'max_edge':640,'quality':'draft'}}
        manager=TaskManager(self.root, {'media':lambda p,c:media_preparation.prepare(self.root,p,c,lambda p:True,publish,proxy_encoder=encoder or self.encoder)})
        self.addCleanup(manager.shutdown)
        task=manager.submit('media','Clip',{'workspace':workspace_id(self.root),'project':'p'},payload)
        result=wait_for(manager,task['id'],timeout=15)
        return result,updates

    def test_preparation_publishes_only_checked_proxy_and_retains_policy(self):
        result,updates=self.preparation();self.assertEqual(result['record']['status'],'done',result)
        ready=updates[-1];self.assertEqual(ready['proxy_status'],'ready');self.assertEqual(ready['proxy_info']['frames'],30)
        self.assertEqual(ready['proxy_info']['policy'],{'max_edge':640,'quality':'draft'})
        self.assertTrue((self.root/ready['proxy'].lstrip('/')).is_file())
        from media_preview import describe
        source=next(self.root.glob('source-*.mov'))
        self.assertEqual(describe(self.root,{'media':{'m':{'path':str(source),**ready}}},'m')['proxy_state'],'ready')

    def test_failed_proxy_keeps_successful_thumbnail_and_reports_proxy_failure(self):
        result,updates=self.preparation(encoder='unavailable');self.assertEqual(result['record']['status'],'error')
        self.assertEqual(updates[-1]['status'],'ready');self.assertEqual(updates[-1]['proxy_status'],'error')
        self.assertFalse(any('proxy' in row for row in updates));self.assertTrue((self.root/updates[0]['thumb'].lstrip('/')).is_file())
        self.assertEqual(list((self.root/'proxies').iterdir()),[])

    def test_rejected_publication_removes_only_this_attempts_new_files(self):
        result,_=self.preparation(update=lambda p,f:False)
        self.assertEqual(result['record']['status'],'error');self.assertEqual(list((self.root/'thumbs').iterdir()),[])

    def test_competing_preparation_is_exclusive_until_prior_job_stops(self):
        manager=TaskManager(self.root,{'media':lambda p,c:{}},start=False);self.addCleanup(manager.shutdown)
        owner={'workspace':workspace_id(self.root),'project':'p'};payload={'media_id':'m','token':'source','proxy_settings':{'max_edge':1280}}
        first=manager.submit('media','Clip',owner,payload)
        self.assertEqual(manager.submit('media','Clip',owner,payload)['id'],first['id'])
        with self.assertRaises(MediaTaskBusy):manager.submit('media','Clip',owner,{**payload,'proxy_settings':{'max_edge':640}})
        manager.cancel(first['id'])
        self.assertNotEqual(manager.submit('media','Clip',owner,{**payload,'proxy_settings':{'max_edge':640}})['id'],first['id'])


if __name__ == '__main__': unittest.main()

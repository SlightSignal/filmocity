"""Independent signals through the real isolated renderer/measurement worker."""
import array
import copy
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import audio_measurement as measurement
import render
from render_replace import stamp
from render_context import RenderContext
from work_budget import WorkBudget
import work_budget


class Measurements(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="Filmocity measurement É's ");self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def wav(self,name,seconds,sample,rate=48000,channels=2):
        path=self.root/name;pcm=array.array('h')
        for i in range(round(seconds*rate)):
            values=sample(i/rate)
            if not isinstance(values,(tuple,list)):values=[values]*channels
            pcm.extend(round(max(-1,min(1,v))*32767) for v in values)
        if sys.byteorder!='little':pcm.byteswap()
        with wave.open(str(path),'wb') as out:out.setparams((channels,2,rate,0,'NONE',''));out.writeframes(pcm.tobytes())
        return {'id':'m','path':str(path),'duration':seconds,'has_audio':True,'has_video':False,'channels':channels,'sample_rate':rate}
    def payload(self,media,mode='peak',clip=None,extra_media=None):
        c={'id':'clip','media_id':media['id'],'start':0,'in_':0,'out':media['duration'],'speed':1,**(clip or {})};duration=render.clip_dur(c)
        project={'version':3,'media':{media['id']:media,**(extra_media or {})},'sequences':[{'id':'s','width':64,'height':48,'fps':30,'master':{},'tracks':[{'id':'a','kind':'audio','index':0,'clips':[c]}]}]}
        resources=[stamp(m['path']) for m in project['media'].values()]
        return {'version':1,'kind':'audio_analysis','mode':mode,'scope':'timeline','sequence':'s','clip_ids':['clip'],'signature':'captured','context':{'workspace':'w','project':'p','revision':'r'},
            'items':[{'id':'clip','media_id':media['id'],'duration':duration,'clip':c,'project':project,'sequence':'s','resources':resources}]}
    def run_measure(self,payload):
        before=copy.deepcopy(payload);result=measurement.analyze(payload,scratch_parent=str(self.root));self.assertEqual(result['range'],payload.get('range'));self.assertEqual(result['media_id'],payload.get('media_id'));self.assertEqual(payload,before);self.assertFalse(list(self.root.glob('filmocity-render-*')));return result['measurements'][0]

    def test_sample_peak_preserves_opposite_phase_stereo_and_mono_duplication(self):
        for channels,sample in ((2,lambda t:(.5,-.5)),(1,lambda t:.5)):
            m=self.wav(f'phase-{channels}.wav',1,sample,channels=channels);row=self.run_measure(self.payload(m))
            expected=20*math.log10(round(.5*32767)/32768)
            self.assertAlmostEqual(row['peak_db'],expected,places=6);self.assertAlmostEqual(row['rms_db'],expected,places=6);self.assertFalse(row['silent']);self.assertEqual(row['channels'],2)

    def test_interpreted_subclip_window_uses_native_offset_once_not_legacy_twenty_db_error(self):
        parent=self.wav('levels.wav',4,lambda t:.01 if t<2 else .1);parent.update(id='parent',frame_rate='20/1',fps=20,interpret_fps=10,duration=8)
        sub={**parent,'id':'sub','subclip_of':'parent','sub_in':2,'duration':4}
        # Logical subclip [0,1) addresses native [.5*2,.5*3) = [1,1.5).
        payload=self.payload(sub,clip={'in_':0,'out':1},extra_media={'parent':parent});payload.update(scope='media',sequence=None,clip_ids=[],media_id='sub',range={'start':0,'end':1})
        row=self.run_measure(payload)
        self.assertAlmostEqual(row['peak_db'],20*math.log10(round(.01*32767)/32768),places=5)
        wrong=20*math.log10(round(.1*32767)/32768);self.assertAlmostEqual(wrong-row['peak_db'],20,delta=.02)

    def test_clip_pan_channels_gain_fades_and_live_automation_are_measured(self):
        m=self.wav('stereo.wav',3,lambda t:(.4,.8))
        base=self.run_measure(self.payload(m,clip={'audio':{'channels':'left','pan':1,'gain_db':-6.020599913,'constant_power':False,'fade_in':3}}))
        self.assertAlmostEqual(base['peak_db'],20*math.log10(round(.4*32767)/32768)-6.020599913,delta=.002)
        automated=self.run_measure(self.payload(m,clip={'audio':{'gain_db':15},'keyframes':{'audio.gain_db':[{'t':0,'v':-12},{'t':3,'v':-6}]}}))
        self.assertAlmostEqual(automated['peak_db'],20*math.log10(round(.8*32767)/32768)-6,delta=.02)
        faded=self.run_measure(self.payload(m,clip={'audio':{'constant_power':False,'fade_in':3,'fade_out':3}}))
        self.assertAlmostEqual(faded['peak_db'],20*math.log10(round(.8*32767)/32768*.25),delta=.002)

    def test_subcentibel_and_fractional_scalar_gain_are_not_ignored_or_rounded(self):
        m=self.wav('precise.wav',.5,lambda t:.5)
        base=20*math.log10(round(.5*32767)/32768)
        for gain in (-.004,.123456789,-6.020599913):
            with self.subTest(gain=gain):
                row=self.run_measure(self.payload(m,clip={'audio':{'gain_db':gain}}));self.assertAlmostEqual(row['peak_db'],base+gain,delta=.000002)

    def test_loudness_has_real_gating_true_peak_and_known_level_delta(self):
        rows=[]
        for level in (.2,.02):
            m=self.wav(f'loud-{level}.wav',3,lambda t:level*math.sin(2*math.pi*1000*t))
            rows.append(self.run_measure(self.payload(m,mode='loudness')))
        self.assertAlmostEqual(rows[0]['integrated_lufs']-rows[1]['integrated_lufs'],20,delta=.05)
        self.assertAlmostEqual(rows[0]['true_peak_dbtp'],20*math.log10(.2),delta=.03)
        self.assertAlmostEqual(rows[0]['integrated_lufs'],20*math.log10(.2)-.01,delta=.15)
        for row in rows:self.assertTrue(math.isfinite(row['loudness_range_lu']));self.assertIn('0.01',row['warnings'][-1])

    def test_digital_silence_and_below_gate_never_return_invented_finite_loudness(self):
        for level in (0,1/32767):
            m=self.wav(f'quiet-{level}.wav',1,lambda t:level*math.sin(2*math.pi*1000*t));row=self.run_measure(self.payload(m,mode='loudness'))
            self.assertIsNone(row['integrated_lufs'])
            if level==0:self.assertTrue(row['silent']);self.assertIsNone(row['peak_db']);self.assertIsNone(row['true_peak_dbtp'])
            else:self.assertFalse(row['silent']);self.assertLess(row['peak_db'],-80)

    def test_real_regular_onsets_have_measured_positions_and_no_extrapolated_missing_beats(self):
        positions=[.25+i*.5 for i in range(15)]
        def signal(t):return .4*math.sin(2*math.pi*1000*t) if any(0<=t-at<.075 for at in positions) else 0
        m=self.wav('pulses.wav',8,signal);row=self.run_measure(self.payload(m,mode='beats'))
        self.assertEqual(len(row['beats']),len(positions));self.assertAlmostEqual(row['bpm'],120,places=5)
        for actual,expected in zip(row['beats'],positions):self.assertAlmostEqual(actual,expected,delta=.005)
        self.assertGreaterEqual(row['tempo_confidence'],.9);self.assertEqual(row['resolution'],.005);self.assertNotIn('downbeats',row)

    def test_silent_steady_and_irregular_audio_refuse_beat_markers(self):
        irregular=[.2,.63,1.51,1.92,2.72,3.12,3.99,4.5,5.21,6.01,6.45,7.1]
        fixtures={'silence':lambda t:0,'steady':lambda t:.4*math.sin(2*math.pi*1000*t),
            'irregular':lambda t:.4*math.sin(2*math.pi*1000*t) if any(0<=t-at<.05 for at in irregular) else 0}
        for name,sample in fixtures.items():
            m=self.wav(name+'.wav',8,sample);before=set(self.root.iterdir())
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'silent|onset|irregular|ambiguous|few'):self.run_measure(self.payload(m,mode='beats'))
            self.assertEqual(set(self.root.iterdir()),before)

    def test_reverse_and_speed_measure_actual_rendered_clip_audio(self):
        m=self.wav('rising.wav',4,lambda t:.1 if t<2 else .5)
        row=self.run_measure(self.payload(m,clip={'in_':1,'out':3,'reverse':True,'speed':2,'audio':{'maintain_pitch':False}}))
        # Independent decoder/filter oracle includes genuine resampler overshoot
        # at the source level edge; source amplitudes alone miss that audible peak.
        decoded=subprocess.check_output(['ffmpeg','-v','error','-nostdin','-i',m['path'],'-af','atrim=start=1:end=3,asetpts=PTS-STARTPTS,areverse,asetrate=96000,aresample=48000','-c:a','pcm_f32le','-f','f32le','-'])
        samples=array.array('f');samples.frombytes(decoded)
        if sys.byteorder!='little':samples.byteswap()
        expected=20*math.log10(max(abs(value) for value in samples))
        self.assertEqual(row['duration'],1);self.assertAlmostEqual(row['peak_db'],expected,delta=.002)
        ramp=self.run_measure(self.payload(m,clip={'time_remap':[{'t':0,'v':.5},{'t':2,'v':2}]}))
        self.assertTrue(any('average-speed' in warning for warning in ramp['warnings']))

    def test_signed_container_origin_preserves_delayed_audio_in_the_selected_window(self):
        media=self.wav('delayed.wav',2,lambda t:.25);path=self.root/'origin.mov'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:s=32x32:r=10:d=2',
            '-itsoffset','0.5','-i',media['path'],'-map','0:v','-map','1:a','-c:v','png','-threads','1','-c:a','pcm_s16le',
            '-output_ts_offset','5',str(path)],check=True,capture_output=True,timeout=30)
        media.update(path=str(path),duration=2.5,has_video=True,fps=10)
        row=self.run_measure(self.payload(media,clip={'in_':0,'out':1}))
        level=round(.25*32767)/32768
        self.assertAlmostEqual(row['peak_db'],20*math.log10(level),delta=.001)
        self.assertAlmostEqual(row['rms_db'],20*math.log10(level)+10*math.log10(.5),delta=.001)

    def test_effect_gain_duck_and_inherited_fade_window_measure_the_live_processing(self):
        from overlap_normalization import slice_clip
        media=self.wav('effects.wav',8,lambda t:.4);level=round(.4*32767)/32768
        processed=self.run_measure(self.payload(media,clip={'out':2,'audio':{'gain_db':-2},
            'afx_stack':[{'type':'amplify','params':{'gain_db':6}}],
            'keyframes':{'audio.duck_db':[{'t':0,'v':-3},{'t':2,'v':-3}]}}))
        self.assertAlmostEqual(processed['peak_db'],20*math.log10(level)+1,delta=.002)
        old={'id':'clip','media_id':'m','start':0,'in_':0,'out':8,'audio':{'fade_in':4,'constant_power':False}}
        clipped=slice_clip(old,2,4);clipped['start']=0
        row=self.run_measure(self.payload(media,clip=clipped))
        self.assertAlmostEqual(row['rms_db'],20*math.log10(level)+10*math.log10(7/12),delta=.001)

    def test_nested_audio_includes_child_processing_and_outer_clip_gain(self):
        media=self.wav('nested.wav',2,lambda t:.4);payload=self.payload(media,clip={'audio':{'gain_db':-6}})
        project=payload['items'][0]['project'];child=copy.deepcopy(project['sequences'][0]);child['id']='child';child['name']='Nested audio'
        child['tracks'][0]['gain_db']=-3;child['master']={'gain_db':-2}
        outer={'id':'clip','sequence_id':'child','start':0,'in_':0,'out':2,'speed':1,'audio':{'gain_db':1}}
        project['sequences'][0]['tracks'][0]['clips']=[outer];project['sequences'].append(child)
        payload['items'][0].update(clip=outer,media_id=None)
        row=self.run_measure(payload)
        self.assertAlmostEqual(row['peak_db'],20*math.log10(round(.4*32767)/32768)-10,delta=.01)

    def test_float_measurements_keep_over_zero_peaks_and_short_loudness_is_unavailable(self):
        media=self.wav('over.wav',1,lambda t:.8*math.sin(2*math.pi*1000*t))
        row=self.run_measure(self.payload(media,mode='loudness',clip={'audio':{'gain_db':6}}))
        self.assertGreater(row['peak_db'],3);self.assertGreater(row['true_peak_dbtp'],3)
        short=self.run_measure(self.payload(media,mode='loudness',clip={'out':.2}))
        self.assertFalse(short['silent']);self.assertIsNone(short['integrated_lufs']);self.assertIsNotNone(short['peak_db'])

    def test_source_changed_during_measurement_rejects_result_and_retires_scratch(self):
        media=self.wav('change.wav',1,lambda t:.1);payload=self.payload(media);actual=measurement._scan
        def change(*args):
            result=actual(*args);path=Path(media['path']);path.write_bytes(path.read_bytes()+b'changed');return result
        with patch.object(measurement,'_scan',change),self.assertRaisesRegex(ValueError,'changed'):
            measurement.analyze(payload,scratch_parent=str(self.root))
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_invalid_bounds_resource_changes_and_nonfinite_pcm_refuse_without_scratch(self):
        m=self.wav('valid.wav',1,lambda t:.1);p=self.payload(m)
        for mutation in (lambda x:x['items'][0].update(duration=601),lambda x:x.update(items=[]),lambda x:x['items'][0].update(duration=float('nan'))):
            value=copy.deepcopy(p);mutation(value)
            with self.assertRaises(ValueError):measurement.analyze(value,scratch_parent=str(self.root))
        Path(m['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'changed'):measurement.analyze(p,scratch_parent=str(self.root))
        raw=self.root/'nonfinite.f32le';raw.write_bytes(struct.pack('<ff',float('nan'),0))
        with self.assertRaisesRegex(ValueError,'nonfinite'):measurement._scan(str(raw),1,lambda:None)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_large_filter_graph_uses_owned_script_instead_of_windows_command_line(self):
        m=self.wav('graph.wav',.1,lambda t:.2)
        payload=self.payload(m,clip={'afx_stack':[{'type':'amplify','params':{'gain_db':0}} for _ in range(400)]})
        calls=[];actual=measurement.subprocess.Popen
        def capture(command,**options):calls.append(command);return actual(command,**options)
        with patch.object(measurement.subprocess,'Popen',capture):row=self.run_measure(payload)
        self.assertTrue(any(any(option in command for option in ('-/filter_complex', '-filter_complex_script')) for command in calls));self.assertAlmostEqual(row['peak_db'],20*math.log10(round(.2*32767)/32768),places=5)

    def test_oversized_nested_dependency_refuses_before_any_process_or_scratch(self):
        m=self.wav('nested-bound.wav',1,lambda t:.2);payload=self.payload(m)
        project=payload['items'][0]['project'];project['sequences'][0]['tracks'][0]['clips']=[{'id':'nest','sequence_id':'long-child','start':0,'in_':0,'out':1}]
        project['sequences'].append({'id':'long-child','duration':601,'tracks':[]})
        with patch.object(measurement.subprocess,'Popen') as child,self.assertRaisesRegex(ValueError,'nested audio'):
            measurement.analyze(payload,scratch_parent=str(self.root))
        child.assert_not_called();self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_nested_budget_counts_repeated_variants_and_rejects_cycles(self):
        child={'id':'child','duration':600,'tracks':[]};root={'id':'root','tracks':[{'clips':[{'sequence_id':'child','start':i,'in_':0,'out':1,'angle':i} for i in range(7)]}]}
        with self.assertRaisesRegex(ValueError,'bounded render budget'):measurement.estimated_work({'sequences':[root,child]},'root')
        child.update(duration=1,tracks=[{'clips':[{'sequence_id':'root','start':0,'in_':0,'out':1}]}])
        with self.assertRaisesRegex(ValueError,'cycle'):measurement.estimated_work({'sequences':[root,child]},'root')

    def test_native_source_budget_counts_speed_reverse_interpretation_and_shared_input_gaps(self):
        media=self.wav('native-budget.wav',1,lambda t:.2);media['duration']=2000
        for clip,metadata in (({'in_':0,'out':601,'speed':601},{}),({'in_':0,'out':601,'speed':601,'reverse':True},{}),
                              ({'in_':0,'out':301,'speed':301},{'fps':10,'interpret_fps':20})):
            with self.subTest(clip=clip,metadata=metadata),patch.object(measurement.subprocess,'Popen') as process,self.assertRaisesRegex(ValueError,'Native audio source window'):
                measurement.analyze(self.payload({**media,**metadata},clip=clip),scratch_parent=str(self.root))
            process.assert_not_called()
        payload=self.payload({**media,'fps':20,'interpret_fps':10},clip={'in_':0,'out':1000,'speed':1000})
        self.assertEqual(measurement.estimated_work(payload['items'][0]['project'],'s')['native_seconds'],500.1)
        project=self.payload(media,clip={'in_':1,'out':2})['items'][0]['project']
        project['sequences'][0]['tracks'][0]['clips'].append({'id':'later','media_id':'m','start':1,'in_':700,'out':701})
        with self.assertRaisesRegex(ValueError,'Native audio source window'):measurement.estimated_work(project,'s')
        payload=self.payload(media,clip={'in_':1000,'out':1001})
        self.assertEqual(measurement.estimated_work(payload['items'][0]['project'],'s')['native_seconds'],3.1)
        many=self.payload(media,clip={'in_':0,'out':590,'speed':59});many['items']*=7
        with patch.object(measurement.subprocess,'Popen') as process,self.assertRaisesRegex(ValueError,'native audio.*decoder budget'):
            measurement.analyze(many,scratch_parent=str(self.root))
        process.assert_not_called()

    def test_nested_subclip_inherits_parent_stream_flags_before_native_admission_and_seek(self):
        parent=self.wav('parent-flags.wav',5,lambda t:.2);parent.update(id='parent',duration=1000)
        child={**parent,'id':'sub','subclip_of':'parent','sub_in':0,'has_audio':False,'is_image':True}
        payload=self.payload(child,clip={'out':1000,'speed':1000},extra_media={'parent':parent})
        project=payload['items'][0]['project'];nested=copy.deepcopy(project['sequences'][0]);nested.update(id='child',name='Nested')
        project['sequences'][0]['tracks'][0]['clips']=[{'id':'nested','sequence_id':'child','start':0,'in_':0,'out':1}];project['sequences'].append(nested)
        with patch.object(measurement.subprocess,'Popen') as process,self.assertRaisesRegex(ValueError,'Native audio source window'):
            measurement.analyze(payload,scratch_parent=str(self.root))
        process.assert_not_called()
        nested['tracks'][0]['clips'][0].update(in_=3,out=4,speed=1)
        calls=[];actual=measurement.subprocess.Popen
        def record(command,**options):calls.append(command);return actual(command,**options)
        with patch.object(measurement.subprocess,'Popen',record):row=self.run_measure(payload)
        self.assertAlmostEqual(row['peak_db'],20*math.log10(round(.2*32767)/32768),places=5)
        self.assertTrue(any('-ss' in command and command[command.index('-ss')+1]=='1.000000000000' for command in calls))

    def _shadow_pcm(self,payload):
        item=payload['items'][0];count=round(item['duration']*48000)
        with RenderContext(scratch_parent=str(self.root)) as context:
            path,frames=measurement._pcm(item,context);bounded=Path(path).read_bytes()
        with RenderContext(scratch_parent=str(self.root)) as context:
            path=context.new_file('.f32le')
            command,_=render.build_command(item['project'],item['sequence'],path,{'format':'audio','acodec':'wav_float'},context=context)
            self.assertNotIn('-ss',command)
            command=list(command[:-1])+['-t',str(count/48000),'-ac','2','-f','f32le',path]
            measurement._run(command,context);ordinary=Path(path).read_bytes()
        self.assertEqual(frames,count);self.assertEqual(len(bounded),len(ordinary));self.assertEqual(bounded,ordinary)
        return bounded

    def test_seeked_signed_origin_subclip_interpretation_and_ramp_match_ordinary_renderer_pcm(self):
        media=self.wav('seek-origin.wav',6,lambda t:.2*math.sin(2*math.pi*700*t) if t<3 else .05*math.sin(2*math.pi*700*t))
        for origin in (5,-2):
            path=self.root/f'origin-{origin}.mov'
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:s=32x32:r=10:d=6',
                '-itsoffset','0.5','-i',media['path'],'-map','0:v','-map','1:a','-c:v','png','-threads','1','-c:a','pcm_s16le',
                '-avoid_negative_ts','disabled','-output_ts_offset',str(origin),str(path)],check=True,capture_output=True,timeout=30)
            parent={**media,'id':'parent','path':str(path),'duration':13,'has_video':True,'frame_rate':'20/1','fps':20,'interpret_fps':10}
            sub={**parent,'id':'sub','subclip_of':'parent','sub_in':4,'duration':9}
            for extra in ({},{'reverse':True,'speed':2,'audio':{'maintain_pitch':False}},
                          {'time_remap':[{'t':0,'v':.5},{'t':1,'v':2}]}):
                payload=self.payload(sub,clip={'in_':3,'out':6,**extra},extra_media={'parent':parent})
                with self.subTest(origin=origin,extra=extra):self._shadow_pcm(payload)

    def test_seeked_non48k_and_compressed_camera_audio_match_ordinary_renderer(self):
        original=self.wav('camera-44100.wav',7,lambda t:.2*math.sin(2*math.pi*997*t)+.03*math.sin(2*math.pi*6431*t),rate=44100)
        for codec in ('pcm','aac'):
            media=copy.deepcopy(original)
            if codec=='aac':
                path=self.root/'camera-aac.mov'
                subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:s=32x32:r=10:d=7',
                    '-itsoffset','0.25','-i',original['path'],'-map','0:v','-map','1:a','-c:v','png','-threads','1',
                    '-c:a','aac','-b:a','192k','-output_ts_offset','5',str(path)],check=True,capture_output=True,timeout=30)
                media.update(path=str(path),has_video=True,fps=10,duration=7.25)
            for begin in (3.2371041666666667,5.123):
                with self.subTest(codec=codec,begin=begin):self._shadow_pcm(self.payload(media,clip={'in_':begin,'out':begin+1}))

    def test_seeked_missing_late_audio_preserves_delayed_and_eof_silence(self):
        original=self.wav('delayed-short.wav',1,lambda t:.4);path=self.root/'delayed-long.mov'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:s=32x32:r=10:d=8',
            '-itsoffset','4.5','-i',original['path'],'-map','0:v','-map','1:a','-c:v','png','-threads','1','-c:a','pcm_s16le',
            '-output_ts_offset','5',str(path)],check=True,capture_output=True,timeout=30)
        media={**original,'path':str(path),'has_video':True,'fps':10,'duration':8}
        for begin in (3,4,6):
            with self.subTest(begin=begin):self._shadow_pcm(self.payload(media,clip={'in_':begin,'out':begin+1}))

    def test_very_late_interpreted_subclip_seeks_directly_and_matches_known_short_window(self):
        # A sparse three-hour file exercises real demux seeking without spending
        # hours creating or reading its silent prefix. Only the selected second
        # contains signal; a short independent file is the decoded PCM oracle.
        rate=8000;seconds=10801;channels=2;frames=seconds*rate;size=frames*channels*2
        path=self.root/'three-hours.wav';block=array.array('h',[8192,-8192]*rate)
        if sys.byteorder!='little':block.byteswap()
        with open(path,'wb') as stream:
            stream.write(struct.pack('<4sI4s4sIHHIIHH4sI',b'RIFF',size+36,b'WAVE',b'fmt ',16,1,channels,rate,rate*channels*2,channels*2,16,b'data',size))
            stream.seek(44+10800*rate*channels*2);stream.write(block.tobytes())
        parent={'id':'parent','path':str(path),'duration':seconds*2,'has_audio':True,'has_video':False,'channels':2,'sample_rate':rate,'fps':20,'frame_rate':'20/1','interpret_fps':10}
        sub={**parent,'id':'sub','subclip_of':'parent','sub_in':21598,'duration':4}
        payload=self.payload(sub,clip={'in_':2,'out':4},extra_media={'parent':parent})
        calls=[];actual=measurement.subprocess.Popen
        def record(command,**options):calls.append(command);return actual(command,**options)
        with patch.object(measurement.subprocess,'Popen',record):row=self.run_measure(payload)
        command=calls[0];self.assertAlmostEqual(float(command[command.index('-ss')+1]),10798);self.assertEqual(float(command[command.index('-t')+1]),3.1)
        short=self.wav('short-oracle.wav',3,lambda t:(.25,-.25) if t>=2 else 0,rate=rate)
        short.update(fps=20,frame_rate='20/1',interpret_fps=10,duration=6)
        oracle=self.run_measure(self.payload(short,clip={'in_':4,'out':6}))
        self.assertAlmostEqual(row['peak_db'],oracle['peak_db'],places=6);self.assertAlmostEqual(row['rms_db'],oracle['rms_db'],places=6)

    def test_nested_seek_inherits_origin_and_native_dependency_budget(self):
        media=self.wav('nested-seek.wav',6,lambda t:.1 if t<3 else .3)
        payload=self.payload(media,clip={'in_':3.25,'out':4.25})
        project=payload['items'][0]['project'];child=copy.deepcopy(project['sequences'][0]);child.update(id='child',name='Nested');child['tracks'][0]['clips'][0]['start']=0
        project['sequences'][0]['tracks'][0]['clips']=[{'id':'nested','sequence_id':'child','start':0,'in_':0,'out':1}];project['sequences'].append(child)
        calls=[];actual=measurement.subprocess.Popen
        def record(command,**options):calls.append(command);return actual(command,**options)
        with patch.object(measurement.subprocess,'Popen',record):self._shadow_pcm(payload)
        self.assertTrue(any('-ss' in command and command[command.index('-ss')+1]=='2.000000000000' for command in calls))
        child['tracks'][0]['clips'][0].update(in_=0,out=601,speed=601)
        with self.assertRaisesRegex(ValueError,'Native audio source window'):measurement.estimated_work(project,'s')

    def test_process_failure_output_limit_and_timeout_join_children_and_cleanup(self):
        for command,error,maximum in ((['ffmpeg','-v','error','-i','missing-measurement-input','-f','null','-'],'failed',None),
                (['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1','-f','f32le','-'],'bounded',1024),
                (['ffmpeg','-v','error','-nostdin','-re','-f','lavfi','-i','anullsrc=r=8000:cl=mono','-t','60','-f','null','-'],'timed out',None)):
            holder={}
            with self.subTest(error=error),patch.object(measurement,'MAX_LOG_BYTES',maximum or measurement.MAX_LOG_BYTES):
                with self.assertRaisesRegex(ValueError,error):
                    with RenderContext(proc_holder=holder,scratch_parent=str(self.root)) as context:measurement._run(command,context,timeout=.1 if error=='timed out' else 5)
                self.assertNotIn('proc',holder);self.assertFalse(list(self.root.glob('filmocity-render-*')))
                self.assertNotIn('scratch_diagnostics',holder)

    def test_running_cancel_reaps_child_and_resource_wait_cancel_starts_none(self):
        holder={};errors=[]
        def operation():
            try:
                with RenderContext(proc_holder=holder,scratch_parent=str(self.root)) as context:
                    measurement._run(['ffmpeg','-v','error','-nostdin','-re','-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','30','-f','null','-'],context)
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=operation);thread.start();deadline=time.monotonic()+5
        while 'proc' not in holder and time.monotonic()<deadline:time.sleep(.01)
        child=holder.get('proc');self.assertIsNotNone(child);holder['cancelled']=True;thread.join(5)
        self.assertFalse(thread.is_alive());self.assertIsNotNone(child.poll());self.assertRegex(str(errors[0]),'cancelled');self.assertFalse(list(self.root.glob('filmocity-render-*')))
        budget=WorkBudget(capacity=1);holder={};errors=[]
        with patch.object(work_budget,'BUDGET',budget),budget.work({},'probe'):
            thread=threading.Thread(target=operation);thread.start();deadline=time.monotonic()+5
            while not budget.snapshot()['waiting'] and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(budget.snapshot()['waiting'],1);self.assertNotIn('proc',holder);holder['cancelled']=True;thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(budget.snapshot()['waiting'],0);self.assertEqual(budget.snapshot()['active'],0)

if __name__=='__main__':unittest.main()

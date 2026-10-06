"""Independent PCM oracles for selected-channel analysis and synchronization."""
import array
import copy
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import media_analysis
import audio_sync
from render_context import RenderContext

CONTEXT={'workspace':'w','project':'p','revision':'r'}


class SelectedChannelAnalysis(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='Filmocity selected channel analysis É ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def wav(self,name,channels,rows):
        path=self.root/(name+'.wav');samples=array.array('h')
        for row in rows:samples.extend(row)
        if sys.byteorder!='little':samples.byteswap()
        with wave.open(str(path),'wb') as stream:
            stream.setparams((channels,2,8000,0,'NONE',''));stream.writeframes(samples.tobytes())
        return path

    def project(self):
        def frames():
            for index in range(4*8000):
                tone=5000 if index%80<40 else -5000
                yield (0 if index<2*8000 else tone,tone,tone,-tone,tone,-tone)
        path=self.wav('six-channels',6,frames())
        parent={'id':'m','path':str(path),'duration':4,'has_audio':True,'has_video':False,'channels':6,'sample_rate':8000,
                'fps':30,'frame_rate':'30/1','audio_streams':[{'channels':6,'sample_rate':8000}]}
        library={'m':parent}
        for identity,index in [('left',0),('right',1)]:
            library[identity]={**copy.deepcopy(parent),'id':identity,'subclip_of':'m','sub_in':0,
                'audio_alias':{'version':1,'physical_media_id':'m','source_media_id':'m','channel_index':index}}
        return {'media':library,'sequences':[]}

    def analyze(self,project,identity='left',**options):
        payload=media_analysis.capture(project,{'media_id':identity,'min_gap':.1,'pad':0,**options},'silences',CONTEXT)
        return media_analysis.analyze(payload,scratch_parent=str(self.root))

    def test_silence_uses_selected_channel_before_other_stream_channels(self):
        project=self.project();left=self.analyze(project);right=self.analyze(project,'right')
        self.assertEqual(left['silences'],[{'start':0,'end':2}]);self.assertEqual(right['silences'],[])
        # The ordinary physical source retains its multichannel RMS behavior.
        self.assertEqual(self.analyze(project,'m')['silences'],[])

    def test_interpreted_channel_subclip_retains_selected_local_source_clock(self):
        project=self.project()
        for identity,media in project['media'].items():
            media.update(interpret_fps=24,duration=5)
            if identity!='m':media.update(sub_in=1.25,duration=2.5)
        result=self.analyze(project)
        self.assertEqual(result['range'],{'start':0,'end':2.5})
        self.assertEqual(result['silences'],[{'start':0,'end':1.25}])
        self.assertEqual(self.analyze(project,'right')['silences'],[])

    def test_channel_selection_keeps_delayed_audio_common_container_origin(self):
        project=self.project();parent=project['media']['m'];source=self.root/'delayed.mkv'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:s=32x32:r=20:d=4.25',
            '-itsoffset','0.25','-i',parent['path'],'-map','0:v','-map','1:a','-c:v','ffv1','-threads','1','-c:a','pcm_s16le',
            '-output_ts_offset','5',str(source)],check=True,capture_output=True,timeout=30)
        for media in project['media'].values():media.update(path=str(source),duration=4.25)
        left=self.analyze(project);right=self.analyze(project,'right')
        self.assertAlmostEqual(left['silences'][0]['end'],2.25,delta=.021)
        self.assertAlmostEqual(right['silences'][0]['end'],.25,delta=.021)
        self.assertEqual(left['silences'][0]['start'],0);self.assertEqual(right['silences'][0]['start'],0)

    def test_analysis_identity_retires_a_result_when_only_channel_changes(self):
        project=self.project();body={'media_id':'left','min_gap':.1,'pad':0}
        payload=media_analysis.capture(project,body,'silences',CONTEXT)
        project['media']['left']['audio_alias']['channel_index']=1
        with self.assertRaisesRegex(ValueError,'source changed'):media_analysis.validate_current(project,payload,CONTEXT)
        replacement=media_analysis.capture(project,body,'silences',CONTEXT)
        self.assertNotEqual(payload['signature'],replacement['signature'])

    def test_silence_uses_independent_channel_factor_and_rejects_native_overrun(self):
        project=self.project();alias=project['media']['left']
        alias.update(interpret_fps=24,duration=5)
        # Five selected logical seconds fit four native parent seconds.
        self.assertEqual(self.analyze(project)['silences'],[{'start':0,'end':2.5}])
        alias.update(sub_in=1.25,duration=3.75)
        self.assertEqual(self.analyze(project)['silences'],[{'start':0,'end':1.25}])
        alias['duration']=3.751
        with self.assertRaisesRegex(ValueError,'beyond'):self.analyze(project)
        alias.update(duration=3.75);alias['audio_alias'].pop('channel_index')
        with self.assertRaisesRegex(ValueError,'differs'):self.analyze(project)

    def test_sync_independent_channel_factor_maps_known_offset_and_native_bounds(self):
        from test_audio_sync import recording
        project=recording(self.root)
        for identity in ('a','b'):
            parent=project['media'][identity];parent.update(fps=30,frame_rate='30/1')
            project['media'][identity+'-channel']={**copy.deepcopy(parent),'id':identity+'-channel','subclip_of':identity,'sub_in':0,
                'duration':10,'interpret_fps':24,'audio_alias':{'version':1,'physical_media_id':identity,'source_media_id':identity,'channel_index':0}}
        body={'media_ids':['a-channel','b-channel']};payload=audio_sync.capture(project,body,CONTEXT)
        self.assertEqual(payload['items'][0]['factor'],1.25);self.assertEqual(payload['items'][0]['duration'],10)
        result=audio_sync.analyze(payload,scratch_parent=str(self.root))
        self.assertAlmostEqual(result['offsets']['b-channel'],.123*1.25,places=8)
        self.assertEqual(result['matches'][1]['method'],'pcm')
        project['media']['a-channel']['duration']=10.001
        with self.assertRaisesRegex(ValueError,'beyond'):audio_sync.capture(project,body,CONTEXT)

    def test_raw_and_timeline_gain_measure_independent_channel_clock_against_mono(self):
        import audio_workflow
        import audio_measurement
        project=self.project();alias=project['media']['left'];alias.update(interpret_fps=24,duration=5)
        mono=self.wav('independent-mono',1,((0 if i<2*8000 else (5000 if i%80<40 else -5000),) for i in range(4*8000)))
        project['media']['oracle']={**copy.deepcopy(project['media']['m']),'id':'oracle','path':str(mono),
            'channels':1,'audio_streams':[{'channels':1,'sample_rate':8000}],'interpret_fps':24,'duration':5}
        clip={'id':'c','media_id':'left','start':0,'in_':0,'out':5,'speed':1,'audio':{'gain_db':-3}}
        project['sequences']=[{'id':'s','name':'Independent clock','fps':30,'width':64,'height':48,
            'tracks':[{'id':'a','kind':'audio','index':0,'clips':[clip]}]}]
        def measured(body):
            payload=audio_workflow.capture(project,body,'peak',CONTEXT)
            return audio_measurement.analyze(payload,scratch_parent=str(self.root))['measurements'][0]
        reference=measured({'media_id':'oracle','in':0,'out':5})
        raw=measured({'media_id':'left','in':0,'out':5});timeline=measured({'sequence':'s','clip_ids':['c']})
        self.assertEqual(raw['duration'],5);self.assertEqual(timeline['duration'],5)
        self.assertAlmostEqual(raw['peak_db'],reference['peak_db'],places=5)
        self.assertAlmostEqual(raw['rms_db'],reference['rms_db'],places=5)
        self.assertAlmostEqual(timeline['peak_db'],raw['peak_db']-3,places=5)
        self.assertAlmostEqual(timeline['rms_db'],raw['rms_db']-3,places=5)
        alias['duration']=5.001;clip['out']=5.001
        for body in ({'media_id':'left','out':5.001},{'sequence':'s','clip_ids':['c']}):
            with self.assertRaisesRegex(ValueError,'beyond'):audio_workflow.capture(project,body,'peak',CONTEXT)

    def test_sync_selects_known_pcm_before_multichannel_downmix(self):
        rng=random.Random(314159);amplitudes=[rng.uniform(.05,.9) for _ in range(150)]
        signal=[round(rng.uniform(-1,1)*amplitudes[i//640]*14000) for i in range(12*8000)]
        library={};offset=.123
        for identity,start in [('a',0),('b',round(offset*8000))]:
            noise=random.Random(421 if identity=='a' else 901)
            # Four louder unrelated channels make an accidental downmix fail.
            rows=((noise.randint(-25000,25000),noise.randint(-25000,25000),sample,
                   noise.randint(-25000,25000),noise.randint(-25000,25000),-sample)
                  for sample in signal[start:start+8*8000])
            path=self.wav(identity,6,rows)
            parent={'id':identity,'path':str(path),'duration':8,'channels':6,'sample_rate':8000,'has_audio':True}
            library[identity]=parent;alias=identity+'-channel'
            library[alias]={**parent,'id':alias,'subclip_of':identity,'sub_in':0,
                'audio_alias':{'version':1,'physical_media_id':identity,'source_media_id':identity,'channel_index':2}}
        project={'media':library,'sequences':[]};body={'media_ids':['a-channel','b-channel']}
        payload=audio_sync.capture(project,body,CONTEXT);result=audio_sync.analyze(payload,scratch_parent=str(self.root))
        self.assertEqual(result['offsets']['b-channel'],offset)
        self.assertEqual(result['matches'][1]['method'],'pcm');self.assertGreater(result['matches'][1]['waveform_correlation'],.999)
        project['media']['a-channel']['audio_alias']['channel_index']=3
        with self.assertRaisesRegex(ValueError,'changed'):audio_sync.validate_current(project,payload,CONTEXT)
        with self.assertRaisesRegex(ValueError,'selected source channel'):audio_sync.capture(project,{'media_ids':['a','b']},CONTEXT)

    def test_sync_decoding_distinguishes_channel_aliases_and_rejects_unavailable_channel(self):
        project=self.project();payload=audio_sync.capture(project,{'media_ids':['left','right']},CONTEXT)
        with RenderContext(scratch_parent=str(self.root)) as context:
            left=audio_sync._decode(payload['items'][0],context)
            # The steady right channel provides no unique sync point; decoding
            # it must no longer borrow left-channel transitions to appear useful.
            with self.assertRaisesRegex(ValueError,'little changing'):audio_sync._decode(payload['items'][1],context)
        self.assertEqual(max(abs(value) for value in left['pcm'][:2*2*8000]),0)
        for value in (6,-1,True):
            project['media']['left']['audio_alias']['channel_index']=value
            with self.assertRaises(ValueError):media_analysis.capture(project,{'media_id':'left'},'silences',CONTEXT)
            with self.assertRaises(ValueError):audio_sync.capture(project,{'media_ids':['left','right']},CONTEXT)


if __name__=='__main__':unittest.main()

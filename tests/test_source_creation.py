"""Source-relative plans and actual discrete-channel PCM/proxy behavior."""
import array
import copy
from fractions import Fraction
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import source_creation as creation
import source_commands
import source_relink
import media_preparation
import media_preview
import render
from render_context import RenderContext
from task_inputs import source_stamp

REPLACEMENT = str(ROOT / 'tests/fixtures/planned-new-source.mkv')


def project(channels=2):
    media={'id':'m','name':'Camera','path':str(ROOT / 'tests/fixtures/missing-source.mkv'),'duration':8,'frame_rate':'30000/1001','fps':29.97,
        'has_video':True,'has_audio':True,'channels':channels,'sample_rate':48000,'audio_streams':[{'index':1,'channels':channels,'sample_rate':48000}],
        'width':64,'height':48,'is_image':False,'acodec':'pcm_s16le','ingest_token':'old','status':'ingesting','task_id':'running',
        'proxy':'/proxies/borrowed.mp4','proxy_info':{'file_stamp':[1,2,3]},'thumb':'/thumbs/borrowed.jpg','wave':'/thumbs/borrowed.png',
        'note':'Authored note','label':'green','input_transform':'slog3','input_transform_resource':{'path':'/look.cube'},
        'workflow':{'import':'old'},'transcript':{'words':[{'start':0,'end':1,'word':'old'}]}}
    clip={'id':'clip','media_id':'m','start':0,'in_':0,'out':2,'reverse':True,'time_remap':[{'t':0,'v':1},{'t':1,'v':2}],
        'keyframes':{'audio.gain_db':[{'t':0,'v':-3}]},'audio':{'fade_in':.1},'note':'Timeline editorial note'}
    return {'id':'document','version':3,'name':'P','media':{'m':media},'sequences':[{'id':'s','name':'S','width':64,'height':48,'fps':30,
        'tracks':[{'id':'v','kind':'video','index':0,'locked':True,'clips':[clip]}]}]}


def plan(p,mode='subclip',**body):
    return creation.plan(p,{'media_id':'m','in':1,'out':3,**body},mode,identity={'subclip':'a','breakout':'b','duplicate':'c'}[mode]*32,added=123)


def applied(p,result):
    candidate=copy.deepcopy(p)
    for item in result['media']:candidate['media'][item['id']]=copy.deepcopy(item)
    return candidate


class SourceCreation(unittest.TestCase):
    def test_source_relative_subclip_flattens_own_interpretation_without_timeline_edits(self):
        p=project();p['media']['m'].update(frame_rate='60/1',fps=60,interpret_fps=30,duration=16)
        p['media']['sub']={**copy.deepcopy(p['media']['m']),'id':'sub','subclip_of':'m','sub_in':10,'duration':5,'interpret_fps':24}
        before=copy.deepcopy(p);r=plan(p,media_id='sub',**{'in':1,'out':3})
        c=r['media'][0];self.assertEqual((c['subclip_of'],c['sub_in'],c['duration'],c['interpret_fps']),('m',11,2,24))
        self.assertEqual(r['summary']['windows'][0]['native_in'],4.4);self.assertEqual(r['summary']['windows'][0]['native_out'],5.2)
        self.assertEqual(p,before);self.assertEqual(applied(p,r)['sequences'],before['sequences']);self.assertFalse(r['prepare_ids'])
        self.assertEqual(c['status'],'ready');self.assertEqual(c['note'],before['media']['sub']['note'])
        self.assertEqual(c['input_transform_resource'],before['media']['sub']['input_transform_resource'])
        for key in ('proxy','proxy_info','wave','task_id','workflow','transcript'):self.assertNotIn(key,c)
        self.assertNotEqual(c['ingest_token'],'old')

    def test_native_fractional_audio_sample_and_ntsc_picture_boundaries_are_not_rounded(self):
        p=project();r=plan(p,**{'in':1001/30000,'out':2002/30000})
        self.assertEqual(r['media'][0]['sub_in'],1001/30000)
        p['media']['m'].update(has_video=False,sample_rate=44100)
        start=.12345;r=plan(p,**{'in':start,'out':start+1/44100})
        self.assertEqual(r['media'][0]['sub_in'],start)
        with self.assertRaisesRegex(ValueError,'sample'):plan(p,**{'in':start,'out':start+.2/44100})

    def test_outside_empty_boolean_nonfinite_and_missing_sources_are_atomic(self):
        p=project();before=copy.deepcopy(p)
        for fields in ({'in':-1},{'in':3,'out':3},{'out':9},{'in':True},{'out':float('nan')},{'media_id':'absent'}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):plan(p,**fields)
        self.assertEqual(p,before)

    def test_cycles_and_nested_parent_chains_are_explicit(self):
        p=project();p['media']['sub']={**p['media']['m'],'id':'sub','subclip_of':'m','sub_in':1,'duration':3}
        p['media']['nested']={**p['media']['sub'],'id':'nested','subclip_of':'sub'}
        with self.assertRaisesRegex(ValueError,'nested'):plan(p,media_id='nested')
        p['media']['m']['subclip_of']='sub'
        with self.assertRaises(ValueError):plan(p,media_id='sub')

    def test_subclip_marker_ranges_crop_and_rebase_without_losing_opaque_notes(self):
        p=project();p['media']['m']['markers']=[{'t':.2,'name':'outside'},{'time':1.5,'name':'point'},
            {'start':.5,'end':2,'duration':1.5,'name':'range'},{'t':3,'name':'exclusive end'},{'frame':17,'name':'unknown'}]
        r=plan(p);markers=r['media'][0]['markers']
        self.assertEqual(markers,[{'time':.5,'name':'point'},{'start':0,'end':1,'duration':1,'name':'range'},{'frame':17,'name':'unknown'}])
        self.assertIn('unknown clock',' '.join(r['warnings']));self.assertEqual(len(p['media']['m']['markers']),5)

    def test_breakout_every_discrete_channel_new_owned_records_and_no_fake_wav(self):
        p=project(6);before=copy.deepcopy(p);r=plan(p,'breakout')
        self.assertEqual(len(r['media']),6);self.assertEqual(len(r['prepare_ids']),6)
        self.assertEqual([m['audio_alias']['channel_index'] for m in r['media']],list(range(6)))
        self.assertEqual(len({m['ingest_token'] for m in r['media']}),6)
        for item in r['media']:
            self.assertFalse(item['has_video']);self.assertTrue(item['has_audio']);self.assertEqual(item['channels'],6)
            self.assertNotIn('input_transform',item);self.assertFalse(item['name'].endswith('.wav'));self.assertNotIn('proxy',item)
            self.assertEqual(source_commands.alias_source(applied(p,r),item)['audio_alias']['channel_index'],item['audio_alias']['channel_index'])
        self.assertEqual(p,before);self.assertIn('first audio stream',' '.join(r['warnings']))

    def test_breakout_refuses_unknown_inconsistent_or_excessive_channels(self):
        for count in (None,True,0,33,2.5):
            p=project();p['media']['m']['channels']=count
            with self.subTest(count=count),self.assertRaises(ValueError):plan(p,'breakout')
        p=project();p['media']['m']['audio_streams'][0]['channels']=6
        with self.assertRaisesRegex(ValueError,'inconsistent'):plan(p,'breakout')
        p=project();p['media']['sub']={**p['media']['m'],'id':'sub','subclip_of':'m','sub_in':0,'duration':3,'interpret_fps':24}
        r=plan(p,'breakout',media_id='sub');self.assertEqual(r['media'][0]['interpret_fps'],24)
        effective=source_commands.alias_source(applied(p,r),r['media'][0]);self.assertEqual(effective['interpret_fps'],24)

    def test_duplicate_selection_copies_authored_fields_and_detaches_all_pending_work(self):
        p=project();p['media']['sub']={**copy.deepcopy(p['media']['m']),'id':'sub','subclip_of':'m','sub_in':1,'duration':4}
        r=plan(p,'duplicate',media_ids=['sub','m']);self.assertEqual(r['summary']['source_media_ids'],['sub','m'])
        self.assertEqual(r['prepare_ids'],[r['media'][1]['id']]);self.assertEqual(r['media'][0]['subclip_of'],'m')
        for c in r['media']:
            self.assertEqual(c['note'],'Authored note');self.assertEqual(c['label'],'green')
            for key in ('task_id','proxy','thumb','wave','proxy_info','workflow','transcript'):self.assertNotIn(key,c)
        self.assertEqual(plan(p,'duplicate',media_ids=['sub','m']),r)
        with self.assertRaisesRegex(ValueError,'already been used'):plan(applied(p,r),'duplicate',media_ids=['sub','m'])
        for ids in ([],['m','m'],['m']*51):
            with self.assertRaises(ValueError):plan(p,'duplicate',media_ids=ids)

    def test_duplicate_synthetic_is_logical_no_queue_and_generated_subclip_refuses(self):
        p=project();p['media']['m']['synthetic']={'type':'color','color':'red'}
        r=plan(p,'duplicate',media_ids=['m']);self.assertFalse(r['resources']);self.assertFalse(r['prepare_ids']);self.assertEqual(r['media'][0]['status'],'ready')
        with self.assertRaisesRegex(ValueError,'Generated'):plan(p)

    def test_numbered_source_paths_are_bounded_before_expansion(self):
        p=project();p['media']['m'].update(sequence_frames=3,path='/frames/%04d.png',input_opts=['-start_number','42'])
        self.assertEqual(plan(p)['resources'],['/frames/0042.png','/frames/0043.png','/frames/0044.png'])
        p['media']['m']['sequence_frames']=1000000
        with patch.object(creation,'media_files',side_effect=AssertionError('must reject before allocation')):
            with self.assertRaisesRegex(ValueError,'10000'):plan(p)

    def test_copy_amplification_refuses_before_deepcopy(self):
        p=project(32);p['media']['m']['custom']='x'*300000
        with patch.object(creation.copy,'deepcopy',side_effect=AssertionError('must reject before copying')):
            with self.assertRaisesRegex(ValueError,'eight MiB'):plan(p,'breakout')

    def test_alias_subclip_and_duplicate_keep_selected_channel_but_own_new_preview(self):
        p=project();first=plan(p,'breakout');p=applied(p,first);a=first['media'][1];a['proxy']='/proxies/old.m4a';p['media'][a['id']]=a
        for mode,body in [('subclip',{'media_id':a['id']}),('duplicate',{'media_ids':[a['id']]})]:
            r=plan(p,mode,**body);c=r['media'][0]
            self.assertEqual(c['audio_alias']['channel_index'],1);self.assertEqual(c['subclip_of'],'m');self.assertNotIn('proxy',c)
            self.assertEqual(r['prepare_ids'],[c['id']]);self.assertEqual(c['status'],'unprepared')
        with self.assertRaisesRegex(ValueError,'already a selected channel'):plan(p,'breakout',media_id=a['id'])

    def test_relink_refuses_channel_lost_in_replacement_before_any_ops(self):
        p=project(6);r=plan(p,'breakout');p=applied(p,r);info={k:v for k,v in p['media']['m'].items() if k in source_relink.MEASURED}
        info.update(channels=2,audio_streams=[{'index':1,'channels':2,'sample_rate':48000}])
        result=source_relink.plan(p,{'media_id':'m','_context':{'workspace':'w','project':'p','revision':'r'}},info,REPLACEMENT,[[REPLACEMENT,100,1,2]])
        self.assertFalse(result['ok']);self.assertFalse(result['ops']);self.assertIn('alias_channel_unavailable',[i['code'] for i in result['issues']])


    def test_relink_channel_alias_own_factor_preserved_while_native_bounds_still_apply(self):
        p=project();p['media']['m'].update(frame_rate='60/1',fps=60,interpret_fps=30,duration=16)
        p['media']['sub']={**copy.deepcopy(p['media']['m']),'id':'sub','subclip_of':'m','sub_in':10,'duration':5,'interpret_fps':24}
        r=plan(p,'breakout',media_id='sub');p=applied(p,r)
        info={k:v for k,v in p['media']['m'].items() if k in source_relink.MEASURED};info['duration']=8
        body={'media_id':'m','_context':{'workspace':'w','project':'p','revision':'r'}}
        result=source_relink.plan(p,body,info,REPLACEMENT,[[REPLACEMENT,100,1,2]])
        self.assertTrue(result['ok']);self.assertEqual(result['media'][r['media'][0]['id']]['interpret_fps'],24)
        info['duration']=5
        result=source_relink.plan(p,body,info,REPLACEMENT,[[REPLACEMENT,100,1,2]])
        self.assertFalse(result['ok']);self.assertIn('dependent_too_short',[i['code'] for i in result['issues']])


class DiscreteChannelPCM(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='filmocity-channel-');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'six.wav';self.channels=[]
        for channel in range(6):self.channels.append([int(4000*math.sin(2*math.pi*(200+channel*100)*i/48000)) for i in range(48000)])
        data=array.array('h',(self.channels[c][i] for i in range(48000) for c in range(6)))
        if sys.byteorder!='little':data.byteswap()
        with wave.open(str(self.source),'wb') as stream:stream.setparams((6,2,48000,0,'NONE',''));stream.writeframes(data.tobytes())
        self.p=project(6);self.p['media']['m'].update(path=str(self.source),duration=1,has_video=False,width=0,height=0,frame_rate='30/1',fps=30)
        result=plan(self.p,'breakout');self.p=applied(self.p,result);self.aliases=result['media']

    def audio(self,document,mid,name,**changes):
        p=copy.deepcopy(document);clip={'id':'c','media_id':mid,'start':0,'in_':0,'out':1,'speed':1,'audio':{'maintain_pitch':False},**changes}
        p['sequences']=[{'id':'s','name':'S','width':64,'height':48,'fps':30,'tracks':[{'id':'a','index':0,'kind':'audio','clips':[clip]}]}]
        out=self.root/(name+'.wav')
        with RenderContext(scratch_parent=str(self.root)) as owned:render.render(p,'s',str(out),{'format':'audio','acodec':'wav'},context=owned)
        with wave.open(str(out),'rb') as stream:self.assertEqual(stream.getnchannels(),2);return stream.readframes(stream.getnframes())

    def test_all_six_export_channels_match_independent_original_samples_without_downmix(self):
        for index,alias in enumerate(self.aliases):
            with self.subTest(channel=index):
                actual=self.audio(self.p,alias['id'],'channel-'+str(index))
                expected=array.array('h',(v for sample in self.channels[index] for v in (sample,sample)))
                if sys.byteorder!='little':expected.byteswap()
                self.assertEqual(actual,expected.tobytes())

    def test_interpreted_subclip_reverse_ramp_fades_and_gain_match_independent_mono_source(self):
        index=4;source=self.root/'mono.wav';data=array.array('h',self.channels[index])
        if sys.byteorder!='little':data.byteswap()
        with wave.open(str(source),'wb') as stream:stream.setparams((1,2,48000,0,'NONE',''));stream.writeframes(data.tobytes())
        p=copy.deepcopy(self.p) # Child interpretation is deliberately independent of its physical parent.
        alias=p['media'][self.aliases[index]['id']];alias.update(duration=1.4,sub_in=.2,interpret_fps=15)
        p['media']['oracle']={**copy.deepcopy(alias),'id':'oracle','path':str(source),'subclip_of':None,'channels':1,'audio_streams':[{'index':0,'channels':1,'sample_rate':48000}]};p['media']['oracle'].pop('audio_alias')
        effects={'in_':.2,'out':1.2,'reverse':True,'time_remap':[{'t':0,'v':1},{'t':.4,'v':2}],
            'audio':{'maintain_pitch':False,'fade_in':.08,'fade_out':.05,'gain_db':-.005},'keyframes':{'audio.gain_db':[{'t':0,'v':-4},{'t':.5,'v':-2}]}}
        for pitch in (False,True):
            with self.subTest(maintain_pitch=pitch):
                effects['audio']['maintain_pitch']=pitch
                actual=self.audio(p,alias['id'],'retimed-'+str(pitch),**effects);expected=self.audio(p,'oracle','oracle-'+str(pitch),**effects)
                a=array.array('h');a.frombytes(actual);b=array.array('h');b.frombytes(expected)
                self.assertEqual(len(a),len(b));self.assertGreater(len(actual),10000)
                print('retimed channel mono oracle',pitch,'max_s16_residual',max(abs(x-y) for x,y in zip(a,b)))
                self.assertEqual(actual,expected)

    def test_multi_stream_source_selects_first_stream_and_preserves_discrete_samples(self):
        path=self.root/'two-streams.mka'
        subprocess.run(['ffmpeg','-v','error','-i',str(self.source),'-f','lavfi','-i','sine=frequency=1500:duration=1:sample_rate=48000',
            '-map','0:a:0','-map','1:a:0','-c:a','pcm_s16le',str(path)],check=True,capture_output=True)
        p=copy.deepcopy(self.p);p['media']['m']['path']=str(path)
        for media in p['media'].values():
            media['audio_streams']=[{'index':0,'channels':6,'sample_rate':48000},{'index':1,'channels':1,'sample_rate':48000}]
        actual=self.audio(p,self.aliases[5]['id'],'first-stream')
        expected=array.array('h',(v for sample in self.channels[5] for v in (sample,sample)))
        if sys.byteorder!='little':expected.byteswap()
        self.assertEqual(actual,expected.tobytes())

    def prepare(self,index):
        alias=self.aliases[index];effective=source_commands.alias_source(self.p,alias)
        class Task:
            id='channel-preparation';holder={}
            def check(self):pass
            def progress(self,*args):pass
            def publish(self,fn):return fn()
        payload={'info':effective,'path':str(self.source),'media_id':alias['id'],'stamp':source_stamp(effective)}
        def update(_,fields):self.p['media'][alias['id']].update(copy.deepcopy(fields));return True
        media_preparation.prepare(str(self.root),payload,Task(),lambda p:True,update)
        return self.p['media'][alias['id']]

    def test_real_aac_proxy_and_wave_select_same_channel_and_original_fallback_refuses(self):
        alias=self.aliases[4]
        for query in ({},{'proxy':'0'},{'proxy':'1'}):
            with self.assertRaises(media_preview.PreviewError):media_preview.resolve(self.root,self.p,alias['id'],query,workspace='w',project_id='p')
        ready=self.prepare(4);self.assertEqual(ready['proxy_info']['channel_index'],4)
        self.assertTrue((self.root/ready['wave'].lstrip('/')).is_file())
        status=media_preview.describe(self.root,self.p,alias['id']);self.assertEqual(status['proxy_state'],'ready')
        with self.assertRaises(media_preview.PreviewError):media_preview.resolve(self.root,self.p,alias['id'],{'proxy':'0'},workspace='w',project_id='p')
        path,_,_=media_preview.resolve(self.root,self.p,alias['id'],{'proxy':'1'},workspace='w',project_id='p')
        pcm=subprocess.run(['ffmpeg','-v','error','-i',path,'-f','f32le','-acodec','pcm_f32le','-'],check=True,capture_output=True).stdout
        samples=array.array('f');samples.frombytes(pcm)
        if sys.byteorder!='little':samples.byteswap()
        left=list(samples[0:96000:2]);right=list(samples[1:96000:2])
        print('lossy AAC dual mono max_stereo_residual',max(abs(x-y) for x,y in zip(left,right)))
        self.assertLess(max(abs(x-y) for x,y in zip(left,right)),.001)
        def corr(values):
            a=left[2000:45000];b=values[2000:45000]
            return sum(x*y for x,y in zip(a,b))/math.sqrt(sum(x*x for x in a)*sum(y*y for y in b))
        self.assertGreater(corr(self.channels[4]),.99);self.assertLess(abs(corr(self.channels[0])),.02)
        ready['proxy_info']['channel_index']=0
        self.assertEqual(media_preview.describe(self.root,self.p,alias['id'])['proxy_state'],'invalid')
        with self.assertRaises(media_preview.PreviewError):media_preview.resolve(self.root,self.p,alias['id'],{'proxy':'1'},workspace='w',project_id='p')
        ready['proxy_info']['channel_index']=4
        (self.root/ready['proxy'].lstrip('/')).unlink()
        with self.assertRaises(media_preview.PreviewError):media_preview.resolve(self.root,self.p,alias['id'],{'proxy':'1'},workspace='w',project_id='p')


if __name__=='__main__':unittest.main()

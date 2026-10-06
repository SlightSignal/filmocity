"""Real SDR video delivery matrix/tags; not native color/display qualification."""
import array
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import render
import delivery_color as delivery
from preflight import inspect_resources
from render_context import RenderContext

PATCHES=[(0,0,0),(255,255,255),(128,128,128),(255,0,0),(0,255,0),(0,0,255),(217,37,38),(0,161,95),(73,42,187),(232,146,91)]


def fixture(root):
    source=root/'RGB patches É.png';image=Image.new('RGB',(320,64))
    for i,rgb in enumerate(PATCHES):image.paste(rgb,(i*32,0,(i+1)*32,64))
    image.save(source)
    return {'id':'p','media':{'m':{'id':'m','path':str(source),'is_image':True,'has_video':True,'has_audio':False}},
        'sequences':[{'id':'s','name':'Delivery patches','width':320,'height':64,'fps':24,'duration':.125,
        'tracks':[{'id':'V1','kind':'video','index':1,'clips':[{'id':'c','media_id':'m','start':0,'in_':0,'out':.125}]}]}]}


def probe(path):return json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_streams','-of','json',str(path)]))['streams'][0]


def samples(path,ten_bit=False):
    fmt='yuv422p10le' if ten_bit else 'yuv420p'
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt',fmt,'pipe:1'])
    values=array.array('H' if ten_bit else 'B',raw)
    if ten_bit and sys.byteorder!='little':values.byteswap()
    width,height=probe(path)['width'],probe(path)['height'];csize=width//2*(height if ten_bit else height//2)
    return [[values[height//2*width+x],values[width*height+(height//2 if ten_bit else height//4)*(width//2)+x//2],
             values[width*height+csize+(height//2 if ten_bit else height//4)*(width//2)+x//2]] for x in [int((i+.5)*width/len(PATCHES)) for i in range(len(PATCHES))]]


def reference(rgb,ten_bit=False):
    # ITU BT.709 nonconstant-luminance equations, independent of FFmpeg filters.
    r,g,b=[v/255 for v in rgb];y=.2126*r+.7152*g+.0722*b;scale=4 if ten_bit else 1
    return [round(scale*(16+219*y)),round(scale*(128+224*(b-y)/1.8556)),round(scale*(128+224*(r-y)/1.5748))]


def load_qa():
    tree=ast.parse((ROOT/'backend/server.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_qa')
    env=dict(subprocess=subprocess,json=json,os=os,hashlib=hashlib,PLATFORM_RULES={})
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/'backend/server.py'),'exec'),env)
    return env['render_qa']


class DeliveryColor(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="Filmocity delivery É's ");self.root=Path(self.temp.name)
        self.project=fixture(self.root);self.before=copy.deepcopy(self.project);self.source=Path(self.project['media']['m']['path']);self.source_hash=hashlib.sha256(self.source.read_bytes()).hexdigest()
    def tearDown(self):
        self.assertEqual(self.project,self.before);self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.source_hash)
        self.assertFalse(list(self.root.glob('filmocity-render-*')));self.assertFalse(list(self.root.glob('*.part.*')));self.temp.cleanup()
    def export(self,name,preset,engine=render):
        out=self.root/name
        with RenderContext(scratch_parent=str(self.root)) as context:engine.render(self.project,'s',str(out),preset,context=context)
        return out
    def check(self,path,preset,tolerance=3):
        stream=probe(path);self.assertEqual(delivery.inspect(stream,preset)['status'],'matched',stream)
        ten=preset.get('format')=='prores'
        self.assertEqual(stream['pix_fmt'],'yuv422p10le' if ten else 'yuv420p')
        for actual,rgb in zip(samples(path,ten),PATCHES):self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,reference(rgb,ten))),tolerance,(rgb,actual,reference(rgb,ten)))
        decoded=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
        for i,rgb in enumerate(PATCHES):
            at=3*(stream['height']//2*stream['width']+int((i+.5)*stream['width']/len(PATCHES)))
            self.assertLessEqual(max(abs(a-b) for a,b in zip(decoded[at:at+3],rgb)),4,(rgb,decoded[at:at+3]))

    def test_h264_openh264_encodes_rec709_matrix_and_matching_tags(self):
        preset={'vcodec':'libopenh264','bitrate':'10M'};self.check(self.export('h264.mp4',preset),preset)

    def test_h264_bitstream_tags_survive_without_the_mp4_container(self):
        preset={'vcodec':'libopenh264'};path=self.export('bitstream.mp4',preset);elementary=self.root/'video.h264'
        subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-c','copy','-f','h264',str(elementary)],capture_output=True,check=True)
        trace=subprocess.run(['ffmpeg','-hide_banner','-v','info','-i',str(elementary),'-map','0:v','-c','copy','-bsf:v','trace_headers','-f','null','-'],capture_output=True,text=True,check=True)
        self.assertEqual(delivery.inspect_headers(trace.stderr)['status'],'matched')

    def test_prores_10bit_uses_709_coefficients_and_matching_tags(self):
        preset={'format':'prores'};self.check(self.export('prores.mov',preset),preset,6)

    def test_vp9_color_tags_and_decoded_samples(self):
        preset={'format':'webm','crf':10};self.check(self.export('vp9.webm',preset),preset)

    def test_av1_aom_color_tags_and_decoded_samples(self):
        # Select the existing libaom branch explicitly for this small fixture.
        # SVT's default parallelism is not a bounded test on large-CPU hosts.
        preset={'format':'av1','crf':10}
        with patch.object(render,'has_encoder',lambda name:False):self.check(self.export('av1.mp4',preset),preset)

    def test_resize_and_range_still_convert_after_compositing(self):
        self.project['sequences'][0].update(in_point=1/24,out_point=3/24);self.before=copy.deepcopy(self.project)
        preset={'vcodec':'libopenh264','out_w':640,'out_h':128,'range':True,'fit':'pad'}
        path=self.export('resize.mp4',preset);self.check(path,preset)
        self.assertEqual((probe(path)['width'],probe(path)['height'],probe(path)['nb_frames']),(640,128,'2'))

    def test_nested_rgb_and_flat_color_are_identical_at_delivery(self):
        preset={'format':'prores'};flat=self.export('flat.mov',preset)
        child=copy.deepcopy(self.project['sequences'][0]);child['id']='child';self.project['sequences'].append(child)
        self.project['sequences'][0]['tracks'][0]['clips']=[{'id':'n','sequence_id':'child','start':0,'in_':0,'out':.125}];self.before=copy.deepcopy(self.project)
        nested=self.export('nested.mov',preset);self.check(nested,preset,6);self.assertEqual(samples(flat,True),samples(nested,True))

    def test_hdr_input_normalization_reaches_tagged_sdr_delivery_once(self):
        from test_source_color import SourceColor,mapped_gray
        f=SourceColor();f.root=self.root;f.sources={};project,codes,_,_=f.fixture()
        path=self.root/'hdr-to-sdr.mp4';before=copy.deepcopy(project)
        render.render(project,'s',str(path),{'vcodec':'libopenh264','bitrate':'10M'})
        stream=probe(path);self.assertEqual(delivery.inspect(stream,{})['status'],'matched');self.assertFalse(stream.get('side_data_list'))
        decoded=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
        for i,code in enumerate(codes):
            at=3*(16*stream['width']+i*16+8);self.assertLessEqual(max(abs(v-mapped_gray(code)) for v in decoded[at:at+3]),3)
        self.assertEqual(project,before);self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in f.sources.items()))

    def test_still_and_rgb_lossless_intermediate_preserve_rgb_bytes(self):
        png=self.root/'still.png';render.render_frame(self.project,'s',0,str(png));self.assertEqual(Image.open(png).convert('RGB').tobytes(),Image.open(self.source).tobytes())
        lossless=self.export('internal.mkv',{'vcodec':'ffv1'})
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(lossless),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
        self.assertEqual(raw,Image.open(self.source).tobytes());self.assertEqual(delivery.plan({'vcodec':'ffv1'})['mode'],'not_applicable')

    def test_audio_and_palette_paths_do_not_receive_video_delivery_tags(self):
        for preset in ({'format':'audio'},{'format':'gif'},{'format':'png_sequence'}):
            with self.subTest(preset=preset),RenderContext(scratch_parent=str(self.root)) as context:
                command,graph=render.build_command(self.project,'s',str(self.root/'out'),preset,context=context)
                self.assertNotIn('-color_trc',command);self.assertNotIn('vdeliveryrgb',graph)

    def test_legacy_policy_is_visible_and_does_not_falsely_claim_709(self):
        preset={'color_processing':'legacy'};report=inspect_resources(self.project,'s',preset)
        self.assertEqual(report['delivery_color']['mode'],'legacy_unmanaged');self.assertTrue(any(i['code']=='legacy_delivery_color' for i in report['issues']))
        with RenderContext(scratch_parent=str(self.root)) as context:
            command,graph=render.build_command(self.project,'s',str(self.root/'legacy.mp4'),preset,context=context)
            self.assertNotIn('-color_trc',command);self.assertNotIn('out_color_matrix=bt709',graph)

    def test_malformed_codec_and_inconsistent_headers_are_rejected(self):
        for codec in (True,False,{},[],42):
            with self.subTest(codec=codec):self.assertFalse(inspect_resources(self.project,'s',{'vcodec':codec})['ok'])
        values={'vui_parameters_present_flag':1,'video_signal_type_present_flag':1,'colour_description_present_flag':1,'video_full_range_flag':0,'colour_primaries':1,'transfer_characteristics':1,'matrix_coefficients':1}
        trace='\n'.join(f'[trace_headers @ fixture] 0 {k} {v:08b} = {v}' for k,v in values.items())
        self.assertEqual(delivery.inspect_headers(trace)['status'],'matched')
        for bad in ('',trace+'\n[trace_headers @ fixture] 0 vui_parameters_present_flag 0 = 0',trace.replace('matrix_coefficients 00000001 = 1','matrix_coefficients 00001001 = 9')):
            self.assertEqual(delivery.inspect_headers(bad)['status'],'mismatch')

    def test_hardware_and_software_video_commands_share_the_delivery_boundary(self):
        for fmt,codec in [('h264','libx264'),('hevc','libx265'),('h264','h264_nvenc'),('hevc','hevc_qsv'),('h264','h264_amf')]:
            with self.subTest(codec=codec),RenderContext(scratch_parent=str(self.root)) as context:
                cmd,graph=render.build_command(self.project,'s',str(self.root/'out.mp4'),{'format':fmt,'vcodec':codec},context=context)
                self.assertIn('out_color_matrix=bt709',graph);self.assertEqual(cmd[cmd.index('-colorspace')+1],'bt709');self.assertEqual(cmd[cmd.index('-c:v')+1],codec)

    def test_qa_retains_expected_and_observed_color_and_flags_missing_tags(self):
        preset={'vcodec':'libopenh264'};path=self.export('qa.mp4',preset);qa=load_qa()
        report=qa(str(path),preset);self.assertEqual(report['delivery_color']['status'],'matched');self.assertEqual(report['color_range'],'tv')
        real=subprocess.run
        def missing(command,**kw):
            result=real(command,**kw)
            if command[0]=='ffprobe':
                document=json.loads(result.stdout);video=next(s for s in document['streams'] if s['codec_type']=='video');video.pop('color_range',None);video['color_space']='bt2020nc';result.stdout=json.dumps(document)
            return result
        with patch.object(subprocess,'run',missing):report=qa(str(path),preset)
        self.assertEqual(report['status'],'error');self.assertEqual(report['delivery_color']['status'],'mismatch');self.assertEqual(set(report['delivery_color']['mismatches']),{'color_range','color_space'})
        self.assertTrue(any('Delivery color metadata' in f for f in report['flags']))

    def test_audio_qa_without_a_preset_has_no_picture_contract(self):
        path=self.export('audio.wav',{'format':'audio'});qa=load_qa()
        report=qa(str(path));self.assertIn(report['status'],('checked','warnings'));self.assertEqual(report['delivery_color']['status'],'not_applicable')
        # An explicitly requested video whose output lost picture is different.
        report=qa(str(path),{});self.assertEqual(report['status'],'error');self.assertEqual(report['delivery_color']['status'],'mismatch')

    def test_incremental_encode_and_reuse_keep_conversion_and_tags(self):
        preset={'full':True,'bitrate':'10M'};real=render._execute_ffmpeg_owned;encodes=[]
        def adapter(command,**kw):
            command=list(command)
            if '-c:v' in command and command[command.index('-c:v')+1]=='libx264':
                command[command.index('-c:v')+1]='libopenh264'
                for flag in ('-preset','-crf','-keyint_min','-sc_threshold'):
                    if flag in command:i=command.index(flag);del command[i:i+2]
                encodes.append(command)
            return real(command,**kw)
        with patch.object(render,'_execute_ffmpeg_owned',adapter):
            for i in range(2):
                out=self.root/f'incremental{i}.mp4'
                with RenderContext(scratch_parent=str(self.root)) as context:
                    _,stats=render.render_incremental(self.project,'s',str(out),preset,cache_dir=str(self.root/'cache'),context=context)
                self.check(out,preset);self.assertEqual(stats['reused'],i)
        self.assertEqual(len(encodes),1);self.assertIn('out_color_matrix=bt709',encodes[0][encodes[0].index('-filter_complex')+1])

    def test_incremental_hevc_defaults_to_hevc_and_rejects_a_cross_family_codec(self):
        # Inspect real orchestration commands; this host cannot encode HEVC.
        calls=[]
        def execute(command,**kw):
            calls.append(list(command));Path(command[-1]).write_bytes(b'controlled command-only output')
        with patch.object(render,'_run_ffmpeg',execute),RenderContext(scratch_parent=str(self.root)) as context:
            render.render_incremental(self.project,'s',str(self.root/'hevc.mp4'),{'format':'hevc','full':True},cache_dir=str(self.root/'hevc-cache'),context=context)
        segment=next(c for c in calls if '-c:v' in c)
        self.assertEqual(segment[segment.index('-c:v')+1],'libx265');self.assertIn('hevc_metadata=',segment[segment.index('-bsf:v')+1])
        previous=self.root/'previous.mp4';previous.write_bytes(b'keep prior delivery')
        with self.assertRaisesRegex(ValueError,'does not encode HEVC'):
            render.render_incremental(self.project,'s',str(previous),{'format':'hevc','vcodec':'libx264'})
        self.assertEqual(previous.read_bytes(),b'keep prior delivery')


if __name__=='__main__':unittest.main()

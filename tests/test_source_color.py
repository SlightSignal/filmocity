"""Tagged 10-bit source conversion with independent scalar reference values.

PQ equations follow ITU-R BT.2100; Hable follows FFmpeg's documented operator.
Gray fixtures cannot qualify gamut mapping, camera looks or native displays.
"""
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
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'backend'))
import render
import source_color as color
from media_metadata import summarize
from preflight import inspect_resources, ResourceError
from proxy_media import options as proxy_options
from render_context import RenderContext


def pq_encode(nits):
    x=(nits/10000)**(2610/16384)
    return ((3424/4096+2413/128*x)/(1+2392/128*x))**(2523/32)


def pq_decode(value):
    x=value**(32/2523)
    return 10000*(max(x-3424/4096,0)/(2413/128-2392/128*x))**(16384/2610)


def hable(x): return (x*(.15*x+.05)+.004)/(x*(.15*x+.5)+.06)-1/15
# zimg's display-referred BT.709 target uses ideal BT.1886, not the camera OETF.
def gamma709(x): return x**(1/2.4)
def hlg_decode(value):
    scene=value*value/3 if value<=.5 else (math.exp((value-.55991073)/.17883277)+.28466892)/12
    return 1000*scene**1.2

def mapped_hlg_gray(code, peak=1000): return round(255*gamma709(max(0,min(1,hable(hlg_decode((code-64)/876)/100)/hable(peak/100)))))
def mapped_gray(code, peak=1000): return round(255*gamma709(max(0,min(1,hable(pq_decode((code-64)/876)/100)/hable(peak/100)))))


class SourceColor(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="Filmocity color É's "); self.root=Path(self.temp.name); self.sources={}
    def tearDown(self):
        self.assertTrue(all(hashlib.sha256(path.read_bytes()).hexdigest()==value for path,value in self.sources.items()))
        self.assertFalse(list(self.root.glob('filmocity-render-*'))); self.temp.cleanup()

    def fixture(self, transfer='smpte2084', values=None, colors=None, levels='tv'):
        values=values or [pq_encode(n) for n in (0,1,10,18,100,400,1000,2000)]
        codes=[round((64+876*v) if levels=='tv' else 1023*v) for v in values]; width,height=len(codes)*16,32
        y=[codes[x//16] for _ in range(height) for x in range(width)]
        u=v=[512]*(width*height)
        if colors:
            # BT.2020 NCL signal equations; each strip has constant chroma.
            planes=[[],[],[]]
            for r,g,b in colors:
                luma=.2627*r+.678*g+.0593*b
                for plane,value in zip(planes,(64+876*luma,512+896*(b-luma)/1.8814,512+896*(r-luma)/1.4746)):plane.append(round(value))
            width=len(colors)*16
            y,u,v=[[plane[x//16] for _ in range(height) for x in range(width)] for plane in planes]
            codes=planes[0]
        raw=array.array('H',y+u+v)
        if sys.byteorder!='little':raw.byteswap()
        path=self.root/(transfer+'.mkv')
        command=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','yuv444p10le','-s',f'{width}x{height}','-framerate','24','-i','pipe:0','-frames:v','3',
            '-vf',f'setparams=color_primaries=bt2020:color_trc={transfer}:colorspace=bt2020nc:range={"limited" if levels=="tv" else "full"}',
            '-c:v','ffv1','-color_primaries','bt2020','-color_trc',transfer,'-colorspace','bt2020nc','-color_range',levels,str(path)]
        subprocess.run(command,input=raw.tobytes()*3,capture_output=True,check=True,timeout=20)
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]));media={'id':'m','path':str(path),**summarize(probe)}
        self.assertEqual(media['color_transfer'],transfer,'Fixture must carry observed transfer metadata')
        self.assertEqual(media['color_primaries'],'bt2020','Fixture must carry observed primary metadata')
        project={'id':'p','media':{'m':media},'sequences':[{'id':'s','name':'Color','width':width,'height':height,'fps':24,'duration':.125,'tracks':[{'id':'V1','kind':'video','index':1,'clips':[{'id':'c','media_id':'m','start':0,'in_':0,'out':.125}]}]}]}
        self.sources[path]=hashlib.sha256(path.read_bytes()).hexdigest();return project,codes,probe,command

    def pixels(self, project):
        before=copy.deepcopy(project);path=self.root/'frame.png'
        with RenderContext(scratch_parent=str(self.root),stall_timeout=20) as context:render.render_frame(project,'s',0,str(path),context=context)
        self.assertEqual(project,before)
        with Image.open(path) as image:return [image.convert('RGB').getpixel((i*16+8,16)) for i in range(image.width//16)]

    def test_bt2020_sdr_is_not_hdr_even_with_a_stale_hdr_flag(self):
        project,codes,_,_=self.fixture('bt709',[0,.1,.25,.5,.75,1]);m=project['media']['m']
        self.assertFalse(m['hdr']);self.assertEqual(m['dynamic_range'],'sdr');self.assertTrue(m['wide_gamut'])
        m['hdr']=True;policy=color.plan(m);self.assertEqual(policy['conversion'],'bt2020_sdr_to_bt709')
        for actual,code in zip(self.pixels(project),codes):self.assertLessEqual(max(abs(v-round(255*(code-64)/876)) for v in actual),3)

    def test_real_pq_gray_strip_matches_independent_transfer_and_hable(self):
        project,codes,_,_=self.fixture()
        self.assertEqual(color.plan(project['media']['m'])['peak_origin'],'assumed')
        for actual,code in zip(self.pixels(project),codes):self.assertLessEqual(max(abs(v-mapped_gray(code)) for v in actual),3,(actual,mapped_gray(code)))

    def test_bt2020_sdr_colored_patches_convert_primaries_before_clipping(self):
        patches=[(.7,.3,.2),(.2,.6,.4),(.3,.2,.7),(.8,.6,.4),(1,0,0),(0,1,0),(0,0,1)]
        project,_,_,_=self.fixture('bt709',colors=patches)
        # D65 BT.2020 to BT.709 linear-light primary matrix, independently
        # applied to the known display-referred input values (BT.1886).
        matrix=((1.660491,-.587641,-.072850),(-.124550,1.132900,-.008350),(-.018151,-.100579,1.118730))
        for actual,source in zip(self.pixels(project),patches):
            expected=[round(255*max(0,min(1,sum(a*b**2.4 for a,b in zip(row,source))))**(1/2.4)) for row in matrix]
            self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)),4,(actual,expected))

    def test_peak_override_changes_pixels_and_cache_identity_without_editing_source(self):
        project,codes,_,_=self.fixture();seq=project['sequences'][0]
        before=render.chunk_key(project,seq,{})
        project['media']['m']['hdr_peak_nits']=400
        self.assertNotEqual(before,render.chunk_key(project,seq,{}))
        for actual,code in zip(self.pixels(project),codes):self.assertLessEqual(max(abs(v-mapped_gray(code,400)) for v in actual),3)

    def test_full_range_pq_is_not_expanded_as_limited_range(self):
        project,codes,_,_=self.fixture(levels='pc');self.assertEqual(project['media']['m']['color_range'],'pc')
        for actual,code in zip(self.pixels(project),codes):
            expected=round(255*gamma709(max(0,min(1,hable(pq_decode(code/1023)/100)/hable(10)))))
            self.assertLessEqual(max(abs(v-expected) for v in actual),3)

    def test_hlg_uses_its_own_transfer_and_produces_ordered_bounded_grays(self):
        project,codes,_,_=self.fixture('arib-std-b67',[0,.1,.25,.5,.75,1]);m=project['media']['m']
        self.assertEqual(color.plan(m)['dynamic_range'],'hlg');actual=self.pixels(project)
        self.assertEqual(actual[0],(0,0,0));self.assertEqual(actual[-1],(255,255,255))
        self.assertEqual([p[0] for p in actual],sorted(p[0] for p in actual))
        for p,code in zip(actual,codes):
            self.assertLessEqual(max(p)-min(p),1)
            self.assertLessEqual(max(abs(v-mapped_hlg_gray(code)) for v in p),3)
        self.assertNotEqual([p[0] for p in actual],[mapped_gray(code) for code in codes])

    def test_missing_filters_refuse_export_before_replacing_existing_output(self):
        project,_,_,_=self.fixture();out=self.root/'delivered.mkv';out.write_bytes(b'previous delivery')
        with patch.object(render,'has_filter',lambda _:False):
            report=inspect_resources(project,'s',{});self.assertFalse(report['ok']);self.assertTrue(any('No brightness-curve' in i['message'] for i in report['issues']))
            with self.assertRaises(ResourceError):render.render(project,'s',str(out),{'vcodec':'ffv1'})
        self.assertEqual(out.read_bytes(),b'previous delivery')

    def test_missing_transfer_primaries_matrix_or_range_cannot_guess_pq(self):
        project,_,_,_=self.fixture()
        for key in ('color_transfer','color_primaries','color_space','color_range'):
            value=copy.deepcopy(project);value['media']['m'].pop(key)
            self.assertFalse(inspect_resources(value,'s',{})['ok'],key)
            with self.assertRaises(ValueError):color.plan(value['media']['m'])

    def test_measured_light_metadata_is_retained_and_peak_priority_is_explicit(self):
        m=summarize({'streams':[{'codec_type':'video','color_transfer':'smpte2084','color_primaries':'bt2020','color_space':'bt2020nc','color_range':'tv','side_data_list':[{'side_data_type':'Content light level metadata','max_content':800},{'side_data_type':'Mastering display metadata','max_luminance':'10000000/10000'}]}]})
        self.assertEqual((m['hdr_max_cll'],m['hdr_mastering_peak_nits']),(800,1000))
        self.assertEqual((color.plan(m)['peak_nits'],color.plan(m)['peak_origin']),(800,'max_cll'))
        del m['hdr_max_cll'];self.assertEqual(color.plan(m)['peak_origin'],'mastering_display')
        m['hdr_peak_nits']=2000;self.assertEqual(color.plan(m)['peak_origin'],'override')
        m.pop('hdr_peak_nits');m['hdr_mastering_peak_nits']=50000;self.assertEqual(color.plan(m)['peak_origin'],'assumed')

    def test_invalid_peak_fails_and_explicit_camera_lut_prevents_double_conversion(self):
        project,_,_,_=self.fixture();m=project['media']['m']
        for invalid in (None,True,0,99,10001,10**400,float('inf'),float('nan'),'bad'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):color.plan({**m,'hdr_peak_nits':invalid})
        chosen={**m,'input_transform':'slog3'}
        self.assertEqual(color.plan(chosen)['conversion'],'camera_log_lut');self.assertEqual(render.hdr_to_sdr_chain(chosen),[])
        self.assertEqual(len(render.input_transform_chain(chosen)),1)
        with self.assertRaisesRegex(ValueError,'Unknown camera-log'):color.plan({**m,'input_transform':'invalid'})

    def test_subclips_follow_current_parent_color_settings(self):
        project,codes,_,_=self.fixture();m=project['media']['m'];m['hdr_peak_nits']=400
        project['media']['sub']={**m,'id':'sub','subclip_of':'m','hdr_peak_nits':4000,'input_transform':'slog3'}
        project['sequences'][0]['tracks'][0]['clips'][0]['media_id']='sub'
        for actual,code in zip(self.pixels(project),codes):self.assertLessEqual(max(abs(v-mapped_gray(code,400)) for v in actual),3)

    def test_audio_only_track_does_not_require_video_color_conversion(self):
        project,_,_,_=self.fixture();project['media']['m'].pop('color_transfer');project['sequences'][0]['tracks'][0]['kind']='audio'
        self.assertTrue(inspect_resources(project,'s',{})['ok'])

    def test_audio_delivery_with_nested_hdr_picture_does_not_require_color_filters(self):
        project,_,_,_=self.fixture();project['media']['m'].pop('color_transfer')
        child=copy.deepcopy(project['sequences'][0]);child['id']='child';project['sequences'].append(child)
        project['sequences'][0]['tracks'][0]['clips']=[{'id':'nested','sequence_id':'child','start':0,'in_':0,'out':.125}]
        target=self.root/'audio.wav';before=copy.deepcopy(project)
        with patch.object(render,'has_filter',lambda _:False),RenderContext(scratch_parent=str(self.root)) as context:
            render.render(project,'s',str(target),{'format':'audio','acodec':'wav'},context=context)
            self.assertEqual(len(context.nested_completed),1)
            nested=next(iter(context.nested_completed.values()))['path']
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',nested]))
            self.assertEqual([s['codec_name'] for s in probe['streams']],['pcm_f32le'])
        self.assertEqual(project,before)
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(target)]))
        self.assertEqual([s['codec_type'] for s in probe['streams']],['audio']);self.assertAlmostEqual(float(probe['streams'][0]['duration']),.125,5)

    def test_wide_gamut_sdr_proxy_is_not_silently_allowed_by_corrected_hdr_flag(self):
        project,_,_,_=self.fixture('bt709');self.assertFalse(project['media']['m']['hdr'])
        with self.assertRaisesRegex(ValueError,'Wide-gamut'):proxy_options(project['media']['m'])

    def test_preflight_exposes_actual_peak_and_conversion_warning(self):
        project,_,_,_=self.fixture();m=project['media']['m'];m['hdr_peak_nits']=750
        report=inspect_resources(project,'s',{});self.assertTrue(report['ok'])
        row=next(i for i in report['issues'] if i['code']=='source_color_conversion')
        self.assertEqual(row['severity'],'warning');self.assertEqual(row['color_policy']['peak_nits'],750);self.assertIn('750-nit',row['message'])


if __name__=='__main__':unittest.main()

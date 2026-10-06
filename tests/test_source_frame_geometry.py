"""Real Source-frame exports match independent display geometry and source clocks."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import frame_source
import media_metadata
import render
from render_context import RenderContext
from picture_geometry import display_size
import test_exact_frame as exact


class SourceFrameGeometry(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='Filmocity Source aspect É ');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.image=Image.new('RGB',(64,48),(200,0,0));self.image.paste((0,200,0),(32,0,64,48));self.image.save(self.root/'bands.png')

    def command(self,argv):
        value=subprocess.run(argv,capture_output=True,timeout=30);self.assertEqual(value.returncode,0,value.stderr.decode(errors='replace'));return value.stdout

    def media(self,sar='2/1',rotation=0):
        name=sar.replace('/','-')+'-'+str(rotation);base=self.root/(name+'-base.mov')
        self.command(['ffmpeg','-v','error','-y','-loop','1','-framerate','10','-i',str(self.root/'bands.png'),
            '-t','1','-vf','setsar='+sar,'-c:v','png','-pix_fmt','rgb24',str(base)])
        source=base
        if rotation:
            source=self.root/(name+'.mov');self.command(['ffmpeg','-v','error','-y','-display_rotation:v:0',str(rotation),'-i',str(base),'-c','copy',str(source)])
        probe=json.loads(self.command(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(source)]))
        value=media_metadata.summarize(probe);value.update(id='m',path=str(source),name='Known bands');return value

    def output(self,project,identity='m',t=.2):
        before=copy.deepcopy(project);prepared=frame_source.source_project(project,identity);target=self.root/'frame.png'
        with RenderContext(scratch_parent=str(self.root)) as context:render.render_frame(prepared,'source',t,str(target),context=context)
        self.assertEqual(project,before)
        with Image.open(target) as frame:return frame.convert('RGB').copy(),prepared

    def test_wide_pixels_and_quarter_turns_match_independent_oriented_bands(self):
        for rotation,size in [(0,(128,48)),(90,(48,128)),(180,(128,48)),(270,(48,128))]:
            with self.subTest(rotation=rotation):
                media=self.media(rotation=rotation);digest=hashlib.sha256(Path(media['path']).read_bytes()).hexdigest()
                actual,prepared=self.output({'media':{'m':media},'sequences':[]})
                expected=self.image.resize((128,48))
                if rotation:expected=expected.rotate(rotation,expand=True)
                self.assertEqual(actual.size,size);self.assertEqual(prepared['sequences'][0]['fps'],10)
                for x,y in [(1,1),(size[0]-2,size[1]-2),(size[0]//4,size[1]//4)]:
                    self.assertLessEqual(max(abs(a-b) for a,b in zip(actual.getpixel((x,y)),expected.getpixel((x,y)))),1)
                self.assertEqual(hashlib.sha256(Path(media['path']).read_bytes()).hexdigest(),digest)

    def test_narrow_square_and_fractional_pixels_keep_correct_output_edges(self):
        for sar,size in [('1/2',(32,48)),('1/1',(64,48)),('4/3',(85,48))]:
            with self.subTest(sar=sar):
                actual,_=self.output({'media':{'m':self.media(sar=sar)},'sequences':[]})
                self.assertEqual(actual.size,size)
                self.assertEqual(actual.getpixel((1,1)),(200,0,0));self.assertEqual(actual.getpixel((size[0]-2,size[1]-2)),(0,200,0))

    def test_interpreted_subclip_exports_original_native_clock_and_display_geometry(self):
        media=self.media();media.update(interpret_fps=25,native_duration=1,duration=.4)
        sub={**media,'id':'sub','subclip_of':'m','sub_in':.1,'duration':.2}
        original={'media':{'m':media,'sub':sub},'sequences':[{'id':'unrelated','color':{'exposure':5}}]}
        picture,prepared=self.output(original,'sub',.9)
        self.assertEqual(picture.size,(128,48));self.assertEqual(set(prepared['media']),{'m'})
        self.assertNotIn('interpret_fps',prepared['media']['m']);self.assertEqual(prepared['sequences'][0]['tracks'][0]['clips'][0]['out'],1)
        self.assertEqual(original['media']['sub']['sub_in'],.1)

    def test_invalid_geometry_refuses_and_missing_pixel_aspect_has_explicit_shared_assumption(self):
        for changed in ({'sample_aspect_ratio':'0:1'},{'sample_aspect_ratio':'1:0'},{'rotation':45},{'width':True},{'width':32768}):
            media={'id':'m','has_video':True,'width':64,'height':48,'duration':1,'fps':30,**changed}
            with self.subTest(changed=changed),self.assertRaises(ValueError):frame_source.source_project({'media':{'m':media}},'m')
        warnings=[];self.assertEqual(display_size({'width':64,'height':48},warnings),(64,48));self.assertTrue(any('assume square pixels' in w for w in warnings))


class SourceFrameRouteGeometry(exact.FrameRoute):
    for _name in dir(exact.FrameRoute):
        if _name.startswith('test_'):locals()[_name]=None

    def test_actual_owned_source_route_returns_display_sized_png_without_project_edit(self):
        helper=SourceFrameGeometry('runTest');helper.setUp();self.addCleanup(helper.doCleanups)
        media=helper.media();project=self.env['load_project']();project['media']={'m':media};self.env['save_project'](project);before=self.raw();owner=self.current()
        response=self.env['frame'](sequence='ignored',t=.2,context=json.dumps(owner),media='m')
        with Image.open(io.BytesIO(response.body)) as picture:
            self.assertEqual(picture.size,(128,48));self.assertEqual(picture.convert('RGB').getpixel((1,1)),(200,0,0))
        self.assertEqual(response.options['headers']['X-Filmocity-Frame'],'2');self.assertEqual(self.raw(),before)
        self.assertFalse(self.env['read_undo_history']('a')['undo'])


if __name__=='__main__':unittest.main()

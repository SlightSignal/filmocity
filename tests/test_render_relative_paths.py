"""Relative media/resources/export paths keep their origin when fonts need cwd."""
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import render
from render_context import RenderContext


@contextmanager
def working_directory(path):
    previous=os.getcwd(); os.chdir(path)
    try: yield
    finally: os.chdir(previous)


class RelativeRenderPaths(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="Filmocity relative É's "); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        Image.new('RGB',(96,64),'navy').save(self.root/'source.png')
        Image.new('RGBA',(16,16),(255,200,30,220)).save(self.root/'logo.png')
        font=self.root/'É font.ttf'; shutil.copyfile(render.bundled_font(),font)
        (self.root/'identity.cube').write_text('LUT_3D_SIZE 2\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n'
            '0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n')
        self.project={'media':{'m':{'id':'m','path':str(self.root/'source.png'),'is_image':True,'has_video':True,'has_audio':False}},
            'sequences':[{'id':'s','name':'Relative fixture','width':96,'height':64,'fps':24,'duration':.5,
                'captions':[{'start':0,'end':.5,'text':'42% É'}], 'caption_style':{'font':str(font),'size':12,'borderw':0},
                'tracks':[{'id':'v','kind':'video','index':0,'clips':[{'id':'c','media_id':'m','start':0,'in_':0,'out':.5,
                    'color':{'lut':str(self.root/'identity.cube')},'title':{'text':'42% É','font':str(font),'size':12}}]}]}]}
        self.preset={'format':'h264','x264_preset':'ultrafast','loudnorm':False,
                     'watermark':{'path':str(self.root/'logo.png'),'scale':.15}}
    def relative(self):
        doc=copy.deepcopy(self.project); preset=copy.deepcopy(self.preset)
        doc['media']['m']['path']='source.png'
        clip=doc['sequences'][0]['tracks'][0]['clips'][0]
        clip['color']['lut']='identity.cube'; clip['title']['font']='É font.ttf'
        doc['sequences'][0]['caption_style']['font']='É font.ttf'
        preset['watermark']['path']='logo.png'
        return doc,preset
    def pixels(self,path):
        with Image.open(path) as image: return image.convert('RGB').tobytes()
    def test_relative_source_frame_output_and_lut_match_absolute_pixels_with_unicode_font(self):
        with working_directory(self.root):
            original=copy.deepcopy(self.project)
            render.render_frame(self.project,'s',.1,str(self.root/'absolute.png'))
            relative,_=self.relative(); before=copy.deepcopy(relative)
            render.render_frame(relative,'s',.1,'relative.png')
            self.assertEqual(self.pixels(self.root/'relative.png'),self.pixels(self.root/'absolute.png'))
            self.assertEqual(relative,before); self.assertEqual(self.project,original)
            self.assertFalse(list(self.root.glob('*.part.png')))
    def test_relative_resources_and_retained_command_run_from_owned_font_cwd(self):
        with working_directory(self.root):
            relative,preset=self.relative(); before=copy.deepcopy(relative)
            command,_=render.build_command(relative,'s','retained.mp4',preset)
            directory=command.context.root
            if os.name=='nt': self.assertEqual(command.cwd,directory); self.assertIsNotNone(directory)
            with command:
                for index,value in enumerate(command[:-1]):
                    if value=='-i': self.assertTrue(os.path.isabs(command[index+1]),command[index+1])
                self.assertEqual(command[-1],str(self.root/'retained.mp4'))
                render._run_ffmpeg(command)
                self.assertTrue((self.root/'retained.mp4').is_file())
            self.assertEqual(relative,before)
            self.assertFalse(Path(directory).exists())
    def test_relative_incremental_output_and_cache_publish_and_reuse_with_unicode_font(self):
        with working_directory(self.root):
            relative,preset=self.relative(); before=copy.deepcopy(relative)
            for attempt in range(2):
                result,stats=render.render_incremental(relative,'s',f'incremental-{attempt}.mp4',preset,cache_dir='cache')
                self.assertEqual(result,str(self.root/f'incremental-{attempt}.mp4'))
                self.assertTrue(Path(result).is_file()); self.assertEqual(stats['reused'],attempt)
            self.assertEqual(relative,before)
            self.assertFalse(list((self.root/'cache').glob('*.part.mp4')))
            self.assertFalse(list(self.root.glob('*.part.mp4')))
    def test_cancelled_relative_export_keeps_master_and_retires_font_scratch(self):
        with working_directory(self.root):
            relative,preset=self.relative(); master=self.root/'master.mp4'; master.write_bytes(b'previous master')
            holder={}; context=RenderContext(proc_holder=holder,scratch_parent=str(self.root))
            with self.assertRaisesRegex(RuntimeError,'cancelled'):
                with context:
                    render.render(relative,'s','master.mp4',preset,context=context,
                                  progress=lambda fraction: holder.update(cancelled=True))
            self.assertEqual(master.read_bytes(),b'previous master')
            self.assertNotIn('proc',holder)
            self.assertFalse(Path(context.root).exists())
            self.assertFalse(list(self.root.glob('*.part.mp4')))


if __name__=='__main__': unittest.main(verbosity=2)

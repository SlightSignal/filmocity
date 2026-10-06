"""Real title/caption pixels from identical fonts in Unicode and long paths."""
import copy
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


class PortableFonts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity fonts Émile's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.font = Path(render.bundled_font())
        still = self.root/'source.png'; Image.new('RGB', (320, 180), 'navy').save(still)
        self.project = {'media': {'m': {'id':'m','path':str(still),'is_image':True,'has_video':True}},
            'sequences':[{'id':'s','width':320,'height':180,'fps':24,
                'captions':[{'start':0,'end':1,'text':'Caption É 42%'}],
                'caption_style':{'size':16,'borderw':0},
                'tracks':[{'id':'v','kind':'video','index':0,'clips':[
                    {'id':'c','media_id':'m','start':0,'in_':0,'out':1}]},
                    {'id':'title','kind':'video','index':1,'clips':[
                    # Titles have their own clip; media clips take the media
                    # rendering branch even if they also contain a title field.
                    {'id':'t','start':0,'in_':0,'out':1,
                     'title':{'text':'Title AV fi\nSecond line','size':20,'y':-35}}]}]}]}

    def with_font(self, font):
        result = copy.deepcopy(self.project)
        sequence = result['sequences'][0]
        sequence['caption_style']['font'] = str(font)
        sequence['tracks'][1]['clips'][0]['title']['font'] = str(font)
        return result

    def pixels(self, project, label):
        output = self.root/(label+'.png')
        with RenderContext(scratch_parent=str(self.root)) as context:
            render.render_frame(project, 's', .25, str(output), context=context)
        with Image.open(output) as image: return image.convert('RGB').tobytes()

    def test_fixture_title_and_caption_each_change_rendered_pixels(self):
        project = self.with_font(self.font)
        baseline = self.pixels(project, 'both-text-layers')
        without_title = copy.deepcopy(project)
        without_title['sequences'][0]['tracks'].pop()
        self.assertNotEqual(self.pixels(without_title, 'without-title'), baseline)
        without_caption = copy.deepcopy(project)
        without_caption['sequences'][0]['captions'] = []
        self.assertNotEqual(self.pixels(without_caption, 'without-caption'), baseline)

    def test_unicode_and_long_font_paths_preserve_exact_title_and_caption_pixels(self):
        baseline = self.pixels(self.with_font(self.font), 'baseline')
        for label, folder in [('unicode',self.root/'Émile'),
                              ('long',self.root/('a'*90)/('b'*90)/('c'*50))]:
            with self.subTest(label=label):
                if os.name=='nt' and label=='long':
                    # Exercise a real long path without changing the machine's
                    # optional Win32 long-path policy. Keep cleanup on that same
                    # explicit path before TemporaryDirectory removes its root.
                    parent=self.root/('a'*90)
                    self.assertTrue(parent.is_relative_to(self.root))
                    extended_parent=Path('\\\\?\\'+str(parent.resolve()))
                    self.addCleanup(shutil.rmtree,extended_parent)
                    folder=Path('\\\\?\\'+str(folder.resolve()))
                folder.mkdir(parents=True)
                font=folder/'same-font.ttf'; shutil.copyfile(self.font,font)
                self.assertEqual(self.pixels(self.with_font(font),label),baseline)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_retained_command_runs_with_owned_font_directory_and_retires_it(self):
        font=self.root/'É font.ttf';shutil.copyfile(self.font,font)
        output=self.root/'command.mp4'
        command,graph=render.build_command(self.with_font(font),'s',str(output),
            {'format':'h264','loudnorm':False,'watermark_text':'50% review'})
        directory=command.context.root
        if os.name=='nt': self.assertEqual(command.cwd,directory)
        with command:
            # The production adapter also honours the cwd through slicing.
            result=render.subprocess.run(command[:],capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace')[-1000:])
            self.assertTrue(output.is_file())
        self.assertFalse(Path(directory).exists())

    def test_animated_captions_render_literal_percent_in_each_mode(self):
        font=self.root/'É caption.ttf';shutil.copyfile(self.font,font)
        for mode in ('highlight','pop'):
            with self.subTest(mode=mode):
                project=self.with_font(font)
                project['sequences'][0]['caption_style']['animate']=mode
                self.assertEqual(len(self.pixels(project,mode)),320*180*3)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))


if __name__=='__main__': unittest.main(verbosity=2)

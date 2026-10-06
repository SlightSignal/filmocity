"""Real ffprobe sources and production probe wrapper, plus malformed-data fixtures."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave
from PIL import Image

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from media_metadata import summarize


class Metadata(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="Filmocity source É's ");self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        tree=ast.parse((ROOT/'backend/server.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='probe')
        self.env={'subprocess':subprocess,'json':json};exec(compile(ast.Module(body=[node],type_ignores=[]),'probe','exec'),self.env);self.probe=self.env['probe']
    def wav(self,rate,channels):
        p=self.root/f'{rate}-{channels}.wav'
        with wave.open(str(p),'wb') as w:w.setparams((channels,2,rate,0,'NONE',''));w.writeframes(b'\0\0'*channels*(rate//10))
        return p
    def test_real_mono_44100_and_stereo_48000_are_distinct(self):
        for rate,channels in [(44100,1),(48000,2)]:
            with self.subTest(rate=rate):
                p=self.wav(rate,channels);before=p.read_bytes();m=self.probe(str(p))
                self.assertEqual((m['sample_rate'],m['channels']),(rate,channels));self.assertFalse(m['has_video']);self.assertEqual(m['audio_streams'][0]['sample_rate'],rate);self.assertEqual(p.read_bytes(),before)
    def test_real_fractional_frame_rate_is_retained_as_a_rational(self):
        p=self.root/'fractional.mov'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=64x32:r=30000/1001','-frames:v','3','-c:v','png','-threads','1',str(p)],check=True,capture_output=True,timeout=20)
        m=self.probe(str(p));self.assertEqual(m['frame_rate'],'30000/1001');self.assertAlmostEqual(m['fps'],30000/1001,12);self.assertIsNone(m['sample_rate']);self.assertFalse(m['vfr'])
    def test_real_surround_wave_reports_six_channels_and_layout(self):
        p=self.root/'surround.wav'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','anullsrc=r=48000:cl=5.1','-t','0.1','-c:a','pcm_s16le',str(p)],check=True,capture_output=True,timeout=20)
        m=self.probe(str(p));self.assertEqual(m['channels'],6);self.assertEqual(m['channel_layout'],'5.1');self.assertEqual(m['sample_rate'],48000)

    def test_real_still_is_five_seconds_without_audio_metadata(self):
        p=self.root/'still.png';Image.new('RGB',(80,40)).save(p);m=self.probe(str(p))
        self.assertTrue(m['is_image']);self.assertEqual(m['duration'],5);self.assertEqual(m['fps'],0);self.assertIsNone(m['frame_rate']);self.assertIsNone(m['channels'])
    def test_mjpeg_movie_without_audio_is_not_a_still(self):
        m=summarize({'streams':[{'codec_type':'video','codec_name':'mjpeg','width':100,'height':50,'avg_frame_rate':'25/1','r_frame_rate':'25/1'}],'format':{'format_name':'avi','duration':'10'}})
        self.assertFalse(m['is_image']);self.assertEqual(m['duration'],10);self.assertEqual(m['fps'],25)
    def test_attached_album_art_is_not_a_video_track(self):
        m=summarize({'streams':[{'codec_type':'video','codec_name':'mjpeg','disposition':{'attached_pic':1}}, {'index':1,'codec_type':'audio','codec_name':'mp3','sample_rate':'44100','channels':1}],'format':{'duration':'2'}})
        self.assertFalse(m['has_video']);self.assertEqual(m['codec'],'mp3');self.assertTrue(m['has_audio'])
    def test_equivalent_rates_are_not_flagged_vfr_and_stream_duration_falls_back(self):
        m=summarize({'streams':[{'codec_type':'video','avg_frame_rate':'60000/2002','r_frame_rate':'30000/1001','duration':'2'}]})
        self.assertFalse(m['vfr']);self.assertEqual(m['duration'],2)
    def test_unknown_or_invalid_audio_values_stay_unknown(self):
        for bad in (None,'N/A',0,-1,True,'nan','48000oops'):
            m=summarize({'streams':[{'codec_type':'audio','sample_rate':bad,'channels':bad}]})
            self.assertIsNone(m['sample_rate']);self.assertIsNone(m['channels'])
    def test_multiple_audio_streams_do_not_invent_one_combined_layout(self):
        m=summarize({'streams':[{'index':1,'codec_type':'audio','sample_rate':'48000','channels':6,'channel_layout':'5.1'}, {'index':2,'codec_type':'audio','sample_rate':'44100','channels':1}]})
        self.assertEqual(m['channels'],6);self.assertEqual(m['channel_layout'],'5.1');self.assertEqual(len(m['audio_streams']),2)
    def test_probe_failures_do_not_create_empty_success_metadata(self):
        for result in [subprocess.CompletedProcess([],1,'{}','unreadable'),subprocess.CompletedProcess([],0,'not-json',''),subprocess.CompletedProcess([],0,'[]',''),subprocess.CompletedProcess([],0,'{}','')]:
            with patch.object(subprocess,'run',return_value=result),self.assertRaises(ValueError):self.probe('source')
        with patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired('ffprobe',60)),self.assertRaisesRegex(ValueError,'timed out'):self.probe('source')
    def test_rotation_and_hdr_metadata_survive_summary(self):
        m=summarize({'streams':[{'codec_type':'video','width':1920,'height':1080,'side_data_list':[{'rotation':90}], 'color_transfer':'arib-std-b67','color_primaries':'bt2020','color_space':'bt2020nc','color_range':'tv'}]})
        self.assertEqual((m['width'],m['height']),(1080,1920));self.assertTrue(m['hdr']);self.assertEqual(m['color_range'],'tv')

if __name__=='__main__':unittest.main()

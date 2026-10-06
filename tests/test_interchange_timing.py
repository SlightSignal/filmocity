"""Boundary timing conversions, real XML serialization and subtitle rollover."""
import ast
import asyncio
import copy
import test_project_sync as store
from fractions import Fraction
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from timeline_time import frame_rate,xml_rate,to_frames,from_frames
from interchange import from_fcp7_xml,to_fcp7_xml,captions_to_srt,captions_to_vtt,media_uri,media_path,to_otio


def document(timebase=30,ntsc='TRUE',start=1800,inn=300,out=330):
    return f'''<xmeml version="4"><sequence><name>Fractional sequence</name><rate><timebase>{timebase}</timebase><ntsc>{ntsc}</ntsc></rate><media><video><track><clipitem><name>Source</name><start>{start}</start><end>{start+out-inn}</end><in>{inn}</in><out>{out}</out><file id="m"><pathurl>file:///source.mov</pathurl></file></clipitem></track></video></media></sequence></xmeml>'''


class Timing(unittest.TestCase):
    def test_ntsc_import_uses_actual_rate_for_start_and_source_offsets(self):
        s=from_fcp7_xml(document(),lambda *a:'m')[0];c=s['tracks'][0]['clips'][0]
        self.assertEqual(s['fps'],30000/1001);self.assertEqual(c['start'],60.06);self.assertEqual(c['in_'],10.01);self.assertEqual(c['out'],11.011)
    def test_all_common_rates_round_trip_frame_boundaries_over_two_hours(self):
        for value in (24,25,30,50,60,23.976,29.97,59.94,Fraction(24000,1001)):
            fps=frame_rate(value)
            with self.subTest(rate=value):
                for f in (0,1,1799,1800,17982,107892,to_frames(7200,fps)):
                    self.assertEqual(to_frames(from_frames(f,fps),value),f)
    def test_xml_round_trip_preserves_long_sequence_and_source_boundaries(self):
        for tb,ntsc in [(24,'TRUE'),(30,'TRUE'),(60,'TRUE'),(25,'FALSE'),(50,'FALSE')]:
            with self.subTest(timebase=tb,ntsc=ntsc):
                seq=from_fcp7_xml(document(tb,ntsc,216000,901,990),lambda *a:'m')[0]
                p={'sequences':[seq],'media':{'m':{'path':'/source.mov','name':'Source','duration':8000,'has_video':True,'has_audio':False,'width':640,'height':360}}}
                xml=to_fcp7_xml(p,seq['id']);again=from_fcp7_xml(xml,lambda *a:'m')[0]
                one,two=seq['tracks'][0]['clips'][0],again['tracks'][0]['clips'][0]
                for key in ('start','in_','out'):self.assertAlmostEqual(one[key],two[key],12)
    def test_export_recognizes_decimal_ntsc_alias_and_rejects_unrepresentable_rate(self):
        self.assertEqual(xml_rate(29.97),(30,True));self.assertEqual(xml_rate(59.94),(60,True))
        for bad in (29.5,0,float('nan'),True):
            with self.assertRaises(ValueError):xml_rate(bad)
    def test_xml_export_refuses_retiming_it_cannot_represent(self):
        seq=from_fcp7_xml(document(),lambda *a:'m')[0]
        for change in ({'speed':2},{'reverse':True},{'hold':True},{'time_remap':[{'t':0,'v':1}]}):
            project={'sequences':[copy.deepcopy(seq)],'media':{}}
            project['sequences'][0]['tracks'][0]['clips'][0].update(change)
            with self.assertRaisesRegex(ValueError,'retimed'):to_fcp7_xml(project,seq['id'])

    def test_half_frames_use_a_consistent_rounding_policy(self):
        self.assertEqual(to_frames(.5,1),1);self.assertEqual(to_frames(2.5,1),3);self.assertEqual(to_frames(-.5,1),-1)
    def test_malformed_xml_rates_and_unsupported_documents_reject(self):
        for xml in (document(0),document('nan'),document(29.5),document(30,'maybe'),'<fcpxml/>','<xmeml/>','<xmeml>'):
            with self.subTest(xml=xml),self.assertRaises(ValueError):from_fcp7_xml(xml,lambda *a:'m')
    def test_unsupported_transition_and_mixed_rate_offset_are_explicit(self):
        for xml in (document().replace('<start>1800</start>','<start>-1</start>'),document().replace('<in>300</in>','<in>300</in><mixedratesoffset>1</mixedratesoffset>')):
            with self.assertRaises(ValueError):from_fcp7_xml(xml,lambda *a:'m')
    def test_subtitle_rounding_carries_into_seconds_minutes_and_hours(self):
        caps=[{'start':.9996,'end':59.9996,'text':'First'}, {'start':3599.9996,'end':3600.0006,'text':'Second'}]
        srt=captions_to_srt(caps);vtt=captions_to_vtt(caps)
        self.assertIn('00:00:01,000 --> 00:01:00,000',srt);self.assertIn('01:00:00,000 --> 01:00:00,001',srt)
        self.assertIn('00:00:01.000 --> 00:01:00.000',vtt);self.assertNotIn(':60.',vtt);self.assertNotIn(',1000',srt)
    def test_nonfinite_negative_subtitle_times_reject(self):
        for bad in (-1,float('inf'),float('nan')):
            with self.assertRaises(ValueError):captions_to_srt([{'start':bad,'end':2,'text':'x'}])
    def test_windows_posix_and_unc_source_urls_round_trip_unicode_and_reserved_characters(self):
        for source,expected in [("C:/Émile's media/take #1.mov", "C:/Émile's media/take #1.mov"),
                                ("//server/share/Émile & take.mov", "//server/share/Émile & take.mov"),
                                ("/tmp/a % encoded.mov", "/tmp/a % encoded.mov")]:
            uri=media_uri(source);self.assertNotIn(' ',uri);self.assertNotIn('#',uri);self.assertEqual(media_path(uri),expected)
        self.assertEqual(media_path('file://localhost/C:/Media/take.mov'),'C:/Media/take.mov')
        with self.assertRaises(ValueError):media_path('https://example.com/take.mov')

    def test_forward_file_references_resolve_before_importing_timeline(self):
        xml=document().replace('<file id="m"><pathurl>file:///source.mov</pathurl></file>','<file id="m"/>').replace('</xmeml>','<file id="m"><pathurl>file:///source.mov</pathurl></file></xmeml>')
        paths=[];seq=from_fcp7_xml(xml,lambda path,name:paths.append(path) or 'm')[0]
        self.assertEqual(paths,['/source.mov']);self.assertEqual(len(seq['tracks'][0]['clips']),1)

    def test_retimed_or_negative_source_ranges_are_rejected_instead_of_changed(self):
        for xml in (document().replace('<in>300</in>','<in>-1</in>'),document().replace('<out>330</out>','<out>340</out>'),document().replace('<in>300</in>','<in>300</in><filter><effect><effectid>timeremap</effectid></effect></filter>')):
            with self.assertRaises(ValueError):from_fcp7_xml(xml,lambda *a:'m')

    def test_xml_source_audio_metadata_is_measured_or_unknown(self):
        seq=from_fcp7_xml(document(),lambda *a:'m')[0]
        media={'path':'/source.mov','duration':20,'has_video':True,'has_audio':True,'sample_rate':44100,'channels':1}
        p={'sequences':[seq],'media':{'m':media}}
        audio=ET.fromstring(to_fcp7_xml(p,seq['id'])).find('.//file/media/audio')
        self.assertEqual(audio.findtext('samplecharacteristics/samplerate'),'44100');self.assertEqual(audio.findtext('channelcount'),'1');self.assertIsNone(audio.find('samplecharacteristics/depth'))
        del media['sample_rate'];del media['channels'];audio=ET.fromstring(to_fcp7_xml(p,seq['id'])).find('.//file/media/audio')
        self.assertIsNone(audio.find('samplecharacteristics/samplerate'));self.assertIsNone(audio.find('channelcount'))

class Routes(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp();tree=ast.parse((ROOT/'backend/server.py').read_text())
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in ('import_fcpxml','export_fcpxml','_workflow_capture','_workflow_commit')]
        for n in nodes:n.decorator_list=[]
        self.queued=[];self.media=self.root/'source.mov';self.media.write_bytes(b'Controlled media-probe adapter')
        def ingest(path,name):return 'xmlmedia',{'id':'xmlmedia','name':name,'path':path,'duration':8000,'has_video':True,'has_audio':False,'width':64,'height':32,'ingest_token':'token','status':'ingesting'}
        async def owned(fn):return fn()
        self.env['_owned_render_thread']=owned
        self.env.update(from_fcp7_xml=from_fcp7_xml,to_fcp7_xml=to_fcp7_xml,ingest=ingest,finish_ingest=lambda *a:self.queued.append(a),Response=lambda text,**kw:{'text':text,**kw})
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'XML routes','exec'),self.env)
    def test_valid_fractional_import_registers_media_before_preparation(self):
        xml=document().replace('file:///source.mov',self.media.as_uri());result=self.invoke('import_fcpxml',{'xml':xml})
        self.assertEqual(len(result['sequences']),1);saved=self.env['load_project']();seq=saved['sequences'][-1]
        self.assertEqual(seq['tracks'][0]['clips'][0]['start'],60.06);self.assertIn('xmlmedia',saved['media']);self.assertEqual(len(self.queued),1)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertNotIn('xmlmedia',self.env['load_project']()['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'][-1]['tracks'][0]['clips'][0]['start'],60.06)
    def test_malformed_or_unsupported_import_leaves_saved_project_and_queue_unchanged(self):
        before=self.raw()
        for xml in ('bad xml',document(0),document().replace('<start>1800</start>','<start>-1</start>')):
            with self.assertRaises(store.HTTPError) as error:self.invoke('import_fcpxml',{'xml':xml})
            self.assertEqual(error.exception.status_code,400);self.assertEqual(self.raw(),before);self.assertEqual(self.queued,[])
    def test_project_switch_during_xml_probe_preserves_both_projects(self):
        before_a,before_b=self.raw(),self.raw('b');original=self.env['ingest']
        def switched(*args):
            value=original(*args);self.env['set_active_project']('b');return value
        self.env['ingest']=switched
        with self.assertRaises(store.HTTPError) as error:self.invoke('import_fcpxml',{'xml':document().replace('file:///source.mov',self.media.as_uri()),'_context':self.current()})
        self.assertEqual(error.exception.status_code,409);self.assertEqual(self.raw(),before_a);self.assertEqual(self.raw('b'),before_b);self.assertEqual(self.queued,[])

    def test_offline_xml_source_is_explicit_and_does_not_partially_import(self):
        before=self.raw()
        with self.assertRaises(store.HTTPError) as error:self.invoke('import_fcpxml',{'xml':document()})
        self.assertEqual(error.exception.status_code,400);self.assertIn('unavailable',str(error.exception));self.assertEqual(self.raw(),before);self.assertEqual(self.queued,[])

    def test_unrepresentable_export_rate_is_an_actionable_client_error(self):
        proj=self.env['load_project']();proj['sequences'][0]['fps']=29.5;self.env['save_project'](proj);before=self.raw()
        with self.assertRaises(store.HTTPError) as error:self.env['export_fcpxml']('seq1')
        self.assertEqual(error.exception.status_code,400);self.assertIn('cannot be represented',str(error.exception));self.assertEqual(self.raw(),before)

if __name__=='__main__':unittest.main()

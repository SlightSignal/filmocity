"""Atomic sequence planner: independent source windows and schema round trips."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from sequence_creation import plan
from project_recovery import parse_project
from timeline_time import frame_rate


def project():
    return {'version':3,'media':{'m':{'id':'m','name':'Shot.mov','path':'offline.mov','duration':6,
        'fps':30,'frame_rate':'30/1','has_video':True,'has_audio':True,'width':1920,'height':1080,
        'sample_aspect_ratio':'1:1','channels':2,'sample_rate':48000}},
        'sequences':[{'id':'main','name':'Main','width':1280,'height':720,'fps':30000/1001,
        'timecode_format':'df','tracks':[{'id':'v','kind':'video','index':1,'locked':True,'clips':[]}]}]}


class SequenceCreation(unittest.TestCase):
    def create(self, document=None, **fields):
        return plan(document or project(), {'_context':{'workspace':'w','project':'p','revision':'r'},
            'mode':'source','sequence':'main','media_id':'m',**fields},identity='a'*32)

    def test_empty_inherits_format_without_tracks_processing_or_annotations(self):
        before=project();before['sequences'][0].update(master={'gain_db':6},markers=[{'t':1}],captions=[{'text':'Old'}])
        original=copy.deepcopy(before);result=self.create(before,mode='empty',media_id=None,name='  New É  ')
        seq=result['sequence'];self.assertEqual(before,original);self.assertEqual(seq['name'],'New É')
        self.assertEqual((seq['width'],seq['height'],seq['fps'],seq['timecode_format']),(1280,720,30000/1001,'df'))
        self.assertEqual(seq['markers'],[]);self.assertNotIn('master',seq)
        self.assertTrue(all(not t['clips'] and not t['locked'] for t in seq['tracks']))
        self.assertEqual(result['ops'],[{'op':'insert','path':'/sequences/1','value':seq}])

    def test_source_whole_window_and_recoverable_numeric_schema(self):
        before=project();original=copy.deepcopy(before);result=self.create(before)
        seq=result['sequence'];clip=seq['tracks'][1]['clips'][0]
        self.assertEqual((clip['media_id'],clip['in_'],clip['out'],clip['start']),('m',0,6,0))
        self.assertEqual((seq['width'],seq['height'],seq['fps'],seq['timecode_format']),(1920,1080,30,'ndf'))
        self.assertEqual(before,original);before['sequences'].append(seq)
        self.assertEqual(parse_project(json.dumps(before).encode()),before)

    def test_tail_subclip_uses_its_interpretation_once(self):
        doc=project();doc['media']['sub']={**doc['media']['m'],'id':'sub','subclip_of':'m','sub_in':2,'duration':1,'interpret_fps':'60/1'}
        result=self.create(doc,media_id='sub');clip=result['sequence']['tracks'][1]['clips'][0]
        self.assertEqual((clip['in_'],clip['out'],clip['media_id']),(0,1,'sub'))
        self.assertEqual(result['sequence']['fps'],60);self.assertEqual(doc['media']['sub']['sub_in'],2)

    def test_audio_channel_alias_retains_selected_window_and_template_canvas(self):
        doc=project();doc['media']['ch']={**doc['media']['m'],'id':'ch','has_video':False,'width':0,'height':0,
            'subclip_of':'m','sub_in':2,'duration':1,'interpret_fps':'60/1',
            'audio_alias':{'version':1,'physical_media_id':'m','source_media_id':'m','channel_index':1}}
        seq=self.create(doc,media_id='ch')['sequence'];clip=seq['tracks'][2]['clips'][0]
        self.assertEqual((clip['in_'],clip['out'],clip['media_id']),(0,1,'ch'))
        self.assertEqual((seq['width'],seq['height'],seq['fps']),(1280,720,30000/1001));self.assertTrue(clip['audio']['linked'])

    def test_still_duration_does_not_depend_on_file_duration_or_fps(self):
        doc=project();doc['media']['m'].update(is_image=True,duration=None,fps=0,frame_rate=None,has_audio=False)
        seq=self.create(doc,still_duration=2.375)['sequence'];clip=seq['tracks'][1]['clips'][0]
        self.assertEqual(clip['out'],2.375);self.assertFalse(clip['audio']['linked']);self.assertEqual(seq['fps'],30000/1001)

    def test_square_pixel_geometry_respects_rotation_and_rounding(self):
        for width,height,rotation,expected in [(720,576,0,(768,576)),(576,720,90,(576,768)),(101,80,180,(108,80))]:
            doc=project();doc['media']['m'].update(width=width,height=height,rotation=rotation,sample_aspect_ratio='16:15')
            result=self.create(doc);seq=result['sequence']
            with self.subTest(rotation=rotation):self.assertEqual((seq['width'],seq['height']),expected)
            self.assertTrue(any('square-pixel' in w for w in result['warnings']))

    def test_ntsc_is_canonical_and_custom_fraction_does_not_claim_exact_schema(self):
        doc=project();doc['media']['m']['frame_rate']='24000/1001';result=self.create(doc)
        self.assertEqual(str(frame_rate(result['sequence']['fps'])),'24000/1001')
        doc['media']['m']['frame_rate']='71/3';result=self.create(doc)
        self.assertEqual(result['summary']['format']['frame_rate'],'71/3')
        self.assertTrue(any('numerically' in w for w in result['warnings']))

    def test_pending_or_offline_preview_with_complete_metadata_is_referenced(self):
        doc=project();doc['media']['m'].update(status='offline',vfr=True,sample_aspect_ratio=None)
        result=self.create(doc);self.assertEqual(result['sequence']['tracks'][1]['clips'][0]['out'],6)
        self.assertTrue(any('variable-frame-rate' in w for w in result['warnings']))
        self.assertTrue(any('preview preparation' in w for w in result['warnings']))
        self.assertTrue(any('assume square pixels' in w for w in result['warnings']))

    def test_unknown_or_invalid_source_cannot_return_partial_sequence(self):
        variants=[{'duration':None},{'duration':0},{'duration':float('nan')},{'has_video':None},
            {'has_audio':1},{'has_video':False,'has_audio':False},{'width':0},{'width':True},
            {'frame_rate':'garbage'},{'sample_aspect_ratio':'0:1'},{'sample_aspect_ratio':'1:0'},
            {'sample_aspect_ratio':'1:2;bad'},{'rotation':45},{'sub_in':1}]
        for changed in variants:
            doc=project();doc['media']['m'].update(changed);original=copy.deepcopy(doc)
            with self.subTest(changed=changed),self.assertRaises(ValueError):self.create(doc)
            # NaN cannot compare by value; serialization also checks no new sequence.
            self.assertEqual(json.dumps(doc),json.dumps(original))

    def test_invalid_selected_bounds_and_alias_cannot_create(self):
        for changed in ({'sub_in':5,'duration':2},{'sub_in':-1,'duration':1},{'sub_in':0,'duration':1,'audio_alias':{'version':1}}):
            doc=project();doc['media']['sub']={**doc['media']['m'],'id':'sub','subclip_of':'m',**changed}
            with self.subTest(changed=changed),self.assertRaises(ValueError):self.create(doc,media_id='sub')

    def test_invalid_command_and_collision_fail_before_mutation(self):
        for fields in ({'_context':{}},{'mode':'other'},{'sequence':'missing'},{'media_id':'missing'},
            {'name':' '},{'name':'x'*257},{'name':'bad\nname'},{'mode':'empty'},
            {'still_duration':2},{'mode':'empty','media_id':None,'still_duration':2}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):self.create(**fields)
        doc=project();doc['sequences'][0]['id']='seq_'+'a'*32
        with self.assertRaisesRegex(ValueError,'already exists'):self.create(doc,sequence=doc['sequences'][0]['id'])

    def test_invalid_still_durations_are_not_silently_defaulted(self):
        doc=project();doc['media']['m']['is_image']=True
        for value in (None,0,-1,True,86401,float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):self.create(doc,still_duration=value)


if __name__=='__main__':unittest.main()

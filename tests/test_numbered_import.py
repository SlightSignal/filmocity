"""Actual numbered import route/store admission and post-probe ownership races."""
import ast
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import preflight
import render
import test_project_sync as store
from PIL import Image


class NumberedImport(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        self.folder = self.root/"Émile's 50% frames"
        self.folder.mkdir()
        for index, color in ((7,'red'),(8,'blue')):
            Image.new('RGB',(64,48),color).save(self.folder/f'shot_{index:04d}.png')
        self.probes = []; self.prepared = []
        def probe(path): self.probes.append(path); return {'width':64,'height':48}
        self.env.update(asyncio=asyncio, probe=probe,
                        finish_ingest=lambda *args:self.prepared.append(copy.deepcopy(args)))
        nodes = [node for node in ast.parse((ROOT/'backend/server.py').read_text(encoding='utf-8')).body
                 if getattr(node,'name',None)=='media_import_sequence']
        self.assertEqual(len(nodes),1)
        nodes[0].decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)

    def import_(self,body=...):
        return self.invoke('media_import_sequence', {'folder':str(self.folder),'fps':24} if body is ... else body)

    def test_valid_numeric_clock_persists_admitted_argv_and_binds_preparation(self):
        before_b=self.raw('b'); result=self.import_({'folder':str(self.folder),'fps':1e-5})
        self.assertEqual(result['input_opts'],['-framerate','0.00001','-start_number','7'])
        self.assertEqual(self.raw('b'),before_b)
        self.assertEqual(preflight.media_files(result),[str(self.folder/f'shot_{i:04d}.png') for i in (7,8)])
        saved=json.loads(self.raw());self.assertEqual(saved['media'][result['id']],result)
        self.assertEqual(self.prepared[0][3],str(self.root/'projects/a/project.json'))
        self.assertEqual(self.prepared[0][4],result['ingest_token'])
        self.assertEqual(self.probes,[str(self.folder/'shot_0007.png')])

    def test_precise_float_clock_is_not_rounded_by_g_format(self):
        result=self.import_({'folder':str(self.folder),'fps':23.976023976023978})
        self.assertEqual(result['input_opts'][1],'23.976023976023978')

    def test_real_source_frames_follow_imported_percent_unicode_pattern_and_start(self):
        result=self.import_()
        sequence={'id':'s','width':64,'height':48,'fps':24,'duration':2/24,
                  'tracks':[{'id':'V1','kind':'video','index':1,'clips':[
                      {'id':'c','media_id':result['id'],'start':0,'in_':0,'out':2/24}]}]}
        snapshot={'media':{result['id']:result},'sequences':[sequence]}
        for index, channel in ((0,0),(1,2)):
            output=self.root/f'frame-{index}.png'
            render.render_frame(snapshot,'s',index/24,str(output))
            with Image.open(output) as picture:
                pixel=picture.convert('RGB').getpixel((32,24))
                self.assertGreater(pixel[channel],200)
                self.assertLess(max(pixel[(channel+1)%3],pixel[(channel+2)%3]),30)

    def test_invalid_body_or_frame_rate_refuses_before_listing_probe_save_or_events(self):
        cases=[None,[],1,{}, {'folder':None}, {'folder':''}, {'folder':'bad\0folder'}]
        cases += [{'folder':str(self.folder),'fps':value} for value in
                  (True,None,{},[],0,-1,1001,float('nan'),float('inf'),'not a rate')]
        before=(self.raw(),self.raw('b'))
        with patch.object(os,'listdir') as listing:
            for body in cases:
                with self.subTest(body=body),self.assertRaises(store.HTTPError) as failure:
                    self.import_(body)
                self.assertEqual(failure.exception.status_code,422)
            listing.assert_not_called()
        self.assertEqual((self.raw(),self.raw('b')),before)
        self.assertFalse(self.probes or self.prepared or self.broadcasts)

    def test_unavailable_folder_is_reported_without_probe_or_commit(self):
        before=self.raw()
        with self.assertRaises(store.HTTPError) as failure:
            self.import_({'folder':str(self.root/'missing'),'fps':24})
        self.assertEqual(failure.exception.status_code,422);self.assertEqual(self.raw(),before)
        self.assertFalse(self.probes or self.prepared)

    def test_unsupported_start_or_end_refuses_before_probe(self):
        for start in (2147483648,2147483647):
            folder=self.root/str(start);folder.mkdir()
            for index in (start,start+1):(folder/f'shot{index}.png').write_bytes(b'fixture')
            before=self.raw()
            with self.subTest(start=start),self.assertRaises(store.HTTPError) as failure:
                self.import_({'folder':str(folder),'fps':24})
            self.assertEqual(failure.exception.status_code,422);self.assertEqual(self.raw(),before)
        self.assertFalse(self.probes or self.prepared)

    def test_nonconsecutive_frames_refuse_before_probe_and_commit(self):
        (self.folder/'shot_0008.png').rename(self.folder/'shot_0009.png')
        before=self.raw()
        with self.assertRaises(store.HTTPError) as failure:self.import_()
        self.assertEqual(failure.exception.status_code,422);self.assertEqual(self.raw(),before)
        self.assertFalse(self.probes or self.prepared)

    def test_project_switch_during_async_probe_preserves_both_project_files(self):
        before=(self.raw(),self.raw('b'))
        def switch(path):
            self.env['set_active_project']('b');return {'width':64,'height':48}
        self.env['probe']=switch
        with self.assertRaises(store.HTTPError) as failure:self.import_()
        self.assertEqual(failure.exception.status_code,409)
        self.assertEqual((self.raw(),self.raw('b')),before)
        self.assertFalse(self.prepared or self.broadcasts)
        self.assertFalse((self.root/'projects/a/events.jsonl').exists())
        self.assertFalse((self.root/'projects/b/events.jsonl').exists())

    def test_same_project_revision_change_during_probe_cannot_be_overwritten(self):
        external=[]
        def edit(path):
            project=self.env['load_project']();project['name']='Other saved edit'
            self.env['save_project'](project);external.append(self.raw())
            return {'width':64,'height':48}
        self.env['probe']=edit
        with self.assertRaises(store.HTTPError) as failure:self.import_()
        self.assertEqual(failure.exception.status_code,409)
        self.assertEqual(self.raw(),external[0]);self.assertFalse(self.prepared or self.broadcasts)

    def test_post_commit_project_switch_does_not_redirect_media_event_or_preparation(self):
        before_b=self.raw('b')
        def prepare(*args):
            self.prepared.append(copy.deepcopy(args));self.env['set_active_project']('b')
        self.env['finish_ingest']=prepare
        result=self.import_()
        event=json.loads((self.root/'projects/a/events.jsonl').read_text().splitlines()[-1])
        self.assertEqual(event['project'],'a');self.assertEqual(event['media'],[result['id']])
        self.assertFalse((self.root/'projects/b/events.jsonl').exists())
        self.assertEqual(self.raw('b'),before_b)
        self.assertEqual(self.prepared[0][3],str(self.root/'projects/a/project.json'))


if __name__=='__main__': unittest.main(verbosity=2)

"""Real channel-alias delivery, rebuilt previews and isolated measurement.

Store/HTTP adapters are controlled; copying, packages and decoded media are real.
This is development evidence, separate from native Windows acceptance.
"""
import ast
import copy
import json
from pathlib import Path
import shutil
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import media_collection as collection
import media_preview
import project_package as package
import source_relink_io
from background_tasks import TaskContext
import test_source_creation_workflow as creation


class SourceCreationDelivery(creation.StoreSourceCreation):
    for _name in dir(creation.StoreSourceCreation):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp();self.apply(self.inspect())
        for identity in list(self.manager.values):self.prepare(identity)
        reply=self.create('breakout');self.channels=reply['media_ids']
        for item in reply['preparation']['tasks']:self.prepare(item['task']['id'])
        self.before=self.project()
        names={'projects_collect','_task_collect','_commit_collection','_apply_collection'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.env['MediaCollectionError']=collection.MediaCollectionError
        self.manager.handlers['collect']=self.env['_task_collect']

    def test_collected_offline_original_preserves_channels_pcm_and_requires_owned_preview_rebuild(self):
        baseline=[self.audio(self.before,mid,'before-'+str(i),.5,3,True) for i,mid in enumerate(self.channels)]
        queued=self.route('projects_collect',{'_context':self.current(),'actor':'human','client':'channel-delivery'})
        identity=queued['task']['id'];value=self.manager.values[identity];value['record']['status']='running'
        value['result']=self.env['_task_collect'](value['payload'],TaskContext(self.manager,identity))
        value['record']['status']='ready';self.manager.store.save(value);self.replacement.unlink()
        self.route('background_task_apply',{'_context':self.current(),'actor':'human'},identity);after=self.project()
        self.assertEqual(after['sequences'],self.before['sequences'])
        self.assertEqual(len({after['media'][mid]['path'] for mid in ['m',*self.channels]}),1)
        for index,mid in enumerate(self.channels):
            self.assertEqual(after['media'][mid]['audio_alias'],self.before['media'][mid]['audio_alias'])
            source_relink_io.check_accepted(after['media'][mid])
            self.assertEqual(self.audio(after,mid,'collected-'+str(index),.5,3,True),baseline[index])
            self.assertEqual(media_preview.describe(self.root,after,mid)['proxy_state'],'stale_source')
            with self.assertRaises(media_preview.PreviewError):media_preview.resolve(self.root,after,mid,{},workspace='w',project_id='a')
            ready=self.route('background_media_prepare',{'_context':self.current()},mid)['task'];self.prepare(ready['id'])
            self.assertEqual(media_preview.describe(self.root,self.project(),mid)['proxy_state'],'ready')
        ready=self.project();self.invoke('undo',{'_context':self.current()})
        self.assertEqual(self.project()['media'],self.before['media'])
        self.invoke('undo',{'_context':self.current()})
        self.assertTrue(all(mid not in self.project()['media'] for mid in self.channels))
        self.assertEqual(self.project()['media']['m'],self.before['media']['m'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],self.before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],ready['media'])

    def test_portable_channel_roundtrip_keeps_descriptor_and_exact_original_export_after_originals_removed(self):
        baseline=[self.audio(self.before,mid,'before-package-'+str(i),.25,2.75) for i,mid in enumerate(self.channels)]
        destination=self.root/'portable';receipt=package.export_package(self.before,destination,'c'*32)
        imported=package.import_package(receipt['manifest'],self.root/'receiving','d'*32)
        after=json.loads((Path(imported['folder'])/'project.json').read_text())
        self.source.unlink();self.replacement.unlink();shutil.rmtree(destination)
        for index,mid in enumerate(self.channels):
            self.assertEqual(after['media'][mid]['audio_alias'],self.before['media'][mid]['audio_alias'])
            self.assertNotIn('source_relink_basis',after['media'][mid])
            self.assertEqual(self.audio(after,mid,'received-'+str(index),.25,2.75),baseline[index])
            with self.assertRaises(media_preview.PreviewError):media_preview.resolve(self.root,after,mid,{},workspace='w',project_id='a')
        self.assertEqual(self.project(),self.before)

    def test_channel_subclip_keeps_selected_range_and_has_independent_preparation(self):
        selected=self.channels[1];reply=self.create('subclip',media_id=selected,**{'in':1.25,'out':3.75});mid=reply['media_ids'][0]
        self.assertEqual(reply['media'][0]['sub_in'],1.25);self.assertEqual(reply['media'][0]['duration'],2.5)
        self.assertEqual(reply['media'][0]['audio_alias']['channel_index'],1)
        self.assertEqual(len(reply['preparation']['tasks']),1)
        self.prepare(reply['preparation']['tasks'][0]['task']['id']);after=self.project()
        self.assertEqual(self.audio(after,mid,'channel-subclip',0,2.5),self.audio(self.before,selected,'selected-window',1.25,3.75))
        self.assertEqual(media_preview.describe(self.root,after,mid)['proxy_state'],'ready')
        self.assertNotEqual(after['media'][mid]['proxy'],after['media'][selected]['proxy'])
        self.assertEqual(after['media'][selected],self.before['media'][selected])

    def test_renderer_based_measurement_observes_only_selected_channel(self):
        import test_audio_measurement as measurement
        oracle=measurement.Measurements('runTest');oracle.setUp();self.addCleanup(oracle.doCleanups)
        parent=oracle.wav('distinct-levels.wav',1,lambda t:(.01,.4));parent['id']='physical'
        results=[]
        for index in (0,1):
            alias={**copy.deepcopy(parent),'id':'selected','subclip_of':'physical','sub_in':0,
                'audio_alias':{'version':1,'physical_media_id':'physical','source_media_id':'physical','channel_index':index}}
            results.append(oracle.run_measure(oracle.payload(alias,extra_media={'physical':parent})))
        self.assertAlmostEqual(results[1]['peak_db']-results[0]['peak_db'],32.04,delta=.03)
        self.assertAlmostEqual(results[1]['rms_db']-results[0]['rms_db'],32.04,delta=.03)


if __name__=='__main__':unittest.main()

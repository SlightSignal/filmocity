"""Interpreted channel delivery with real copies, decoding and saved Undo chains."""
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
import test_source_relink_workflow as relink
import test_source_interpretation_workflow as interpretation


class InterpretationDelivery(interpretation.StoreSourceInterpretation):
    for _name in dir(interpretation.StoreSourceInterpretation):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp()
        relink.StoreSourceRelink.apply(self,self.inspect())
        for identity in list(self.manager.values):self.prepare(identity)
        self.before_creation=self.project()
        created=self.create('breakout');self.channels=created['media_ids']
        for item in created['preparation']['tasks']:self.prepare(item['task']['id'])
        self.before_interpretation=self.project()
        reply=self.apply(self.review(media_id=self.channels[1],fps='24/1'))
        for item in reply['preparation']['tasks']:self.prepare(item['task']['id'])
        self.before=self.project()
        names={'projects_collect','_task_collect','_commit_collection','_apply_collection'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.env['MediaCollectionError']=collection.MediaCollectionError
        self.manager.handlers['collect']=self.env['_task_collect']

    def test_interpret_collect_offline_rebuild_and_three_step_undo_redo(self):
        baseline=[self.audio(self.before,mid,'before-'+str(i),.5,3,True) for i,mid in enumerate(self.channels)]
        queued=self.route('projects_collect',{'_context':self.current(),'actor':'human','client':'interpret-delivery'})
        identity=queued['task']['id'];value=self.manager.values[identity];value['record']['status']='running'
        value['result']=self.env['_task_collect'](value['payload'],TaskContext(self.manager,identity))
        value['record']['status']='ready';self.manager.store.save(value);self.replacement.unlink()
        self.route('background_task_apply',{'_context':self.current(),'actor':'human'},identity);after=self.project()
        self.assertEqual(after['sequences'],self.before['sequences'])
        for index,mid in enumerate(self.channels):
            self.assertEqual(after['media'][mid].get('interpret_fps'),self.before['media'][mid].get('interpret_fps'))
            self.assertEqual(after['media'][mid]['audio_alias'],self.before['media'][mid]['audio_alias'])
            source_relink_io.check_accepted(after['media'][mid])
            self.assertEqual(self.audio(after,mid,'collected-'+str(index),.5,3,True),baseline[index])
            self.assertEqual(media_preview.describe(self.root,after,mid)['proxy_state'],'stale_source')
            task=self.route('background_media_prepare',{'_context':self.current()},mid)['task'];self.prepare(task['id'])
            self.assertEqual(media_preview.describe(self.root,self.project(),mid)['proxy_state'],'ready')
        ready=self.project()
        for expected in (self.before,self.before_interpretation,self.before_creation):
            self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],expected['media'])
        for expected in (self.before_interpretation,self.before,ready):
            self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],expected['media'])

    def test_package_roundtrip_keeps_independent_channel_clock_after_sources_removed(self):
        baseline=[self.audio(self.before,mid,'before-package-'+str(i),.5,3) for i,mid in enumerate(self.channels)]
        destination=self.root/'portable';receipt=package.export_package(self.before,destination,'e'*32)
        imported=package.import_package(receipt['manifest'],self.root/'receiving','f'*32)
        after=json.loads((Path(imported['folder'])/'project.json').read_text())
        self.source.unlink();self.replacement.unlink();shutil.rmtree(destination)
        self.assertNotIn('interpret_fps',after['media'][self.channels[0]])
        self.assertEqual(after['media'][self.channels[1]]['interpret_fps'],'24/1')
        for index,mid in enumerate(self.channels):
            self.assertEqual(after['media'][mid]['audio_alias'],self.before['media'][mid]['audio_alias'])
            self.assertNotIn('source_relink_basis',after['media'][mid])
            self.assertEqual(self.audio(after,mid,'received-'+str(index),.5,3),baseline[index])
        # Portable graphics explicitly pin/copy fonts. Verify those resources
        # before comparing the editorial payload with its original fields.
        sequences=copy.deepcopy(after['sequences']);stack=[sequences];fonts=0
        while stack:
            item=stack.pop()
            if isinstance(item,list):stack.extend(item)
            elif isinstance(item,dict):
                resource=item.pop('font_resource',None)
                if resource is not None:
                    self.assertTrue(Path(resource['path']).is_file());fonts+=1
                stack.extend(item.values())
        self.assertEqual(fonts,2);self.assertEqual(sequences,self.before['sequences']);self.assertEqual(self.project(),self.before)


if __name__=='__main__':unittest.main()

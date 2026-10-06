"""Real verified copies/packages after owned Relink; controlled route wrappers.

Original resources and decoded PCM are real. These checks do not exercise a
native HTTP/WebView server or Windows filesystem/device behavior.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import collection_workflow as collect
import media_collection as collection
import media_preview
import preflight
import project_package as package
import project_lifecycle as lifecycle
import render
import source_relink_io
from background_tasks import TaskContext
from render_context import RenderContext
from task_inputs import source_stamp, portable_source_stamp
import test_source_relink_workflow as relink


class SourceRelinkDelivery(relink.StoreSourceRelink):
    for _name in dir(relink.StoreSourceRelink):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp();self.children(interpreted=True);self.apply(self.inspect('alias'))
        self.accepted=self.project();self.destination=self.root/'delivered';self.packages=self.root/'packages'
        names={'projects_collect','_task_collect','_commit_collection','_apply_collection'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.env['MediaCollectionError']=collection.MediaCollectionError
        self.manager.handlers['collect']=self.env['_task_collect']
        worker=lifecycle.ActionWorker();scope=patch.object(lifecycle,'COPY_WORKER',worker);scope.start()
        self.addCleanup(scope.stop);self.addCleanup(worker.shutdown)

    def collect_core(self,project=None):
        with collection.MediaCollection(project or self.project(),self.destination) as value:
            value.publish();return value.project,value.result()

    def ready_collection(self):
        queued=self.route('projects_collect',{'_context':self.current(),'actor':'human','client':'relink-delivery'})
        identity=queued['task']['id'];value=self.manager.values[identity];value['record']['status']='running'
        result=self.env['_task_collect'](value['payload'],TaskContext(self.manager,identity))
        value['result']=result;value['record']['status']='ready';self.manager.store.save(value)
        return identity

    def assert_bound(self,project,identities=('m','sub','alias')):
        for identity in identities:
            media=project['media'][identity];basis=media['source_relink_basis']
            self.assertEqual(basis['version'],1);self.assertEqual(basis['path'],media['path'])
            self.assertEqual(basis['stamp'],portable_source_stamp(source_stamp(media)));self.assertEqual(basis['probe'],self.accepted['media']['m']['source_relink_basis']['probe'])
            self.assertEqual(source_relink_io.check_accepted(media),basis)

    def export_package(self,identity='a'*32,**kw):return package.export_package(self.project(),self.packages,identity,**kw)

    def test_collection_rebases_duplicate_physical_and_child_identities_and_reuses_verified_copy(self):
        project=self.project();project['media']['duplicate']={**copy.deepcopy(project['media']['m']),'id':'duplicate','name':'Same physical bytes'}
        project['media']['late-sub']={**copy.deepcopy(project['media']['sub']),'id':'late-sub','name':'Created after Relink'}
        project['media']['late-sub'].pop('source_relink_basis')
        before=copy.deepcopy(project);sha=hashlib.sha256(self.replacement.read_bytes()).hexdigest()
        after,receipt=self.collect_core(project)
        self.assertEqual(project,before);self.assertEqual(receipt['copied'],1);self.assertEqual(receipt['verified'],1)
        self.assertEqual(len({m['path'] for m in after['media'].values()}),1)
        self.assert_bound(after,('m','sub','alias','duplicate','late-sub'))
        self.assertEqual(hashlib.sha256(Path(after['media']['m']['path']).read_bytes()).hexdigest(),sha)
        self.assertEqual(after['sequences'],before['sequences'])
        for identity in ('sub','alias'):
            self.assertEqual(after['media'][identity]['sub_in'],before['media'][identity]['sub_in'])
            self.assertEqual(after['media'][identity]['duration'],before['media'][identity]['duration'])
        reused,again=self.collect_core(after)
        self.assertEqual(reused,after);self.assertEqual((again['copied'],again['reused']),(0,1))
        self.assert_bound(reused,('m','sub','alias','duplicate','late-sub'))

    def test_managed_collection_offline_receipt_applies_one_undo_and_preserves_actual_pcm(self):
        project=self.project();project['media']['late-sub']={**copy.deepcopy(project['media']['sub']),'id':'late-sub','name':'Created after Relink'}
        project['media']['late-sub'].pop('source_relink_basis');self.env['save_project'](project)
        before=self.project();baseline=self.audio(before,'alias','before-collection',0,3,True)
        original=self.raw();identity=self.ready_collection();self.assertEqual(self.raw(),original)
        value=self.manager.values[identity];self.replacement.unlink()
        verified=collect.verify(self.project(),value['payload'],value['result']);self.assert_bound(verified,('m','sub','alias','late-sub'))
        self.route('background_task_apply',{'_context':self.current(),'actor':'human'},identity)
        after=self.project();self.assert_bound(after);self.assertEqual(after['sequences'],before['sequences'])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),2)
        self.assertEqual(self.audio(after,'alias','after-collection',0,3,True),baseline)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])
        self.assert_bound(self.project())
        # This fixture deliberately has no running workers. Retire the old
        # queued jobs whose captured pre-collection paths can no longer publish.
        for task_id,value in list(self.manager.values.items()):
            if value['record']['kind']=='media' and value['record']['status']=='queued':
                self.assertFalse(self.env['_task_media_current'](value['payload']))
                self.manager.cancel(task_id)
        ready=self.route('background_media_prepare',{'_context':self.current()},'alias')['task'];self.prepare(ready['id'])
        self.assertEqual(self.project()['media']['alias']['proxy_status'],'ready')

    def test_completed_proxy_transfers_to_collected_identity_and_alias_rebuild_remains_owned(self):
        for identity,value in list(self.manager.values.items()):
            if value['record']['kind']=='media':self.prepare(identity)
        before=self.project();self.assertEqual(media_preview.describe(self.root,before,'m')['proxy_state'],'ready')
        identity=self.ready_collection();self.route('background_task_apply',{'_context':self.current()},identity)
        after=self.project();self.assert_bound(after)
        self.assertEqual(media_preview.describe(self.root,after,'m')['proxy_state'],'ready')
        # Alias range ownership includes the physical path; collected bytes are
        # accepted, but its old preview needs an explicit current-owner rebuild.
        self.assertEqual(media_preview.describe(self.root,after,'alias')['proxy_state'],'stale_source')
        queued=self.route('background_media_prepare',{'_context':self.current()},'alias')['task'];self.prepare(queued['id'])
        self.assertEqual(media_preview.describe(self.root,self.project(),'alias')['proxy_state'],'ready')

    def test_collection_capture_includes_accepted_basis_and_rejects_rebound_owner(self):
        captured=collect.payload(self.project(),self.root,'a');project=self.project()
        project['media']['m']['source_relink_basis']['probe']='0'*64
        with self.assertRaises(collection.MediaCollectionError):collect.current_inputs(project,captured)

    def test_changed_accepted_source_blocks_preflight_and_real_export_before_output_replacement(self):
        project=self.project();before=self.raw();target=self.root/'preserved-master.wav';target.write_bytes(b'previous accepted master')
        with self.replacement.open('ab') as stream:stream.write(b'changed source bytes')
        issues=preflight.inspect_resources(project,'s')['issues']
        self.assertIn('source_changed_after_relink',{row['code'] for row in issues})
        with self.assertRaises(preflight.ResourceError):
            with RenderContext(scratch_parent=str(self.root)) as context:render.render(project,'s',str(target),{'format':'audio','acodec':'wav'},context=context)
        self.assertEqual(target.read_bytes(),b'previous accepted master');self.assertEqual(self.raw(),before)

    def test_stale_before_delivery_refuses_collection_and_package_without_publication(self):
        before=self.raw()
        with self.replacement.open('ab') as stream:stream.write(b'changed before delivery')
        with self.assertRaises((collection.MediaCollectionError,ValueError)):self.collect_core()
        with self.assertRaises((package.PackageError,ValueError)):self.export_package()
        self.assertFalse(list(self.destination.glob('collection-*')));self.assertFalse(list(self.destination.glob('.collect-*')))
        self.assertFalse(list(self.packages.glob('package-*')));self.assertFalse(list(self.packages.glob('.pack-*')))
        self.assertEqual(self.raw(),before)

    def test_source_change_after_verified_collection_read_prevents_publication(self):
        original=collection._verified_file;changed=False;before=self.raw()
        def changed_after_copy(source,*args,**kwargs):
            nonlocal changed
            details=original(source,*args,**kwargs)
            if Path(source)==self.replacement and not changed:
                changed=True
                with self.replacement.open('ab') as stream:stream.write(b'changed after verified copy')
            return details
        with patch.object(collection,'_verified_file',changed_after_copy),self.assertRaises((collection.MediaCollectionError,ValueError)):
            self.collect_core()
        self.assertTrue(changed);self.assertFalse(list(self.destination.glob('collection-*')))
        self.assertFalse(list(self.destination.glob('.collect-*')));self.assertEqual(self.raw(),before)

    def test_source_change_after_verified_package_read_prevents_publication(self):
        original=package._verified_file;changed=False;before=self.raw()
        def changed_after_copy(source,*args,**kwargs):
            nonlocal changed
            details=original(source,*args,**kwargs)
            if Path(source)==self.replacement and not changed:
                changed=True
                with self.replacement.open('ab') as stream:stream.write(b'changed after verified package copy')
            return details
        with patch.object(package,'_verified_file',changed_after_copy),self.assertRaises((package.PackageError,ValueError)):
            self.export_package()
        self.assertTrue(changed);self.assertFalse(list(self.packages.glob('package-*')))
        self.assertFalse(list(self.packages.glob('.pack-*')));self.assertEqual(self.raw(),before)

    def test_portable_roundtrip_omits_machine_acceptance_but_preserves_decoded_pcm_and_picture(self):
        from PIL import Image
        before=self.project();raw=self.raw();source_sha=hashlib.sha256(self.replacement.read_bytes()).hexdigest()
        audio=self.audio(before,'alias','before-package',0,3,True)
        def pixels(project,name):
            path=self.root/name
            with RenderContext(scratch_parent=str(self.root)) as context:render.render_frame(project,'s',1,str(path),context=context)
            with Image.open(path) as image:return image.convert('RGB').tobytes()
        picture=pixels(before,'before-package.png');receipt=self.export_package()
        portable,manifest,_=package.verify_package(receipt['manifest'])
        self.assertTrue(all('source_relink_basis' not in m for m in portable['media'].values()))
        self.assertTrue(any(row['sha256']==source_sha for row in manifest['files']))
        imported=package.import_package(receipt['manifest'],self.root/'receiving','b'*32)
        after=json.loads((Path(imported['folder'])/'project.json').read_text())
        self.assertTrue(all('source_relink_basis' not in m for m in after['media'].values()))
        self.replacement.unlink();self.source.unlink();shutil.rmtree(self.packages)
        self.assertEqual(self.audio(after,'alias','after-package',0,3,True),audio)
        self.assertEqual(pixels(after,'after-package.png'),picture)
        self.assertTrue(preflight.inspect_resources(after,'s')['ok']);self.assertEqual(self.raw(),raw)

    def test_rehashed_portable_document_cannot_reintroduce_machine_acceptance(self):
        receipt=self.export_package();folder=Path(receipt['folder'])
        document=json.loads((folder/'project.json').read_text());manifest=json.loads((folder/'manifest.json').read_text())
        document['media']['alias']['source_relink_basis']=copy.deepcopy(self.accepted['media']['alias']['source_relink_basis'])
        raw=package.packed(document);(folder/'project.json').write_bytes(raw)
        manifest['project_sha256']=package.digest(raw);(folder/'manifest.json').write_bytes(package.packed(manifest))
        with self.assertRaises(package.PackageError):package.verify_package(receipt['manifest'])
        with self.assertRaises(package.PackageError):package.import_package(receipt['manifest'],self.root/'rejected-import','c'*32)
        self.assertFalse((self.root/'rejected-import').exists())

    def test_actual_browser_color_and_canonical_duplicate_preserve_exact_file_identity(self):
        # Source Color still sends a complete media value through browser JSON.
        # Duplicate now sends IDs to the guarded server planner, never a clone.
        script=r'''const fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8')),calls=[];
(async()=>{
 const color=require('./frontend/media-color.js'),{fixture}=require('./tests/dom_fixture.cjs');
 const d=fixture(['host']);d.nodes.host.ownerDocument=d.document;d.document.body={};
 const CR={S:{proj:input,context:{workspace:'w',project:'p'}},canEdit:()=>true,status(){},applyOps:async ops=>{calls.push(ops);return true;}};
 const view=color.mount(CR,d.nodes.host,'m');view.peakInput.value='500';await view.form.events.submit({preventDefault(){}});
 if(calls.length!==1)throw Error('Expected one actual callback edit');process.stdout.write(JSON.stringify(calls[0]));
})().catch(error=>{process.stderr.write(String(error));process.exitCode=1;});'''
        def browser(project):
            value=subprocess.run(['node','-e',script],input=json.dumps(project),text=True,cwd=ROOT,capture_output=True,check=True,timeout=15)
            return json.loads(value.stdout)
        expected=copy.deepcopy(self.project()['media']['m']['source_relink_basis'])
        self.assertGreater(int(expected['stamp'][0][2]),2**53)
        ops=browser(self.project());self.assertEqual(ops[0]['value']['source_relink_basis'],expected)
        reply=self.invoke('patch_project',{'_context':self.current(),'actor':'human','tool':'source_color','ops':ops});self.assertTrue(reply['ok'])
        names={'media_duplicate','_source_creation_command','_commit_source_creation'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        duplicate=self.route('media_duplicate',{'_context':self.current(),'actor':'human','media_ids':['m']})
        mid=duplicate['media_ids'][0]
        for identity in ('m',mid):
            self.assertEqual(self.project()['media'][identity]['source_relink_basis'],expected)
            source_relink_io.check_accepted(self.project()['media'][identity])
            self.assertNotEqual(media_preview.describe(self.root,self.project(),identity)['proxy_state'],'stale_source')
        self.assertTrue(preflight.inspect_resources(self.project(),'s')['ok'])
        before=self.audio(self.accepted,'m','before-browser-copy',0,1)
        self.assertEqual(self.audio(self.project(),mid,'after-browser-copy',0,1),before)
        self.prepare(duplicate['preparation']['tasks'][0]['task']['id'])
        self.assertEqual(media_preview.describe(self.root,self.project(),mid)['proxy_state'],'ready')
        # Exercise all three stat fields above Number.MAX_SAFE_INTEGER without
        # inventing a petabyte source or relying on filesystem inode magnitude.
        raw=[[str(self.replacement),2**53+1,1791229938123456789,2**54+3]]
        specimen=copy.deepcopy(self.accepted);specimen['media']['m']['source_relink_basis']['stamp']=portable_source_stamp(raw)
        media=browser(specimen)[0]['value']
        self.assertEqual(media['source_relink_basis']['stamp'],[[raw[0][0],*[str(v) for v in raw[0][1:]]]])
        with patch.object(source_relink_io,'source_stamp',return_value=raw):source_relink_io.check_accepted(media)
        media['source_relink_basis']['stamp']=copy.deepcopy(raw)
        with patch.object(source_relink_io,'source_stamp',return_value=raw),self.assertRaises(ValueError):source_relink_io.check_accepted(media)


if __name__=='__main__':unittest.main()

"""Owned Relink routes against actual saved stores, probes and preparation."""
import ast
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import media_preview
import media_preparation
import source_relink_io as relink_io
from controlled_probe import python_probe
import source_commands
from background_tasks import TaskContext
from test_media_collection import run_async_check
import test_editorial_source_commands as editorial
import test_project_sync as store


class StoreSourceRelink(editorial.StoreEditorialSourceCommands):
    for _name in dir(editorial.StoreEditorialSourceCommands):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp()
        names={'media_relink','media_relink_inspect','_relink_candidate','_commit_source_relink'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.replacement=self.root/'replacement.mkv';shutil.copyfile(self.source,self.replacement)

    def inspect(self,media_id='m',path=None,**kw):
        return self.route('media_relink_inspect',self.body(media_id=media_id,path=str(path or self.replacement),**kw))

    def apply(self,review,**kw):
        return self.route('media_relink',{'_context':review['context'],'media_id':review['requested_media_id'],
            'path':review['path'],'fingerprint':review['fingerprint'],'actor':'human','client':'relink-test',**kw})

    def prepare(self,identity):
        # This host lacks libx264. Qualify the actual opt-in OpenH264 path;
        # production proxy defaults are unchanged and remain Windows acceptance.
        task=self.manager.values[identity];task['record']['status']='running';context=TaskContext(self.manager,identity)
        result=media_preparation.prepare(self.root,task['payload'],context,self.env['_task_media_current'],self.env['_task_media_update'],proxy_encoder='libopenh264')
        context.commit_result(lambda:result);self.manager.store.save(task);return result

    def children(self,*,interpreted=False):
        project=self.project();parent=project['media']['m']
        if interpreted:parent.update(interpret_fps=24,native_duration=6,native_fps=30,duration=7.5)
        for identity,alias in [('sub',False),('alias',True)]:
            child={**copy.deepcopy(parent),'id':identity,'name':identity,'subclip_of':'m','sub_in':1,'duration':3,'ingest_token':identity+'-old'}
            if alias:child.update(has_video=False,width=0,height=0,audio_alias={'version':1,'source_media_id':'sub','physical_media_id':'m'})
            project['media'][identity]=child
        self.env['save_project'](project)
        return project

    def short(self,duration=2):
        path=self.root/'short.mkv'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(self.source),'-t',str(duration),'-c','copy',str(path)],check=True,capture_output=True,timeout=30)
        return path

    def test_inspect_is_owned_readonly_and_allowed_for_proposals_agents(self):
        before=(self.raw(),self.raw('b'));history=self.env['read_undo_history']('a')
        (self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
        report=self.inspect(actor='agent');self.assertTrue(report['ok']);self.assertEqual(len(report['fingerprint']),64)
        self.assertEqual(report['context'],self.current());self.assertEqual((self.raw(),self.raw('b')),before)
        self.assertEqual(self.env['read_undo_history']('a'),history);self.assertFalse(self.manager.values)
        with self.assertRaises(store.HTTPError):self.apply(report,actor='agent')
        with self.assertRaises(store.HTTPError):self.route('media_relink_inspect',{'media_id':'m','path':str(self.replacement)})
        self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.route('media_relink_inspect',{'_context':report['context'],'media_id':'m','path':str(self.replacement)})
        self.assertEqual((self.raw(),self.raw('b')),before)

    def test_actual_probe_updates_parent_and_dependents_one_complete_undo(self):
        project=self.children(interpreted=True);project['sequences'][0]['tracks'][0]['locked']=True
        project['sequences'][0]['tracks'][0]['clips'].append(dict(project['sequences'][0]['tracks'][0]['clips'][0],id='overlap',start=1))
        self.env['save_project'](project);before=self.project();review=self.inspect('alias')
        self.assertTrue(review['ok']);self.assertEqual(review['media_id'],'m');self.assertEqual(review['requested_media_id'],'alias')
        reply=self.apply(review);after=self.project()
        self.assertTrue(reply['changed']);self.assertEqual(after['sequences'],before['sequences'])
        self.assertEqual(set(reply['affected_media_ids']),{'m','sub','alias'})
        self.assertEqual(after['media']['m']['duration'],7.5)
        for identity in ('sub','alias'):
            self.assertEqual(after['media'][identity]['duration'],3);self.assertEqual(after['media'][identity]['sub_in'],1)
        self.assertEqual({x['media_id'] for x in reply['preparation']['tasks']},{'m','alias'})
        history=self.env['read_undo_history']('a');self.assertEqual(len(history['undo']),1)
        self.assertEqual({tuple(c['path']) for c in history['undo'][0]['changes']},{('media','m'),('media','sub'),('media','alias')})
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])

    def test_escaped_media_ids_do_not_retarget_literal_escape_names(self):
        project=self.project();identity='a/b~c';decoy='a~1b~0c'
        media=project['media'].pop('m');media['id']=identity;project['media'][identity]=media
        project['media'][decoy]={**copy.deepcopy(media),'id':decoy,'name':'Never modify literal escapes'}
        project['sequences'][0]['tracks'][0]['clips'][0]['media_id']=identity;self.env['save_project'](project)
        before=self.project();reply=self.apply(self.inspect(identity));after=self.project()
        self.assertEqual(reply['media_id'],identity);self.assertEqual(after['media'][decoy],before['media'][decoy])
        self.assertEqual(after['media'][identity]['path'],str(self.replacement))
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])

    def test_completed_preparation_updates_only_receipt_after_values(self):
        self.children();before=self.project();reply=self.apply(self.inspect())
        for item in reply['preparation']['tasks']:self.prepare(item['task']['id'])
        after=self.project();history=self.env['read_undo_history']('a')
        self.assertEqual(len(history['undo']),1)
        for change in history['undo'][0]['changes']:
            identity=change['path'][1];self.assertEqual(change['before']['value'],before['media'][identity]);self.assertEqual(change['after']['value'],after['media'][identity])
        self.assertEqual(after['media']['alias']['proxy_status'],'ready')
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])
        self.assertEqual(media_preview.describe(self.root,self.project(),'alias')['proxy_state'],'ready')

    def test_unused_alias_extent_blocks_short_candidate_without_history(self):
        self.children();project=self.project();project['media']['alias'].update(sub_in=0,duration=6)
        project['sequences'][0]['tracks'][0]['clips'][0]['out']=1;project['media'].pop('sub');self.env['save_project'](project)
        before=self.raw();review=self.inspect(path=self.short(2))
        self.assertFalse(review['ok']);self.assertTrue(review['issues']);self.assertEqual(len(review['fingerprint']),64)
        with self.assertRaises(store.HTTPError):self.apply(review)
        self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('a')['undo'])

    def test_held_frame_checks_address_not_nominal_duration(self):
        project=self.project();project['sequences'][0]['tracks'][0]['clips'][0].update(hold=True,in_=1,out=600)
        self.env['save_project'](project);review=self.inspect(path=self.short(2));self.assertTrue(review['ok'],review['issues'])
        before=copy.deepcopy(self.project()['sequences']);self.apply(review);self.assertEqual(self.project()['sequences'],before)

    def test_late_saved_timeline_change_or_foreign_owner_rejects_probe(self):
        original=relink_io.inspect
        for foreign in (False,True):
            started,release=threading.Event(),threading.Event()
            def delayed(*args,**kwargs):
                result=original(*args,**kwargs);started.set();release.wait(5);return result
            async def scenario():
                task=asyncio.create_task(self.env['media_relink_inspect'](store.Request(self.body(media_id='m',path=str(self.replacement)))))
                for _ in range(1000):
                    if started.is_set():break
                    await asyncio.sleep(.001)
                self.assertTrue(started.is_set());self.assertFalse(task.done())
                if foreign:self.env['set_active_project']('b')
                else:
                    project=self.project();project['sequences'][0]['tracks'][0]['clips'][0]['out']=9;self.env['save_project'](project)
                before=(self.raw(),self.raw('b'));release.set()
                with self.assertRaises(store.HTTPError) as caught:await task
                self.assertEqual(caught.exception.status_code,409);self.assertEqual((self.raw(),self.raw('b')),before)
            with patch.object(relink_io,'inspect',delayed):run_async_check(scenario())
            self.env['set_active_project']('a')
        self.assertFalse(self.manager.values)

    def test_apply_requires_exact_fingerprint_and_context_no_old_fallback(self):
        review=self.inspect();before=self.raw()
        for values in ({'fingerprint':None},{'fingerprint':'a'*64},{'_context':{**review['context'],'revision':'old'}}):
            with self.assertRaises(store.HTTPError):self.apply(review,**values)
            self.assertEqual(self.raw(),before)
        with self.assertRaises(store.HTTPError):self.route('media_relink',self.body(media_id='m',path=review['path'],expectedSource=review['expectedSource']))
        with self.replacement.open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(store.HTTPError):self.apply(review)
        self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)

    def test_policy_change_during_reprobe_refuses_before_commit(self):
        review=self.inspect(actor='agent');original=relink_io.inspect;before=self.raw()
        def policy_changed(*args,**kwargs):
            result=original(*args,**kwargs)
            (self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
            return result
        with patch.object(relink_io,'inspect',policy_changed),self.assertRaises(store.HTTPError) as caught:
            self.apply(review,actor='agent')
        self.assertEqual(caught.exception.status_code,403);self.assertEqual(self.raw(),before)
        self.assertFalse(self.env['read_undo_history']('a')['undo']);self.assertFalse(self.manager.values)

    def test_second_reviewed_same_source_is_noop_without_history_or_queue(self):
        self.apply(self.inspect());before=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'));tasks=set(self.manager.values)
        reply=self.apply(self.inspect());self.assertFalse(reply['changed']);self.assertEqual(self.raw(),before)
        self.assertEqual(self.env['read_undo_history']('a'),history);self.assertEqual(set(self.manager.values),tasks)

    def test_undo_during_preparation_and_old_generation_cannot_publish(self):
        self.children();old=source_commands.alias_source(self.project(),self.project()['media']['alias'])
        old_task=self.env['finish_ingest']('alias',old['path'],old,str(self.root/'projects/a/project.json'),old['ingest_token'])
        reply=self.apply(self.inspect());parent_task=next(x['task']['id'] for x in reply['preparation']['tasks'] if x['media_id']=='m')
        self.assertFalse(self.env['_task_media_current'](self.manager.values[old_task['id']]['payload']))
        self.invoke('undo',{'_context':self.current()});before=self.raw()
        with self.assertRaises(ValueError):self.prepare(parent_task)
        self.assertEqual(self.raw(),before)

    def test_source_change_during_actual_preparation_rejects_late_publication(self):
        self.children();reply=self.apply(self.inspect());identity=next(x['task']['id'] for x in reply['preparation']['tasks'] if x['media_id']=='alias')
        payload=self.manager.values[identity]['payload'];original=media_preparation._run_ffmpeg;changed=False
        def replace_after_decode(*args,**kwargs):
            nonlocal changed
            result=original(*args,**kwargs)
            if not changed:
                changed=True
                with self.replacement.open('ab') as stream:stream.write(b'changed before publication')
            return result
        with patch.object(media_preparation,'_run_ffmpeg',replace_after_decode),self.assertRaises(ValueError):self.prepare(identity)
        media=self.project()['media']['alias'];self.assertFalse(media.get('wave'));self.assertFalse(media.get('proxy'))
        self.assertFalse(list((self.root/'proxies').glob(identity+'*')));self.assertFalse(list((self.root/'thumbs').glob(identity+'*')))
        self.assertEqual(media_preview.describe(self.root,self.project(),'alias')['proxy_state'],'stale_source')
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_file_changed_after_commit_requires_new_relink_before_first_proxy(self):
        review=self.inspect();actual=self.env['broadcast']
        async def mutate(event):
            await actual(event)
            with self.replacement.open('ab') as stream:stream.write(b'changed after save')
        self.env['broadcast']=mutate;reply=self.apply(review);self.env['broadcast']=actual
        self.assertTrue(reply['changed']);self.assertTrue(reply['preparation']['warnings']);self.assertFalse(self.manager.values)
        self.assertEqual(media_preview.describe(self.root,self.project(),'m')['proxy_state'],'stale_source')
        with self.assertRaises(store.HTTPError):self.route('background_media_prepare',{'_context':self.current()},'m')
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        fresh=self.inspect();self.assertTrue(fresh['ok']);self.apply(fresh);self.assertTrue(self.manager.values)

    def test_workspace_switch_during_postcommit_stamp_check_cannot_retarget_queue(self):
        review=self.inspect();original=relink_io.check;owner_root=self.env['ROOT'];checks=[]
        def switch_after_check(*args):
            original(*args);checks.append(True)
            # Apply reinspection, precommit, then the first queued preparation.
            if len(checks)==3:self.env['ROOT']=str(self.root/'changed-workspace')
        try:
            with patch.object(relink_io,'check',switch_after_check):reply=self.apply(review)
        finally:self.env['ROOT']=owner_root
        self.assertEqual(len(checks),3);self.assertTrue(reply['changed']);self.assertTrue(reply['preparation']['warnings'])
        self.assertFalse(self.manager.values);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.assertEqual(self.project()['media']['m']['path'],str(self.replacement))

    def test_relink_alias_actual_interpreted_reverse_pcm_uses_original_replacement(self):
        self.children(interpreted=True);before=self.project();source_hash=hashlib.sha256(self.source.read_bytes()).hexdigest()
        original=self.audio(before,'alias','before-relink',0,3,True)
        self.apply(self.inspect('sub'));after=self.audio(self.project(),'alias','after-relink',0,3,True)
        self.assertEqual(original,after);self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),source_hash)

    def test_lost_reply_joins_commit_and_queue_but_workspace_switch_warns(self):
        review=self.inspect();entered,release=asyncio.Event(),asyncio.Event();actual=self.env['broadcast']
        async def delay(event):entered.set();await release.wait();await actual(event)
        self.env['broadcast']=delay
        async def scenario():
            task=asyncio.create_task(self.env['media_relink'](store.Request({'_context':review['context'],'media_id':'m','path':review['path'],'fingerprint':review['fingerprint']})))
            await entered.wait();task.cancel();await asyncio.sleep(.005);self.assertFalse(task.done());task.cancel();release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        run_async_check(scenario());self.env['broadcast']=actual
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);self.assertTrue(self.manager.values)
        self.assertEqual(self.project()['media']['m']['path'],str(self.replacement))
        another=self.root/'another.mkv';shutil.copyfile(self.source,another);review=self.inspect(path=another)
        async def switch(event):await actual(event);self.env['ROOT']=str(self.root/'foreign')
        self.env['broadcast']=switch;reply=self.apply(review);self.env['ROOT']=str(self.root)
        self.assertTrue(reply['changed']);self.assertTrue(reply['preparation']['warnings'])

    def test_cancelled_actual_probe_reaps_owned_child_and_scratch(self):
        script=self.root/'slow-probe.py'
        identity_file=self.root/'slow-probe-identity.json'
        source=('import json,os,time\nfrom pathlib import Path\n'
            'identity={"pid":os.getpid()}\n'
            'if os.name=="nt":\n'
            ' import ctypes\n from ctypes import wintypes\n'
            ' kernel=ctypes.WinDLL("kernel32",use_last_error=True)\n'
            ' kernel.GetCurrentProcess.restype=wintypes.HANDLE\n'
            ' kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4\n'
            ' kernel.GetProcessTimes.restype=wintypes.BOOL\n'
            ' times=[wintypes.FILETIME() for _ in range(4)]\n'
            ' assert kernel.GetProcessTimes(kernel.GetCurrentProcess(),*[ctypes.byref(t) for t in times])\n'
            ' identity["creation_filetime"]=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime\n'
            f'path=Path({str(identity_file)!r})\n'
            'partial=path.with_suffix(".partial")\n'
            'partial.write_text(json.dumps(identity))\nos.replace(partial,path)\n'
            'time.sleep(30)\n')
        original=relink_io.inspect;holder={}
        def slow(*args,**kwargs):kwargs['ffprobe']=str(script);holder.update(ref=kwargs['proc_holder']);return original(*args,**kwargs)
        async def scenario():
            task=asyncio.create_task(self.env['media_relink_inspect'](store.Request(self.body(media_id='m',path=str(self.replacement)))))
            for _ in range(1000):
                if holder.get('ref',{}).get('proc') and identity_file.exists():break
                await asyncio.sleep(.001)
            self.assertTrue(identity_file.exists(), 'Actual probe did not acknowledge its process identity')
            child=holder['ref']['proc'];identity=json.loads(identity_file.read_text())
            self.assertEqual(identity['pid'],child.pid, 'Probe metadata writer must be the exact owned child, not a venv redirector descendant')
            self.assertIsNone(child.poll())
            if os.name=='nt':
                import ctypes
                from ctypes import wintypes
                kernel=ctypes.WinDLL('kernel32',use_last_error=True)
                kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
                kernel.GetProcessTimes.restype=wintypes.BOOL
                times=[wintypes.FILETIME() for _ in range(4)]
                self.assertTrue(kernel.GetProcessTimes(int(child._handle),*[ctypes.byref(t) for t in times]))
                self.assertEqual(identity['creation_filetime'],(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
            task.cancel();await asyncio.sleep(.005);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertIsNotNone(child.poll());self.assertNotIn('proc',holder['ref'])
            self.assertNotIn('scratch_diagnostics',holder['ref'])
        before=self.raw()
        with python_probe(script,source),patch.object(relink_io,'inspect',slow):run_async_check(scenario())
        self.assertEqual(self.raw(),before);self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_probe_output_limit_and_exit_error_leave_no_mutation(self):
        for script_text in ('import sys\nsys.stdout.write("x"*3000)\n','import sys\nsys.stderr.write("injected decoder error");sys.exit(3)\n'):
            script=self.root/'bad-probe.py'
            original=relink_io.inspect
            def fail(*args,**kwargs):kwargs['ffprobe']=str(script);return original(*args,**kwargs)
            before=self.raw()
            expected='exceeds four MiB' if 'stdout' in script_text else 'cannot be read: injected decoder error'
            with python_probe(script,script_text),patch.object(relink_io,'inspect',fail),patch.object(relink_io,'MAX_OUTPUT',2048),self.assertRaisesRegex(store.HTTPError,expected):self.inspect()
            self.assertEqual(self.raw(),before);self.assertFalse(list(self.root.glob('filmocity-render-*')))


if __name__=='__main__':unittest.main()

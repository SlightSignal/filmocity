"""Real receipt files, interrupted child processes and controlled I/O failures."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import job_history as history
import project_transaction
from export_storage import reserve_export
from project_sync import workspace_id


class JobHistory(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity history É's ")
        self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)

    def job(self, status='queued', name='Émiles export'):
        dest = reserve_export(self.root/'renders', name, {})
        job = {'id':dest['id'], 'name':dest['name'], 'out':dest['url'], 'status':status,
               'started':time.time(), 'sequence':'s', 'preset':{}, 'actor':'human',
               'review_url':'/review/job-'+dest['id'], 'review_start':0,
               'command_log':dest['url'].rsplit('.',1)[0]+'.cmd.txt',
               'context':{'workspace':workspace_id(self.root),'project':'a','revision':'captured'}}
        return job

    def paths(self, job):
        folder=self.root/'renders'/('job-'+job['id'])
        return folder/history.PRIMARY,folder/history.PREVIOUS

    def output(self, job):return self.root/job['out'].lstrip('/')

    def done(self, job):
        self.output(job).write_bytes(b'completed output fixture')
        job.update(status='done',finished=time.time(),qa={'status':'checked','flags':[]})
        history.save(self.root,job)

    def restored(self, job):return history.restore(self.root)[job['id']]

    def rewrite(self, job, mutate):
        path,_=self.paths(job);envelope=json.loads(path.read_bytes());mutate(envelope)
        payload={k:envelope[k] for k in ('version','workspace','job','output_metadata')}
        envelope['sha256']=hashlib.sha256(history._bytes(payload)).hexdigest()
        path.write_bytes(history._bytes(envelope))

    def test_completed_record_roundtrips_identity_links_settings_qa_and_unicode(self):
        job=self.job();job['preset']={'watermark':{'path':"C:/Émile's/logo.png"}}
        history.save(self.root,job);job['status']='running';history.save(self.root,job);self.done(job)
        result=self.restored(job)
        for key in ('id','out','name','review_url','preset','context','qa'):self.assertEqual(result[key],job[key])
        self.assertEqual(result['status'],'done');self.assertEqual(result['history']['status'],'restored')
        self.assertEqual(result['output_check'],'metadata_only')

    def test_each_inflight_state_is_interrupted_without_rewrite_requeue_or_output_inference(self):
        for state in ('queued','running','cancelling'):
            job=self.job(status=state);history.save(self.root,job);self.output(job).write_bytes(b'even a complete-looking file is not proof')
            primary,_=self.paths(job);before=primary.read_bytes()
            result=self.restored(job);self.assertEqual(result['status'],'error');self.assertTrue(result['recovery']['interrupted'])
            self.assertEqual(result['recovery']['previous_status'],state);self.assertIn('Start a new export',result['error'])
            self.assertEqual(primary.read_bytes(),before);self.assertTrue(self.output(job).exists())
            self.assertEqual(self.restored(job)['status'],'error')

    def test_cancelled_and_failed_states_retain_their_actual_error(self):
        for error in ('cancelled','GPU driver failure'):
            job=self.job(status='error');job['error']=error;history.save(self.root,job)
            result=self.restored(job);self.assertEqual(result['error'],error);self.assertNotIn('recovery',result)

    def test_corrupt_current_uses_previous_valid_state_and_preserves_both_files(self):
        job=self.job();history.save(self.root,job);job['status']='running';history.save(self.root,job)
        primary,backup=self.paths(job);primary.write_bytes(b'broken json');before=backup.read_bytes()
        result=self.restored(job)
        self.assertEqual(result['history']['status'],'recovered');self.assertEqual(result['recovery']['previous_status'],'queued')
        self.assertEqual(primary.read_bytes(),b'broken json');self.assertEqual(backup.read_bytes(),before)

    def test_unreadable_receipts_are_visible_errors_without_invented_output_links(self):
        job=self.job();primary,backup=self.paths(job);primary.write_bytes(b'{');backup.write_bytes(b'corrupt')
        result=self.restored(job);self.assertEqual(result['history']['status'],'unreadable')
        self.assertNotIn('out',result);self.assertNotIn('review_url',result)
        self.assertEqual(primary.read_bytes(),b'{');self.assertEqual(backup.read_bytes(),b'corrupt')

    def test_checksum_unknown_version_and_nonfinite_receipts_are_not_trusted(self):
        for mutate in (lambda e:e.update(sha256='bad'),lambda e:e.update(version=99),lambda e:e['job'].update(started=float('nan'))):
            job=self.job();history.save(self.root,job);primary,_=self.paths(job);value=json.loads(primary.read_bytes());mutate(value)
            primary.write_text(json.dumps(value),encoding='utf-8')
            self.assertEqual(self.restored(job)['history']['status'],'unreadable')

    def test_recomputed_checksum_cannot_adopt_another_job_output_or_outside_path(self):
        for output in ('/renders/old.mp4','/renders/job-'+'f'*16+'/other.mp4','/renders/../private.json'):
            job=self.job();history.save(self.root,job);self.rewrite(job,lambda e:e['job'].update(out=output))
            self.assertEqual(self.restored(job)['history']['status'],'unreadable')

    def test_receipt_copied_to_another_workspace_is_not_rebound_to_current_projects(self):
        job=self.job();self.done(job);other=self.root/'other data';other.mkdir()
        shutil.copytree(self.root/'renders',other/'renders')
        result=history.restore(other)[job['id']]
        self.assertEqual(result['history']['status'],'unreadable');self.assertNotIn('context',result)
        self.assertEqual(self.restored(job)['status'],'done')

    def test_media_deleted_or_metadata_changed_does_not_return_a_working_completed_link(self):
        for mode in ('removed','changed-size','changed-time'):
            job=self.job();self.done(job);path=self.output(job)
            if mode=='removed':path.unlink()
            elif mode=='changed-size':path.write_bytes(b'different')
            else:
                info=path.stat();os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns+2_000_000_000))
            result=self.restored(job);self.assertEqual(result['status'],'error');self.assertTrue(result['recovery']['output_unavailable'])
            self.assertEqual(result['qa']['status'],'output unavailable')
            self.assertEqual(json.loads(self.paths(job)[0].read_bytes())['job']['status'],'done')

    def test_restart_does_not_read_video_payload_or_claim_byte_verification(self):
        job=self.job();self.done(job);actual=Path.open;opened=[]
        def inspect(path,*args,**kwargs):opened.append(path);return actual(path,*args,**kwargs)
        with mock.patch.object(Path,'open',inspect):result=self.restored(job)
        self.assertNotIn(self.output(job),opened);self.assertEqual(result['output_check'],'metadata_only')

    def test_png_manifest_and_first_frame_metadata_are_checked(self):
        job=self.job();base=job['out'].rsplit('.',1)[0]+'.frames'
        job.update(out=base+'/sequence.json',output_kind='png_sequence',frames={'directory':base,'first_frame':base+'/frame_00001.png','pattern':'frame_%05d.png'})
        self.output(job).parent.mkdir();first=self.root/job['frames']['first_frame'].lstrip('/');first.write_bytes(b'fixture frame')
        self.done(job);self.assertEqual(self.restored(job)['status'],'done')
        first.unlink();self.assertEqual(self.restored(job)['status'],'error')

    def test_restored_preview_cannot_reactivate_monitor_or_reuse_old_request_identity(self):
        job=self.job();job['preview']={'context':job.pop('context'),'signature':'old'};job['preview_request']='old-request';self.done(job)
        result=self.restored(job);self.assertEqual(result['status'],'done');self.assertTrue(result['restored_preview'])
        self.assertEqual(result['context']['project'],'a');self.assertNotIn('preview',result);self.assertNotIn('preview_request',result)

    def test_invalid_oversize_and_nonserializable_updates_preserve_previous_receipt(self):
        job=self.job();before=history.save(self.root,job)
        for change in ({'started':float('nan')},{'preset':{'value':object()}},{'error':'x'*history.MAX_BYTES}):
            modified={**job,**change}
            with self.assertRaises((ValueError,TypeError)):history.save(self.root,modified)
            self.assertEqual(self.paths(job)[0].read_bytes(),before)

    def test_backup_or_primary_write_failure_keeps_last_valid_record_and_is_visible(self):
        for where in (history.PRIMARY,history.PREVIOUS):
            job=self.job();before=history.save(self.root,job);job['status']='running';actual=history.write_atomic
            def fail(path,raw):
                if path.name==where:raise PermissionError('injected sharing denial')
                return actual(path,raw)
            with mock.patch.object(history,'write_atomic',side_effect=fail):
                self.assertIsNone(history.remember(self.root,job));self.assertEqual(job['history']['status'],'error')
            self.assertEqual(self.paths(job)[0].read_bytes(),before)
            history.remember(self.root,job);self.assertEqual(job['history']['status'],'saved')

    def test_old_or_corrupt_records_are_not_overwritten_by_late_writes(self):
        job=self.job();history.save(self.root,job);self.done(job);primary,_=self.paths(job);before=primary.read_bytes()
        job['status']='running'
        with self.assertRaisesRegex(history.HistoryError,'terminal'):history.save(self.root,job)
        self.assertEqual(primary.read_bytes(),before)
        primary.write_bytes(b'corrupt')
        with self.assertRaises(history.HistoryError):history.save(self.root,job)
        self.assertEqual(primary.read_bytes(),b'corrupt')

    def test_windows_sharing_retry_contract_keeps_one_complete_receipt(self):
        job=self.job();actual=os.replace;attempts=[]
        def sharing(source,target):
            attempts.append(str(target))
            if len(attempts)<3:
                error=PermissionError('simulated Windows sharing violation');error.winerror=32;raise error
            return actual(source,target)
        with mock.patch.object(project_transaction.os,'replace',side_effect=sharing), mock.patch.object(project_transaction.time,'sleep'):
            history.save(self.root,job)
        self.assertEqual(len(attempts),3)
        self.assertEqual(self.restored(job)['recovery']['previous_status'],'queued')
        self.assertFalse(list(self.paths(job)[0].parent.glob('.commit-*.tmp')))

    def test_initial_rollback_only_removes_the_exact_unqueued_receipt(self):
        job=self.job();receipt=history.save(self.root,job);primary,_=self.paths(job)
        history.discard_unqueued(self.root,job,receipt);self.assertFalse(primary.exists())
        receipt=history.save(self.root,job);primary.write_bytes(b'external change')
        history.discard_unqueued(self.root,job,receipt);self.assertEqual(primary.read_bytes(),b'external change')

    def test_legacy_files_and_unrecorded_folders_are_left_unadopted(self):
        job=self.job();self.output(job).write_bytes(b'no receipt');old=self.root/'renders/old.mp4';old.write_bytes(b'legacy')
        self.assertEqual(history.restore(self.root),{});self.assertEqual(old.read_bytes(),b'legacy')

    @unittest.skipIf(os.name=='nt', 'Native symlink/junction privilege acceptance is separate on Windows')
    def test_linked_receipts_are_not_read_or_overwritten(self):
        job=self.job();primary,_=self.paths(job);outside=self.root/'keep.json';outside.write_bytes(b'outside')
        primary.symlink_to(outside)
        self.assertEqual(self.restored(job)['history']['status'],'unreadable')
        with self.assertRaises(history.HistoryError):history.save(self.root,job)
        self.assertEqual(outside.read_bytes(),b'outside')

    def test_actual_process_exit_before_and_after_terminal_publication(self):
        child = r'''
import json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import job_history as h
root=Path(sys.argv[2]);job=json.loads(Path(sys.argv[3]).read_text(encoding='utf-8'))
actual=h.write_atomic
def crash(path,raw):
    actual(path,raw)
    if path.name==sys.argv[4]:os._exit(73)
h.write_atomic=crash
h.save(root,job)
raise RuntimeError('Expected the child to exit inside publication')
'''
        for phase,expected in ((history.PREVIOUS,'error'),(history.PRIMARY,'done')):
            job=self.job(status='running');history.save(self.root,job);self.output(job).write_bytes(b'completed before checkpoint')
            job.update(status='done',finished=time.time());config=self.root/(job['id']+'.json');config.write_text(json.dumps(job),encoding='utf-8')
            result=subprocess.run([sys.executable,'-c',child,str(ROOT/'backend'),str(self.root),str(config),phase],capture_output=True,timeout=15)
            self.assertEqual(result.returncode,73,result.stderr)
            restored=self.restored(job);self.assertEqual(restored['status'],expected)
            self.assertEqual(self.output(job).read_bytes(),b'completed before checkpoint')


if __name__=='__main__':unittest.main(verbosity=2)

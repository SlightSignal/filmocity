"""Windows project-save sharing failures preserve the previous file and retry within bounds."""
import copy, json, os, shutil, sys, unittest, uuid
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
os.environ.setdefault('FILMOCITY_ROOT',str(ROOT/'.project-save-tests/default'))
import server

class ProjectSave(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'.project-save-tests'/uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.current=self.root/'project.json'
        self.previous={'version':3,'name':'Previous project'}
        self.current.write_text(json.dumps(self.previous),encoding='utf-8')
        self.next={'version':3,'name':'New project'}
        self.replace=os.replace
    def tearDown(self):
        shutil.rmtree(self.root)
    def error(self,winerror=5):
        error=PermissionError(13,'Injected sharing failure')
        if winerror is not None:error.winerror=winerror
        return error
    def save(self):server.save_project(copy.deepcopy(self.next),str(self.current))
    def test_transient_windows_sharing_retries_and_publishes_complete_json(self):
        calls=[]
        def replace(source,destination):
            calls.append((source,destination))
            self.assertEqual(json.loads(self.current.read_text(encoding='utf-8')),self.previous)
            if len(calls)<3:raise self.error(32)
            return self.replace(source,destination)
        with patch.object(server.os,'replace',side_effect=replace),patch.object(server.time,'sleep') as sleep:
            self.save()
        self.assertEqual(len(calls),3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[.02,.04])
        self.assertEqual(json.loads(self.current.read_text(encoding='utf-8'))['name'],'New project')
        self.assertFalse(Path(str(self.current)+'.tmp').exists())
    def test_persistent_windows_denial_is_bounded_and_preserves_old_project(self):
        with patch.object(server.os,'replace',side_effect=self.error()) as replace,patch.object(server.time,'sleep') as sleep:
            with self.assertRaises(PermissionError):self.save()
        self.assertEqual(replace.call_count,6)
        self.assertEqual([c.args[0] for c in sleep.call_args_list],[.02,.04,.08,.16,.2])
        self.assertEqual(json.loads(self.current.read_text(encoding='utf-8')),self.previous)
        self.assertEqual(json.loads(Path(str(self.current)+'.tmp').read_text(encoding='utf-8'))['name'],'New project')
    def test_other_permissions_and_filesystem_failures_do_not_retry(self):
        for error in [self.error(None),self.error(123),OSError('Injected disk failure')]:
            with self.subTest(error=repr(error)),patch.object(server.os,'replace',side_effect=error) as replace,patch.object(server.time,'sleep') as sleep:
                with self.assertRaises(OSError):self.save()
            self.assertEqual(replace.call_count,1)
            sleep.assert_not_called()
            self.assertEqual(json.loads(self.current.read_text(encoding='utf-8')),self.previous)
    def test_real_windows_reader_release_allows_save(self):
        if os.name!='nt':self.skipTest('Windows sharing contract')
        reader=self.current.open('rb')
        try:
            with patch.object(server.time,'sleep',side_effect=lambda _:reader.close()) as sleep:
                self.save()
            self.assertGreaterEqual(sleep.call_count,1,'Real open reader must refuse the first Windows replace')
        finally:reader.close()
        self.assertEqual(json.loads(self.current.read_text(encoding='utf-8'))['name'],'New project')

if __name__=='__main__':unittest.main(verbosity=2)

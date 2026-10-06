"""Actual project commits/recovery history for source-color form operations."""
import copy
import unittest
import test_project_sync as store


class SourceHistory(store.ProjectStoreFixture):
    def test_source_color_edit_undo_redo_preserves_other_source_metadata(self):
        before=copy.deepcopy(self.env['load_project']()['media']['A'])
        value={**before,'input_transform':'slog3','hdr_peak_nits':400,'vendor_color_note':'retained'}
        result=self.invoke('patch_project',{'_context':self.current(),'ops':[{'op':'set','path':'/media/A','value':value}],'tool':'source_color','reason':'source color settings'})
        self.assertTrue(result['ok']);self.assertEqual(self.env['load_project']()['media']['A'],value)
        self.assertTrue(self.invoke('undo',{'_context':self.current()})['ok']);self.assertEqual(self.env['load_project']()['media']['A'],before)
        self.assertTrue(self.invoke('redo',{'_context':self.current()})['ok']);self.assertEqual(self.env['load_project']()['media']['A'],value)

    def test_stale_source_color_commit_cannot_write_to_another_project(self):
        context=self.current();value={**self.env['load_project']()['media']['A'],'input_transform':'vlog'}
        a,b=self.raw('a'),self.raw('b');self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError) as error:
            self.invoke('patch_project',{'_context':context,'ops':[{'op':'set','path':'/media/A','value':value}],'tool':'source_color'})
        self.assertEqual(error.exception.status_code,409);self.assertEqual((self.raw('a'),self.raw('b')),(a,b))


if __name__=='__main__':unittest.main()

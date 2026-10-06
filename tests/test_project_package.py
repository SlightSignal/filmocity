"""Portable folders with real files/rendering, cancellation and publication faults.

No HTTP server, native browser or Windows device is exercised by this suite.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from link_fixture import file_link, directory_link
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import project_package as package
import project_resources as resources
import render
from PIL import Image
from preflight import inspect_resources
from render_context import RenderContext
from background_tasks import TaskCancelled


def fixture(root):
    sources=root/'originals';sources.mkdir()
    picture=sources/'still É.png';Image.new('RGB',(160,90),(90,45,135)).save(picture)
    font=sources/'font.ttf';shutil.copyfile(render.font_file(),font)
    lut=sources/'look.cube';lut.write_text('LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n')
    style={'text':'Package 42%','font':str(font),'weight':'bold','size':16}
    p={'version':3,'id':'p','name':'Transfer É','media':{'m':{'id':'m','path':str(picture),'is_image':True,'has_video':True,'has_audio':False}},
       'sequences':[{'id':'s','name':'Original','width':160,'height':90,'fps':24,'duration':.25,'tracks':[
           {'id':'v','kind':'video','index':1,'clips':[{'id':'c','media_id':'m','start':0,'in_':0,'out':.25,'color':{'lut':str(lut)},'title':style}]}]}]}
    return p


class Packages(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='Filmocity package É ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.project=fixture(self.root);self.original=copy.deepcopy(self.project)
        self.out=self.root/'packages';self.library=self.root/'library';self.seq=self.project['sequences'][0]
    def export(self,identity='a'*32,**kw):return package.export_package(self.project,self.out,identity,**kw)
    def import_(self,receipt,identity='b'*32,**kw):return package.import_package(receipt['manifest'],self.library,identity,**kw)
    def read(self,receipt):return json.loads((Path(receipt['folder'])/'project.json').read_text())
    def tamper(self,receipt,fn):
        root=Path(receipt['folder']);doc=json.loads((root/'project.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
        fn(doc,manifest);raw=package.packed(doc);(root/'project.json').write_bytes(raw);manifest['project_sha256']=package.digest(raw);(root/'manifest.json').write_bytes(package.packed(manifest))
    def assert_unpublished(self):
        self.assertFalse(list(self.out.glob('package-*')));self.assertFalse(list(self.out.glob('.pack-*')))
        self.assertEqual(self.project,self.original)

    def test_roundtrip_pins_fonts_luts_and_originals_without_mutating_source_project(self):
        before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.root/'originals').iterdir()}
        receipt=self.export();self.assertEqual(self.project,self.original);self.assertEqual(receipt['files'],3)
        doc,manifest,checksum=package.verify_package(receipt['manifest']);self.assertEqual(checksum,receipt['manifest_sha256'])
        self.assertEqual(len(manifest['bindings']),3)
        for obj,key,_,_ in resources.references(doc):self.assertFalse(os.path.isabs(obj[key]));self.assertTrue(obj[key].startswith('resources/'))
        imported=self.import_(receipt);result=self.read(imported)
        self.assertEqual(result['name'],self.project['name']);self.assertNotEqual(result['id'],self.project['id'])
        for obj,key,_,_ in resources.references(result):self.assertTrue(Path(obj[key]).is_file());self.assertTrue(Path(obj[key]).is_relative_to(self.library))
        for path,sha in before.items():self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),sha)
        self.assertFalse(any(i['severity']=='error' for i in inspect_resources(result,'s')['issues']))

    def test_real_render_survives_removed_originals_and_removed_transfer_folder(self):
        def picture(proj,name):
            target=self.root/name
            with RenderContext(scratch_parent=str(self.root)) as context:render.render_frame(proj,'s',.05,str(target),context=context)
            with Image.open(target) as im:return im.convert('RGB').tobytes()
        before=picture(self.project,'before.png');receipt=self.export();imported=self.import_(receipt)
        shutil.rmtree(self.root/'originals');shutil.rmtree(self.out)
        result=self.read(imported);after=picture(result,'after.png');self.assertEqual(after,before)
        with patch.object(render,'font_file',side_effect=AssertionError('Receiving system font must not be used')):
            self.assertTrue(resources.style_font(result['sequences'][0]['tracks'][0]['clips'][0]['title']).startswith(str(self.library)))

    def test_numbered_frames_and_subclip_parents_are_collected_once_with_start_number(self):
        for n in (12,13):Image.new('RGB',(16,16),(n,0,0)).save(self.root/f'originals/frame{n:04d}.png')
        m=self.project['media']['m'];m.update(path=str(self.root/'originals/frame%04d.png'),sequence_frames=2,input_opts=['-start_number','12','-framerate','24000/1001'])
        self.project['media']['sub']={**m,'id':'sub','subclip_of':'m','path':'obsolete.mov','sequence_frames':999}
        receipt=self.export();self.assertEqual(receipt['files'],4)
        result=self.read(self.import_(receipt));self.assertEqual(result['media']['m']['path'],result['media']['sub']['path'])
        self.assertEqual(result['media']['sub']['sequence_frames'],2)
        from preflight import media_files
        self.assertEqual([Path(p).read_bytes() for p in media_files(result['media']['m'])],[Path(p).read_bytes() for p in media_files(m)])

    def test_nested_inactive_graphics_stabilization_and_camera_transform_resources(self):
        graphic=self.root/'originals/graphic.png';Image.new('RGB',(8,8),'red').save(graphic)
        stab=self.root/'originals/stabilization.trf';stab.write_bytes(b'transform data')
        self.project['media']['m'].update(stab_trf=str(stab),input_transform='slog3')
        self.seq['tracks'].append({'id':'off','kind':'video','index':2,'muted':True,'clips':[{'id':'g','graphic':{'layers':[{'kind':'image','path':str(graphic)},{'kind':'text','text':'Nested'}]}}]})
        self.seq['captions']=[{'id':'cap','text':'Caption','start':0,'end':.2}]
        self.project['sequences'].append({**copy.deepcopy(self.seq),'id':'nested'})
        receipt=self.export();result=self.read(self.import_(receipt))
        self.assertEqual(len(list(resources.font_styles(result))),6)
        self.assertIn('resources',resources.input_lut(result['media']['m']))
        for style in resources.font_styles(result):self.assertIsNotNone(resources.font_binding(style))
        self.assertEqual(Path(result['media']['m']['stab_trf']).read_bytes(),stab.read_bytes())
        with patch.object(render,'INPUT_LUTS','missing-bundled-folder'):
            self.assertIn('resources',render.input_transform_chain(result['media']['m'])[0])

    def test_derived_media_and_task_state_are_omitted_but_editing_metadata_is_kept(self):
        m=self.project['media']['m'];m.update({key:'external' for key in package.DERIVED});m['proxy_settings']={'width':640};m['vendor']={'keep':7}
        receipt=self.export();result=self.read(self.import_(receipt))['media']['m']
        self.assertFalse(any(key in result for key in package.DERIVED));self.assertEqual(result['proxy_settings'],m['proxy_settings']);self.assertEqual(result['vendor'],m['vendor'])
        self.assertTrue(receipt['omissions'])

    def test_no_file_is_required_for_synthetic_media(self):
        self.project['media']['generated']={'id':'generated','synthetic':{'kind':'black'},'path':'legacy-placeholder'}
        receipt=self.export();doc,_,_=package.verify_package(receipt['manifest']);self.assertNotIn('path',doc['media']['generated'])

    def test_missing_original_or_pinned_font_or_lut_refuses_publication(self):
        variants=[lambda p:p['media']['m'].update(path='missing'),lambda p:p['media']['m'].pop('path'),
                  lambda p:p['sequences'][0]['tracks'][0]['clips'][0]['title'].update(font='C:/missing/font.ttf'),
                  lambda p:p['media']['m'].update(input_transform='slog3',input_transform_resource={'name':'slog3','path':'missing'})]
        for change in variants:
            self.project=copy.deepcopy(self.original);change(self.project);saved=copy.deepcopy(self.project)
            with self.assertRaises((OSError,ValueError,RuntimeError)):self.export()
            self.assertEqual(self.project,saved);self.assertFalse(list(self.out.glob('package-*')))

    def test_export_cancel_during_copy_removes_private_staging_only(self):
        big=self.root/'originals/big.bin';big.write_bytes(b'x'*(4*1024*1024));self.project['media']['m']['path']=str(big)
        partial=[]
        def cancel():
            for path in self.out.glob('.pack-*/resources/*/big.bin'):
                if path.stat().st_size:
                    partial.append(path.stat().st_size)
                    raise TaskCancelled('Cancelled during resource copy')
        with self.assertRaises(TaskCancelled):self.export(check=cancel)
        self.assertTrue(partial);self.assertLess(partial[0],big.stat().st_size)
        self.assertFalse(list(self.out.iterdir()));self.assertEqual(big.stat().st_size,4*1024*1024)

    def test_copy_or_publication_failure_preserves_previous_folders_and_source(self):
        old=self.export();old_bytes=Path(old['manifest']).read_bytes()
        for target in ('_verified_file','os.rename'):
            with patch('project_package.'+target,side_effect=OSError('disk full / sharing violation')):
                with self.assertRaises(OSError):self.export('c'*32)
            self.assertFalse(list(self.out.glob('.pack-*')));self.assertEqual(Path(old['manifest']).read_bytes(),old_bytes)
        self.assertEqual(self.project,self.original)

    def test_existing_export_and_import_destinations_are_never_replaced(self):
        receipt=self.export();imported=self.import_(receipt);saved=(Path(imported['folder'])/'project.json').read_bytes()
        with self.assertRaisesRegex(package.PackageError,'already exists'):self.export()
        with self.assertRaisesRegex(package.PackageError,'already exists'):self.import_(receipt)
        self.assertEqual((Path(imported['folder'])/'project.json').read_bytes(),saved)

    def test_corrupt_source_copy_is_refused_before_import_project_exists(self):
        receipt=self.export();file=next((Path(receipt['folder'])/'resources').rglob('*.png'));file.write_bytes(b'bad')
        with self.assertRaisesRegex(package.PackageError,'checksum'):self.import_(receipt)
        self.assertFalse(self.library.exists())

    def test_project_checksum_is_required(self):
        receipt=self.export();path=Path(receipt['folder'])/'project.json';path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(package.PackageError,'checksum'):self.import_(receipt)

    def test_traversal_absolute_windows_devices_and_alternate_streams_are_refused(self):
        for path in ('../outside','/absolute','resources/../escape','resources/CON.txt','resources/name.','resources/C:stream','resources/a\\b','resources//empty'):
            with self.subTest(path=path),self.assertRaises(package.PackageError):package.safe_file(self.root,path)

    def test_linked_file_in_package_is_refused(self):
        receipt=self.export();root=Path(receipt['folder']);picture=next((root/'resources').rglob('*.png'));picture.unlink();file_link(picture,self.project['media']['m']['path'])
        with self.assertRaisesRegex(package.PackageError,'Linked'):self.import_(receipt)

    def test_linked_destination_is_refused(self):
        self.out.mkdir();link=self.root/'linked';directory_link(link,self.out)
        with self.assertRaisesRegex(package.PackageError,'Linked'):package.export_package(self.project,link/'child','c'*32)
        self.assertEqual(list(self.out.iterdir()),[])

    def test_missing_binding_and_uncollected_external_resources_are_refused(self):
        receipt=self.export();self.tamper(receipt,lambda d,m:m['bindings'].pop())
        with self.assertRaisesRegex(package.PackageError,'uncollected'):self.import_(receipt)
        receipt=self.export('c'*32);self.tamper(receipt,lambda d,m:d['media']['m'].update(path=self.project['media']['m']['path']))
        with self.assertRaisesRegex(package.PackageError,'binding'):self.import_(receipt)

    def test_case_alias_duplicate_inventory_and_unknown_input_options_are_refused(self):
        receipt=self.export();self.tamper(receipt,lambda d,m:m['files'].append({**m['files'][0],'path':m['files'][0]['path'].upper()}))
        with self.assertRaisesRegex(package.PackageError,'Duplicate'):self.import_(receipt)
        self.project['media']['m']['input_opts']=['-i','external.mov']
        with self.assertRaisesRegex(package.PackageError,'numeric'):self.export('c'*32)

    def test_changed_package_during_import_is_not_admitted(self):
        receipt=self.export()
        def change(*args):Path(receipt['manifest']).write_bytes(Path(receipt['manifest']).read_bytes()+b' ')
        with self.assertRaisesRegex(package.PackageError,'changed'):self.import_(receipt,progress=change)
        self.assertFalse(list(self.library.iterdir()))

    def test_import_cancel_cleans_private_files_and_keeps_transfer_package(self):
        receipt=self.export();checksum=package.digest(Path(receipt['manifest']).read_bytes())
        def cancel(*args):raise TaskCancelled('stop import')
        with self.assertRaises(TaskCancelled):self.import_(receipt,progress=cancel)
        self.assertFalse(list(self.library.iterdir()));self.assertEqual(package.digest(Path(receipt['manifest']).read_bytes()),checksum)

    def test_failed_final_project_write_retains_resources_and_receipt_for_recovery(self):
        receipt=self.export();write=package.write_atomic
        def fail(path,data):
            if Path(path).name=='project.json':raise OSError('project save failed')
            return write(path,data)
        with patch.object(package,'write_atomic',side_effect=fail):
            with self.assertRaisesRegex(package.PackageError,'retained at'):self.import_(receipt)
        kept=self.library/('import-'+'b'*32)/'content';self.assertTrue((kept/'import-receipt.json').is_file());self.assertTrue(list((kept/'resources').rglob('*.png')))
        saved=(kept/'project-snapshot.json').read_bytes();receipt=json.loads((kept/'import-receipt.json').read_text())
        self.assertEqual(package.digest(saved),receipt['project_snapshot_sha256']);doc=package.parse_project(saved)
        self.assertFalse(any(i['severity']=='error' for i in inspect_resources(doc,'s')['issues']))

    def test_camera_lut_and_font_selection_changes_release_only_the_inactive_binding(self):
        style={'font':'One','weight':'bold','font_resource':{'family':'One','weight':'bold','path':'pinned.ttf'}}
        self.assertEqual(resources.style_font(style),'pinned.ttf');style['font']='Two'
        with patch.object(render,'font_file',return_value='system.ttf'):self.assertEqual(resources.style_font(style),'system.ttf')
        m={'input_transform':'vlog','input_transform_resource':{'name':'slog3','path':'pinned.cube'}}
        self.assertTrue(resources.input_lut(m).endswith('vlog.cube'))

    def test_subclip_parent_cycles_and_missing_pin_are_rejected(self):
        self.project['media']['m']['subclip_of']='m'
        with self.assertRaisesRegex(package.PackageError,'cyclic'):self.export()
        self.project=copy.deepcopy(self.original);receipt=self.export()
        self.tamper(receipt,lambda d,m:d['sequences'][0]['tracks'][0]['clips'][0]['title']['font_resource'].pop('path'))
        with self.assertRaises(package.PackageError):self.import_(receipt)

    def test_patch_addresses_are_not_files_but_op_fields_cannot_hide_media_or_font_files(self):
        receipt=self.export()
        def corrupt(doc,manifest):
            font=doc['sequences'][0]['tracks'][0]['clips'][0]['title']['font_resource'];old=font['path'];font.update(op='replace',path='C:/outside.ttf')
            manifest['bindings']=[b for b in manifest['bindings'] if b['path']!=old]
            manifest['files']=[f for f in manifest['files'] if f['path']!=old]
        self.tamper(receipt,corrupt)
        with self.assertRaisesRegex(package.PackageError,'uncollected'):self.import_(receipt)
        project={'ops':[{'op':'replace','path':'/media/m/path','value':{'path':'file.mov'}}],'media':{'m':{'op':'replace','path':'source.mov'}}}
        paths=[obj[key] for obj,key,_,_ in resources.references(project)]
        self.assertEqual(paths,['file.mov','source.mov'])

    def test_missing_pinned_font_and_camera_lut_fail_preflight_after_import(self):
        self.project['media']['m']['input_transform']='slog3'
        receipt=self.export();doc=self.read(self.import_(receipt))
        font=next(resources.font_styles(doc));Path(resources.style_font(font)).unlink();Path(resources.input_lut(doc['media']['m'])).unlink()
        codes={i['code'] for i in inspect_resources(doc,'s')['issues'] if i['severity']=='error'}
        self.assertIn('missing_font',codes);self.assertIn('missing_input_lut',codes)
        with self.assertRaisesRegex(ValueError,'unavailable'):render.input_transform_chain(doc['media']['m'])

    def test_existing_package_reexports_without_system_font_or_bundled_camera_lut(self):
        self.project['media']['m']['input_transform']='vlog'
        receipt=self.export();doc=self.read(self.import_(receipt));shutil.rmtree(self.root/'originals')
        with patch.object(render,'font_file',side_effect=AssertionError('Must use collected fonts')),patch.object(render,'INPUT_LUTS','missing'):
            result=package.export_package(doc,self.out,'c'*32)
            verified,_,_=package.verify_package(result['manifest']);self.assertEqual(len(list(resources.font_styles(verified))),1)


if __name__=='__main__':unittest.main()

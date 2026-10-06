"""Verified portable project folders. No edits to the source project/library.

Each export/import owns private staging and a new exclusive publication folder.
Import copies only manifest-listed, contained regular files; it never extracts
archive paths or adopts external project paths. Caches/history are explicit
omissions, while all current project resource fields are collected.
"""
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from media_collection import _portable_name, _verified_file
from preflight import media_files
from project_recovery import parse_project
from project_transaction import write_atomic
from cache_paths import linked as is_link
import project_resources as resources
from input_options import validated_input_options

MAX_JSON = 32 * 1024 * 1024
DERIVED = ('proxy', 'proxy_info', 'proxy_source', 'proxy_signature', 'proxy_status', 'thumb', 'thumbnail', 'filmstrip', 'strip', 'waveform', 'wave', 'status', 'ingest_status', 'ingest_error', 'ingest_token', 'task_id')
OMISSIONS = ['Derived proxies, thumbnails, filmstrips and waveforms; regenerate on the receiving device.',
             'Workspace preferences, export jobs, edit/undo history, backups, task receipts and training/event logs. The package contains the captured project snapshot.',
             'Machine-local Relink file identities; package checksums verify the transferred originals.',
             'External applications, plugins, codecs and system/display configuration. The receiving Filmocity build must support the project.']


class PackageError(ValueError): pass


def packed(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode('utf-8')
def digest(raw): return hashlib.sha256(raw).hexdigest()


def read_json(path):
    with open(path, 'rb') as stream: raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON: raise PackageError('Package JSON exceeds the 32 MiB limit')
    value = json.loads(raw)
    if not isinstance(value, dict): raise PackageError('Package JSON must be an object')
    return value, raw


def unlinked(path):
    path = Path(path).absolute()
    if any(is_link(part) for part in (path, *path.parents)):
        raise PackageError('Linked package/project folders are not supported')


def safe_file(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise PackageError('Invalid package file path')
    rel = PurePosixPath(name)
    if rel.is_absolute() or any(p in ('', '.', '..') for p in name.split('/')): raise PackageError('Package paths must stay inside the package folder')
    current = Path(root); unlinked(current)
    for part in rel.parts:
        if _portable_name(part) != part: raise PackageError('Package filename is not Windows-portable')
        current = current / part
        if is_link(current): raise PackageError('Linked package files/folders are not supported')
    return current


def validate_options(media):
    try:
        validated_input_options(media.get('input_opts'))
    except ValueError as error:
        raise PackageError(str(error)) from error


def _check_accepted_sources(project, check):
    from source_relink_io import check_accepted
    for media in project.get('media', {}).values():
        check()
        if media.get('synthetic') or media.get('subclip_of'): continue
        try: check_accepted(media)
        except (OSError, ValueError, TypeError) as error:
            raise PackageError('Cannot package an unreviewed source change: ' + str(error)) from error


def prepare(project, *, check=lambda: None):
    from PIL import ImageFont
    _check_accepted_sources(project, check)
    result = copy.deepcopy(project); warnings = []; _parent_sources(result)
    for media in result.get('media', {}).values():
        check()
        if not media.get('synthetic') and (not isinstance(media.get('path'), str) or not media['path']):
            raise PackageError('Original media has no source path')
        validate_options(media)
        for key in DERIVED: media.pop(key, None)
        # Path/stat acceptance belongs to the exporting device. The package's
        # content hashes establish transfer integrity on the receiving device.
        media.pop('source_relink_basis', None)
    for style in resources.font_styles(result):
        check()
        selected=style.get('font')
        if isinstance(selected,str) and ('/' in selected or '\\' in selected) and not resources.font_binding(style) and not os.path.isfile(selected):
            raise PackageError('An explicit project font is missing: '+selected)
        path = resources.style_font(style)
        if not path or not os.path.isfile(path): raise PackageError('A required project font is unavailable: ' + str(path))
        family = style.get('font') or ''; actual = ImageFont.truetype(path, 24).getname()[0]
        if family and (os.path.isabs(family) or '/' in family or '\\' in family):
            style['font'] = family = actual
        elif family and re.sub(r'\W', '', actual).lower() != re.sub(r'\W', '', family).lower():
            warnings.append(f'Font {family} resolves to {actual}; the resolved file is pinned in this package.')
        style['font_resource'] = {'family': family, 'weight': style.get('weight', 'bold'), 'path': path}
    for media in resources.objects(result):
        check()
        if media.get('input_transform') not in (None, '', 'none'):
            path=resources.input_lut(media)
            if not path or not os.path.isfile(path):raise PackageError('A required camera LUT is unavailable: '+str(path))
            media['input_transform_resource'] = {'name': media['input_transform'], 'path': path}
    return result, sorted(set(warnings))


def _parent_sources(project):
    media = project.get('media', {})
    for key, item in media.items():
        if not item.get('subclip_of'): continue
        seen = {key}; parent = item['subclip_of']
        while True:
            if parent in seen or parent not in media: raise PackageError('Subclip has a missing or cyclic parent: ' + str(key))
            seen.add(parent)
            if not media[parent].get('subclip_of'): break
            parent = media[parent]['subclip_of']
        item['path'] = media[parent].get('path', '')
        # Current parent source interpretation is authoritative for subclips.
        for field in ('stab_trf', 'input_transform', 'input_transform_resource', 'sequence_frames', 'input_opts'):
            if field in media[parent]: item[field] = copy.deepcopy(media[parent][field])
            else: item.pop(field, None)


def export_package(project, destination, identity, *, check=lambda: None, progress=lambda *args: None, publish=lambda f: f()):
    if not re.fullmatch('[a-f0-9]{32}', identity): raise PackageError('Invalid package identity')
    root = Path(destination).absolute();unlinked(root);root.mkdir(parents=True, exist_ok=True)
    final = root / ('package-' + identity)
    if final.exists(): raise PackageError('Package destination already exists; preserve it and use a new request')
    stage = Path(tempfile.mkdtemp(prefix='.pack-', dir=root));published = False
    try:
        check();document,warnings = prepare(project, check=check)
        refs = list(resources.references(document));files=[];groups={};bindings=[]
        for index,(obj,key,pointer,kind) in enumerate(refs):
            check();source=obj[key]
            if key == 'path' and obj.get('synthetic'):
                obj.pop(key, None); continue
            paths = media_files(obj) if key == 'path' and obj.get('sequence_frames') else [source]
            token=(os.path.normcase(os.path.realpath(source)),tuple(paths) if key=='path' and obj.get('sequence_frames') else ())
            if token not in groups:
                folder=f'resources/item-{len(groups)+1:05d}'
                basename=('frame%09d'+Path(source).suffix) if obj.get('sequence_frames') and key=='path' else _portable_name(Path(source).name)
                relative=folder+'/'+basename;targets=media_files({**obj,'path':relative}) if obj.get('sequence_frames') and key=='path' else [relative]
                (stage/folder).mkdir(parents=True)
                for original,target in zip(paths,targets):
                    check();details=_verified_file(original,stage/target,check=check)
                    files.append({'path':target,**details})
                groups[token]=(relative,targets)
            relative,targets=groups[token];obj[key]=relative
            bindings.append({'pointer':list(pointer),'path':relative,'files':targets})
            progress('Collecting project resources',(index+1)/max(1,len(refs)),f'{index+1} of {len(refs)} resource references verified')
        _check_accepted_sources(project, check)
        raw=packed(document);parse_project(raw)
        if len(raw)>MAX_JSON: raise PackageError('Project snapshot exceeds package size limit')
        write_atomic(stage/'project.json',raw)
        manifest={'format':'filmocity-project-package','version':1,'id':identity,'project':'project.json','project_sha256':digest(raw),
                  'source_project_sha256':digest(packed(project)),'files':files,'bindings':bindings,'warnings':warnings,'omissions':OMISSIONS}
        payload=packed(manifest)
        if len(payload)>MAX_JSON: raise PackageError('Package manifest exceeds the 32 MiB limit')
        write_atomic(stage/'manifest.json',payload);check()
        def commit():
            nonlocal published
            _check_accepted_sources(project, check)
            final.mkdir()
            try:os.rename(stage,final/'content')
            except BaseException:final.rmdir();raise
            published=True
            return {'kind':'package','folder':str(final/'content'),'manifest':str(final/'content/manifest.json'),
                    'manifest_sha256':digest(payload),'files':len(files),'bytes':sum(f['bytes'] for f in files),'warnings':warnings,'omissions':OMISSIONS}
        return publish(commit)
    finally:
        if not published and stage.exists(): shutil.rmtree(stage)


def verify_package(manifest_path, *, check=lambda: None):
    path=Path(manifest_path).absolute();root=path.parent
    if path.name!='manifest.json': raise PackageError('Choose the package manifest.json file')
    manifest,raw=read_json(safe_file(root,'manifest.json'))
    if manifest.get('format')!='filmocity-project-package' or manifest.get('version')!=1 or manifest.get('project')!='project.json':
        raise PackageError('Unsupported Filmocity project package')
    document,project_raw=read_json(safe_file(root,'project.json'));parse_project(project_raw)
    if digest(project_raw)!=manifest.get('project_sha256'):raise PackageError('Packaged project checksum mismatch')
    files=manifest.get('files');bindings=manifest.get('bindings')
    if not isinstance(files,list) or not isinstance(bindings,list):raise PackageError('Package inventory is missing')
    names=set(); spelling=set()
    for entry in files:
        check()
        if not isinstance(entry,dict) or not isinstance(entry.get('path'),str):raise PackageError('Invalid package inventory entry')
        if type(entry.get('bytes')) is not int or entry['bytes'] <= 0 or not isinstance(entry.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',entry['sha256']):
            raise PackageError('Invalid package resource checksum/size')
        name=entry['path'];key=name.casefold()
        if key in names or not name.startswith('resources/'):raise PackageError('Duplicate or invalid package inventory path')
        names.add(key);spelling.add(name);source=safe_file(root,name);actual=_verified_file(source,check=check)
        if actual!={'bytes':entry.get('bytes'),'sha256':entry.get('sha256')}:raise PackageError('Resource checksum mismatch: '+name)
    expected={tuple(p):value[key] for value,key,p,_ in resources.references(document)}
    seen=set();used=set()
    for binding in bindings:
        if not isinstance(binding,dict) or not isinstance(binding.get('pointer'),list) or not binding['pointer'] or any(type(p) not in (str,int) for p in binding['pointer']):
            raise PackageError('Invalid package binding')
        pointer=tuple(binding['pointer']);name=binding['path']
        if pointer in seen or expected.get(pointer)!=name:raise PackageError('Package binding does not match its project')
        seen.add(pointer);safe_file(root,name)
        obj=document
        for part in pointer[:-1]:obj=obj[part]
        actual=media_files(obj) if pointer[-1]=='path' and obj.get('sequence_frames') else [name]
        if binding.get('files')!=actual or any(p not in spelling for p in actual):raise PackageError('Uncollected project resource: '+name)
        used.update(actual)
    if seen!=set(expected):raise PackageError('The project contains uncollected resource references')
    if used!=spelling:raise PackageError('Package inventory contains unbound files')
    for media in document.get('media',{}).values():
        if not media.get('synthetic') and (not isinstance(media.get('path'),str) or not media['path']):raise PackageError('Original media has no source path')
        validate_options(media)
        if any(k in media for k in DERIVED):raise PackageError('Package contains external derived-media references')
        if 'source_relink_basis' in media:raise PackageError('Package contains a machine-local source acceptance')
    for style in resources.font_styles(document):
        bound=resources.font_binding(style)
        if not bound or not isinstance(bound.get('path'),str) or not bound['path']:raise PackageError('Package font is not pinned to a collected file')
    for obj in resources.objects(document):
        if obj.get('input_transform') not in (None,'','none'):
            bound=obj.get('input_transform_resource') or {}
            if not isinstance(bound,dict) or bound.get('name')!=obj['input_transform'] or not isinstance(bound.get('path'),str) or not bound['path']:raise PackageError('Package camera LUT is not pinned')
    normalized=copy.deepcopy(document);_parent_sources(normalized)
    if normalized!=document:raise PackageError('Package subclip sources disagree with their parents')
    return document,manifest,digest(raw)


def import_package(manifest_path, destination, identity, *, check=lambda:None, progress=lambda *args:None, publish=lambda f:f()):
    if not re.fullmatch('[a-f0-9]{32}',identity):raise PackageError('Invalid import identity')
    document,manifest,checksum=verify_package(manifest_path,check=check);source=Path(manifest_path).absolute().parent
    root=Path(destination).absolute();unlinked(root);root.mkdir(parents=True,exist_ok=True)
    pid='import-'+identity;final=root/pid
    if final.exists():raise PackageError('Import destination already exists; preserve it and use a new request')
    stage=Path(tempfile.mkdtemp(prefix='.import-',dir=root));published=False
    try:
        for index,entry in enumerate(manifest['files']):
            check();target=stage/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
            details=_verified_file(safe_file(source,entry['path']),target,check=check)
            if details!={'bytes':entry['bytes'],'sha256':entry['sha256']}:raise PackageError('Package changed during import')
            progress('Importing verified resources',(index+1)/max(1,len(manifest['files'])),f'{index+1} files copied')
        for binding in manifest['bindings']:
            obj=document
            for part in binding['pointer'][:-1]:obj=obj[part]
            obj[binding['pointer'][-1]]=str(final/'content'/binding['path'])
        document['id']=identity[:8]
        raw=packed(document);parse_project(raw)
        # Keep an independently recoverable edit even if final project.json fails.
        write_atomic(stage/'project-snapshot.json',raw)
        write_atomic(stage/'import-receipt.json',packed({'package_manifest_sha256':checksum,'source_manifest':str(manifest_path),
                     'project_snapshot_sha256':digest(raw),'files':manifest['files'],'omissions':manifest.get('omissions',[])}))
        # The validated project bytes are captured; recheck the manifest, bounded.
        if digest(read_json(safe_file(source,'manifest.json'))[1])!=checksum:raise PackageError('Package manifest changed during import')
        check()
        def commit():
            nonlocal published
            final.mkdir()
            try:os.rename(stage,final/'content')
            except BaseException:final.rmdir();raise
            published=True
            try:write_atomic(final/'project.json',raw)
            except Exception as error:raise PackageError(f'Imported resources retained at {final}; project publication failed: {error}') from error
            return {'kind':'package_import','project':pid,'name':document.get('name','Imported project'),'folder':str(final),'files':len(manifest['files']),
                    'manifest_sha256':checksum,'warnings':manifest.get('warnings',[]),'omissions':manifest.get('omissions',[])}
        return publish(commit)
    finally:
        if not published and stage.exists():shutil.rmtree(stage)

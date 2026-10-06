"""Prepare originals independently; explicit, guarded relinking belongs to the caller."""
import copy
import hashlib
import json
import os
from pathlib import Path

from media_collection import MediaCollection, MediaCollectionError, _signature, _verified_file, rebase_source_acceptance
from preflight import media_files
from task_inputs import source_stamp

SOURCE_FIELDS = ('path', 'subclip_of', 'sub_in', 'sub_out', 'sequence_frames', 'input_opts', 'synthetic', 'ingest_token', 'source_relink_basis')
MAX_MANIFEST_BYTES = 32 * 1024 * 1024


def read_manifest(path):
    with open(path, 'rb') as stream: raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise MediaCollectionError('Collection manifest exceeds the 32 MiB limit; collect a smaller media library.')
    return raw


def basis(project):
    return {mid: {k: copy.deepcopy(media[k]) for k in SOURCE_FIELDS if k in media}
            for mid, media in project['media'].items() if not media.get('synthetic')}


def stamps(project, check=lambda: None):
    result = {}
    for media in project['media'].values():
        check()
        if media.get('synthetic') or media.get('subclip_of'): continue
        for path in media_files(media):
            check(); MediaCollection._unlinked(path)
            result[os.path.abspath(path)] = list(_signature(os.stat(path)))
    return result


def payload(project, root, pid, *, check=lambda: None):
    # Timelines/presentation are not inputs to an original-media copy.
    snapshot = {'media': copy.deepcopy(project['media'])}
    if not basis(snapshot): raise MediaCollectionError('There are no original media files to collect')
    return {'project': snapshot, 'basis': basis(snapshot), 'stamps': stamps(snapshot, check),
            'root': os.path.abspath(root), 'destination': os.path.join(root, 'collected', pid)}


def current_inputs(project, captured):
    if basis(project) != captured['basis']:
        raise MediaCollectionError('The media library changed. Start a new collection for its current sources.')


def prepare(captured, task):
    task.progress('Inspecting originals', None)
    if stamps(captured['project'], task.check) != captured['stamps']:
        raise MediaCollectionError('Original files changed before copying. Start a new collection.')
    with MediaCollection(captured['project'], captured['destination'], check=task.check, progress=task.progress) as collection:
        if stamps(captured['project'], task.check) != captured['stamps']:
            raise MediaCollectionError('Original files changed during collection. Start a new collection.')
        manifest = collection.stage / 'manifest.json'
        result = {'dest': str(collection.destination), 'folder': str(collection.final),
                  'manifest': str(collection.final / 'media' / 'manifest.json'),
                  'manifest_sha256': hashlib.sha256(read_manifest(manifest)).hexdigest(),
                  'copied': collection.copied, 'reused': collection.reused, 'verified': len(collection.files),
                  'paths': {mid: m.get('path') for mid, m in collection.project['media'].items() if mid in captured['basis']}}
        # Bound the actual persisted envelope before publishing owned copies.
        from background_tasks import MAX_BYTES, packed
        if len(packed({'payload': captured, 'result': result})) > MAX_BYTES - 8192:
            raise MediaCollectionError('Collection receipt exceeds the 32 MiB task limit; collect a smaller media library.')
        def publish():
            collection.publish()
            return result
        return task.commit_result(publish, ready=True)


def verify(project, captured, result, check=lambda: None):
    """Verify retained bytes before a short project/history commit; originals may be offline."""
    current_inputs(project, captured); check()
    manifest_path = Path(result['manifest'])
    MediaCollection._unlinked(manifest_path)
    raw = read_manifest(manifest_path)
    if hashlib.sha256(raw).hexdigest() != result['manifest_sha256']:
        raise MediaCollectionError('The collection manifest changed. Keep the current sources and inspect the copies.')
    manifest = json.loads(raw)
    for entry in manifest['files']:
        check(); MediaCollection._unlinked(entry['destination'])
        actual = _verified_file(entry['destination'], check=check)
        if actual != {k: entry[k] for k in ('bytes', 'sha256')}:
            raise MediaCollectionError('A collected file changed: ' + entry['destination'])
    for path, expected in captured['stamps'].items():
        check()
        try:
            MediaCollection._unlinked(path)
            actual = list(_signature(os.stat(path)))
        except FileNotFoundError: continue
        if actual != expected:
            raise MediaCollectionError('An original file was replaced after copying. Start a new collection: ' + path)
    after = copy.deepcopy(project)
    from media_preview import digest
    for mid, path in result['paths'].items():
        check(); media = after['media'][mid]; old_path = media.get('path')
        if path == old_path: continue
        # Destination hashes were checked above, and capture includes the exact
        # acceptance. The original may now be offline; use the verified copy.
        rebase_source_acceptance(media, path)
        media['path'] = path
        # Later owned preparation must update this relocation's after-receipt.
        # The complete old source remains available for exact Undo.
        media['workflow_import'] = True
        # Byte-verified relocation keeps existing proxy validation tied to the
        # new file identity. Unvalidated/stale proxies remain unvalidated/stale.
        info = media.get('proxy_info') or {}
        original = captured['project']['media'][mid]
        try:
            old_stamp = [[os.path.abspath(p), captured['stamps'][os.path.abspath(p)][2],
                          captured['stamps'][os.path.abspath(p)][3], captured['stamps'][os.path.abspath(p)][1]]
                         for p in media_files(original)]
            if info.get('source_signature') == digest(old_stamp):
                info['source_signature'] = digest(source_stamp(media))
        except (KeyError, ValueError, TypeError): pass  # Subclip metadata can describe a parent range.
        if media.get('status') == 'ingesting':
            media['status'] = 'ready'
            media['ingest_error'] = 'Originals collected. Prepare media again to finish preview generation.'
        media.pop('task_id', None)
    # All dependent windows read the same physical bytes, including subclips
    # created after the last Relink and therefore lacking a copied acceptance.
    for media in after['media'].values():
        if not media.get('subclip_of'): continue
        check(); parent = media; seen = set()
        while parent.get('subclip_of'):
            key = parent['subclip_of']
            if key in seen or key not in after['media']:
                raise MediaCollectionError('A dependent source has a missing or cyclic parent')
            seen.add(key); parent = after['media'][key]
        if 'source_relink_basis' in parent:
            media['source_relink_basis'] = copy.deepcopy(parent['source_relink_basis'])
        else:
            media.pop('source_relink_basis', None)
    return after

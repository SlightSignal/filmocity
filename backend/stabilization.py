"""Reviewed, source-bound motion analysis with owned subprocesses and publication.

The encoder reads a private byte snapshot. A basename in its private cwd avoids
passing Windows drive letters, Unicode or apostrophes through filter escaping.
Existing unbound .trf files are never overwritten or accepted as a cache.
"""
import copy
import hashlib
import json
import os
import shutil
import subprocesses as subprocess
import time
import uuid

from render_context import RenderContext
from source_relink_io import check_accepted
from task_inputs import portable_source_stamp, source_stamp
from work_budget import work

VERSION = 1
MAX_ANALYSIS = 256 * 1024 * 1024
MAX_LOG = 4 * 1024 * 1024


class StaleAnalysis(ValueError):
    pass


def _check(holder):
    if holder.get('cancelled'): raise RuntimeError('cancelled')


def _hash(path, holder):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            _check(holder)
            data = stream.read(1024 * 1024)
            if not data: break
            digest.update(data)
    return digest.hexdigest()


def _settings(body):
    shakiness, force = body.get('shakiness', 5), body.get('force', False)
    if type(shakiness) is not int or not 1 <= shakiness <= 10:
        raise ValueError('Stabilization shakiness must be an integer from 1 to 10')
    if type(force) is not bool: raise ValueError('Stabilization force must be a boolean')
    return {'shakiness': shakiness, 'force': force}


def _source(project, media_id):
    if not isinstance(media_id, str) or not media_id: raise ValueError('Choose one video source')
    media, seen = project.get('media', {}), set()
    current = media_id
    while True:
        if current in seen: raise ValueError('The source contains a subclip cycle')
        seen.add(current)
        source = media.get(current)
        if not isinstance(source, dict): raise ValueError('The video source no longer exists')
        if not source.get('subclip_of'): break
        current = source['subclip_of']
    if (not source.get('has_video') or not isinstance(source.get('path'), str)
            or source.get('synthetic') or source.get('sequence_frames') or source.get('input_opts')
            or source.get('still') or source.get('is_image') or source.get('type') in ('image', 'still')):
        raise ValueError('Analyze a physical moving video source; generated media, stills and input overrides are unsupported')
    check_accepted(source)
    return current, source


def _descriptor(source, holder):
    stamp = source_stamp(source)
    if len(stamp) != 1: raise ValueError('Analyze one physical video file')
    digest = _hash(source['path'], holder)
    if source_stamp(source) != stamp: raise StaleAnalysis('The source changed during inspection; inspect it again')
    return {'path': os.path.abspath(source['path']), 'stamp': portable_source_stamp(stamp), 'sha256': digest}


def _analyzer(ffmpeg, holder):
    path = shutil.which(ffmpeg)
    if not path: raise ValueError('FFmpeg is unavailable for stabilization analysis')
    return {'sha256': _hash(path, holder)}


def inspect(project, context, body, *, proc_holder=None, ffmpeg='ffmpeg'):
    """Read only; bind the saved owner, physical input bytes and exact settings."""
    holder = proc_holder if proc_holder is not None else {}
    settings = _settings(body)
    media_id, source = _source(project, body.get('media_id'))
    descriptor = _descriptor(source, holder)
    analyzer = _analyzer(ffmpeg, holder)
    affected = []
    media = project.get('media', {})
    for identity in sorted(media):
        current, seen = identity, set()
        while current in media and current not in seen:
            if current == media_id:
                affected.append(identity)
                break
            seen.add(current)
            current = media[current].get('subclip_of')
            if not isinstance(current, str): break
    binding = {'version': VERSION, 'context': context, 'media_id': media_id,
               'requested_media_id': body['media_id'], 'affected_media_ids': affected, 'settings': settings,
               'source': descriptor, 'analyzer': analyzer}
    fingerprint = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
    _check(holder)
    return {'ok': True, 'kind': 'stabilization', **binding, 'fingerprint': fingerprint}


def check_source(review):
    if portable_source_stamp(source_stamp({'path': review['source']['path']})) != review['source']['stamp']:
        raise StaleAnalysis('The reviewed source changed; inspect it again')


def _valid_transform(path, holder):
    if not os.path.isfile(path) or os.path.islink(path) or os.path.getsize(path) > MAX_ANALYSIS:
        raise ValueError('Stabilization output is missing or exceeds the analysis limit')
    with open(path, 'rb') as stream:
        if stream.readline(64).rstrip(b'\r\n') != b'VID.STAB 1':
            raise ValueError('Stabilization output is not the supported ASCII format')
    return _hash(path, holder)


def _cache(root, project, review, holder):
    source = project['media'][review['media_id']]
    record, path = source.get('stab_analysis'), source.get('stab_trf')
    if not isinstance(record, dict) or not isinstance(path, str): return None
    expected = {'version': VERSION, 'owner': {k: review['context'][k] for k in ('workspace', 'project')},
                'media_id': review['media_id'], 'source': review['source'],
                'settings': {'shakiness': review['settings']['shakiness']}, 'analyzer': review['analyzer']}
    if any(record.get(k) != v for k, v in expected.items()): return None
    base = os.path.realpath(os.path.join(root, 'stab'))
    parent = os.path.dirname(path)
    # Only our versioned, unique published directories are eligible. Legacy
    # files and references outside this library remain untouched.
    junction = hasattr(os.path, 'isjunction') and os.path.isjunction(parent)
    if (os.path.dirname(parent) != base or not os.path.basename(parent).startswith('analysis-')
            or os.path.basename(path) != 'transforms.trf' or os.path.realpath(parent) != parent
            or os.path.islink(parent) or junction):
        return None
    try:
        receipt = os.path.join(parent, 'analysis.json')
        if os.path.getsize(receipt) > 64 * 1024: return None
        with open(receipt, encoding='utf-8') as stream:
            sidecar = json.load(stream)
        if sidecar != record or _valid_transform(path, holder) != record.get('sha256'): return None
    except (OSError, ValueError, TypeError): return None
    return {'trf': path, 'record': copy.deepcopy(record), 'cached': True}


class Prepared:
    def __init__(self, root, review, cached=None):
        self.root, self.review, self.stage = root, review, None
        self.result = cached
        self.published = False

    def publish(self):
        if self.result: return self.result
        if not self.stage: raise RuntimeError('Analysis preparation has no owned output')
        target = os.path.join(self.root, 'stab', 'analysis-' + uuid.uuid4().hex)
        os.rename(self.stage, target)  # same filesystem; no shared result is overwritten
        self.stage = None
        self.published = True
        self.result = {'trf': os.path.join(target, 'transforms.trf'), 'record': self.record, 'cached': False}
        return self.result

    def close(self):
        if self.stage:
            # Generated stage only; never delete a published result, even when
            # acknowledgement or the subsequent project commit is uncertain.
            shutil.rmtree(self.stage)
            self.stage = None


def prepare(root, project, review, *, proc_holder=None, ffmpeg='ffmpeg', timeout=3600):
    holder = proc_holder if proc_holder is not None else {}
    check_source(review)
    cached = None if review['settings']['force'] else _cache(root, project, review, holder)
    prepared = Prepared(root, review, cached)
    holder['prepared_analysis'] = prepared
    if cached:
        _check(holder)
        return prepared
    base = os.path.join(root, 'stab')
    os.makedirs(base, exist_ok=True)
    if (os.path.islink(base) or (hasattr(os.path, 'isjunction') and os.path.isjunction(base))
            or os.path.realpath(base) != base):
        raise ValueError('Stabilization storage must be an owned directory inside this library')
    try:
        with RenderContext(proc_holder=holder) as context:
            original = review['source']['path']
            captured = context.new_file(os.path.splitext(original)[1] or '.video')
            digest = hashlib.sha256()
            with open(original, 'rb') as source, open(captured, 'wb') as target:
                while True:
                    context.check_cancelled()
                    chunk = source.read(1024 * 1024)
                    if not chunk: break
                    target.write(chunk); digest.update(chunk)
            if digest.hexdigest() != review['source']['sha256']:
                raise StaleAnalysis('The reviewed source bytes changed; inspect it again')
            check_source(review)
            output, errors = context.new_file('.trf'), context.new_file('.log')
            # The filter sees a generated ASCII basename, never a Windows path.
            result_name = os.path.basename(output)
            command = [ffmpeg, '-hide_banner', '-nostats', '-y', '-i', captured, '-an', '-vf',
                f"vidstabdetect=fileformat=ascii:shakiness={review['settings']['shakiness']}:accuracy=15:result={result_name}",
                '-f', 'null', '-']
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            with work(holder, 'encode', check=context.check_cancelled):
                with open(errors, 'wb') as stderr:
                    context.check_cancelled()
                    child = subprocess.Popen(command, cwd=context.root, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=stderr, **options)
                    holder['proc'] = child
                    deadline = time.monotonic() + timeout
                    try:
                        while child.poll() is None:
                            context.check_cancelled()
                            if time.monotonic() > deadline: raise ValueError('Stabilization analysis timed out')
                            if os.path.getsize(errors) > MAX_LOG or os.path.getsize(output) > MAX_ANALYSIS:
                                raise ValueError('Stabilization output exceeds its limit')
                            time.sleep(.025)
                        context.check_cancelled()
                        if child.returncode:
                            with open(errors, 'rb') as stream:
                                stream.seek(max(0, os.path.getsize(errors)-600))
                                message = stream.read(600).decode('utf-8', 'replace')
                            raise ValueError('FFmpeg stabilization failed: ' + message)
                    finally:
                        if child.poll() is None: child.kill()
                        child.wait()
                        if holder.get('proc') is child: holder.pop('proc', None)
            output_sha = _valid_transform(output, holder)
            if _descriptor({'path': original}, holder) != review['source']:
                raise StaleAnalysis('The source changed during analysis; inspect it again')
            stage = os.path.join(root, 'stab', '.pending-' + uuid.uuid4().hex)
            os.mkdir(stage)
            prepared.stage = stage
            prepared.record = {'version': VERSION,
                'owner': {k: review['context'][k] for k in ('workspace', 'project')},
                'media_id': review['media_id'], 'source': copy.deepcopy(review['source']),
                'settings': {'shakiness': review['settings']['shakiness']},
                'analyzer': review['analyzer'], 'sha256': output_sha}
            shutil.copyfile(output, os.path.join(stage, 'transforms.trf'))
            with open(os.path.join(stage, 'analysis.json'), 'x', encoding='utf-8') as stream:
                json.dump(prepared.record, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
                stream.flush(); os.fsync(stream.fileno())
            context.check_cancelled()
        _check(holder)
        return prepared
    except BaseException:
        prepared.close()
        raise

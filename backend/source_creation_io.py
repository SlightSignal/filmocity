"""Captured file identities and bounded primary-audio inspection for bin commands."""
import copy
import json
import math
import os
import subprocess
import time

from render_context import RenderContext
from render_replace import stamp
from source_relink_io import check_accepted
from timeline_time import interpretation_factor

MAX_OUTPUT = 4 * 1024 * 1024
TIMEOUT = 60
MAX_FILES = 50000


def check(resources):
    """Absence is captured too: a newly mounted file needs a fresh command."""
    for resource in resources:
        try: current = stamp(resource['path'])
        except FileNotFoundError: current = None
        if current != resource['stamp']:
            raise ValueError('Source files or their availability changed; retry from the current saved project')


def _probe(path, context, *, ffprobe='ffprobe'):
    from work_budget import work
    output, errors = context.new_file('.json'), context.new_file('.log')
    with work(context.holder, 'probe', check=context.check_cancelled):
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        with open(output, 'wb') as stdout, open(errors, 'wb') as stderr:
            context.check_cancelled()
            child = subprocess.Popen([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path],
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **options)
            context.holder['proc'] = child
            deadline = time.monotonic() + TIMEOUT
            try:
                while child.poll() is None:
                    context.check_cancelled()
                    if time.monotonic() > deadline: raise ValueError('Audio source inspection timed out')
                    if any(os.path.getsize(p) > MAX_OUTPUT for p in (output, errors)):
                        raise ValueError('Audio source inspection exceeds four MiB')
                    time.sleep(.025)
                context.check_cancelled()
                if any(os.path.getsize(p) > MAX_OUTPUT for p in (output, errors)):
                    raise ValueError('Audio source inspection exceeds four MiB')
                if child.returncode:
                    with open(errors, 'rb') as stream:
                        stream.seek(max(0, os.path.getsize(errors)-400))
                        message = stream.read(400).decode('utf-8', 'replace')
                    raise ValueError('Audio source cannot be inspected: '+message)
            finally:
                if child.poll() is None: child.kill()
                child.wait()
                if context.holder.get('proc') is child: context.holder.pop('proc', None)
    with open(output, 'rb') as stream: raw = stream.read(MAX_OUTPUT+1)
    if len(raw) > MAX_OUTPUT: raise ValueError('Audio source inspection exceeds four MiB')
    document = json.loads(raw)
    if not isinstance(document, dict): raise ValueError('Audio inspection returned invalid metadata')
    return document


def prepare(project, body, mode, *, identity, added, proc_holder=None, scratch_parent=None):
    import source_creation
    from media_metadata import summarize
    from media_preview import digest
    from preflight import media_files
    from source_commands import physical
    from task_inputs import source_stamp
    holder = proc_holder if proc_holder is not None else {}
    with RenderContext(proc_holder=holder, scratch_parent=scratch_parent) as context:
        context.check_cancelled()
        planned = source_creation.plan(project, body, mode, identity=identity, added=added)
        paths = list(dict.fromkeys(os.path.abspath(path) for path in planned['resources']))
        if len(paths) > MAX_FILES: raise ValueError('Source creation exceeds 50000 physical files')
        resources = []
        for path in paths:
            context.check_cancelled()
            try: current = stamp(path)
            except FileNotFoundError: current = None
            resources.append({'path':path, 'stamp':current})
        offline = {value['path'] for value in resources if value['stamp'] is None}
        for mid in planned['resource_media_ids']:
            context.check_cancelled()
            media = project['media'][mid]
            if media.get('synthetic'): continue
            if any(os.path.abspath(path) in offline for path in media_files(media)): continue
            check_accepted(media)
            previous = (media.get('proxy_info') or {}).get('source_signature')
            if previous and previous != digest(source_stamp(media)):
                raise ValueError('Source changed on disk; inspect and Relink it before creating bin items')
        if mode == 'breakout':
            selected, parent = physical(project, body.get('media_id'))
            if offline: raise ValueError('Breakout needs the online original; Relink it before selecting source channels')
            if parent.get('input_opts'): raise ValueError('Breakout cannot inspect custom input options; render a normal file first')
            measured = summarize(_probe(parent['path'], context))
            if not measured.get('has_audio'): raise ValueError('The source has no readable audio stream; Relink it to refresh metadata')
            for field in ('channels','sample_rate'):
                stored, actual = parent.get(field), measured.get(field)
                if type(stored) is not int or stored <= 0 or stored != actual:
                    raise ValueError('Primary audio '+field+' differs from saved metadata; inspect and Relink the source first')
            end = (selected.get('sub_in',0)+selected['duration'])/interpretation_factor(selected)
            duration = measured.get('duration')
            if (isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration)
                    or duration <= 0 or end > duration+max(1e-9,math.ulp(duration)*8)):
                raise ValueError('Selected source window exceeds the inspected container duration; Relink the source first')
            planned['inspection'] = {'channels':measured['channels'],'sample_rate':measured['sample_rate'],
                'native_duration':duration,'stream':'first audio stream'}
        planned = copy.deepcopy(planned)
        if offline:
            planned['summary'].setdefault('warnings',[]).append('Some original files are offline. Bin metadata is retained; preview preparation waits until the original is available or relinked.')
            available = []
            for media in planned['media']:
                if media['id'] not in planned['prepare_ids']: continue
                if not any(os.path.abspath(path) in offline for path in media_files(media)):
                    available.append(media['id'])
            planned['prepare_ids'] = available
        check(resources); context.check_cancelled()
        return {'plan':planned,'resources':resources}

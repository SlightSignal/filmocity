"""Checked, cancellable media derivatives, published only for their captured source."""
import os
import math
from pathlib import Path

import proxy_media
from cache_paths import linked
from render import _run_ffmpeg
from render_context import RenderContext
from task_inputs import source_stamp
from input_options import validated_input_options

WEB_AUDIO_EXT = {'.mp3', '.m4a', '.aac', '.wav', '.ogg', '.oga', '.opus', '.flac', '.webm'}


def prepare(root, payload, task, current, update, *, ffmpeg='ffmpeg', ffprobe='ffprobe', proxy_encoder='libx264'):
    from media_cache import preparing
    with preparing(root, task.check):
        return _prepare(root, payload, task, current, update, ffmpeg=ffmpeg, ffprobe=ffprobe, proxy_encoder=proxy_encoder)


def _prepare(root, payload, task, current, update, *, ffmpeg, ffprobe, proxy_encoder):
    info, path = payload['info'], payload['path']
    source_options = validated_input_options(info.get('input_opts'))
    def validate():
        task.check()
        if not current(payload) or source_stamp({**info, 'path': path}) != payload['stamp']:
            raise ValueError('Media changed or was removed. Import/relink the current source before preparing it again.')
    def publish(files, fields):
        def commit():
            validate()
            published = []
            try:
                for staged, destination in files:
                    if os.path.lexists(destination): raise ValueError('Derivative destination already exists')
                    os.replace(staged, destination); published.append(destination)
            except Exception:
                for destination in published: os.remove(destination)
                raise
            # A save exception can mean the commit reached disk. Keep those files
            # for recovery; only an explicit rejection proves they are unattached.
            if not update(payload, fields):
                for destination in published: os.remove(destination)
                raise ValueError('Media changed before prepared files could be attached')
        task.publish(commit)
    for folder in ('thumbs', 'proxies'):
        directory = Path(root) / folder
        if linked(directory): raise ValueError('Linked derivative directories are not supported')
        directory.mkdir(exist_ok=True)
    validate()
    with RenderContext(proc_holder=task.holder, scratch_parent=str(Path(root) / 'thumbs')) as owned:
        artifacts, fields = [], {'status': 'ready', 'ingest_error': None, 'task_id': task.id}
        def encode(stage, inputs, filters, suffix, folder='thumbs', progress=False):
            validate(); task.progress(stage, None)
            staged = owned.new_file(suffix)
            command = [ffmpeg, '-hide_banner', '-y', *inputs, *filters, '-threads', '1', staged]
            _run_ffmpeg(command, context=owned, total=info.get('audio_alias_native_duration', info.get('duration', 1)),
                progress=(lambda fraction: task.progress(stage, fraction)) if progress else None, timeout=180)
            if not os.path.isfile(staged) or os.path.getsize(staged) == 0: raise ValueError(stage + ' produced no output')
            name = task.id + '-' + str(len(artifacts)) + suffix
            destination = str(Path(root) / folder / name); artifacts.append((staged, destination))
            return '/' + folder + '/' + name
        derivatives_ready = False
        try:
            inputs = source_options + ['-i', path]
            alias_graph = None
            if info.get('audio_alias'):
                from source_timing import audio_window
                from audio_source_channels import input_chain, channel_index
                duration = info.get('audio_alias_native_duration')
                if isinstance(duration, bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or not 0 < duration <= 4*3600:
                    raise ValueError('Audio alias preparation requires a bounded native source duration')
                # Full native clock, including delayed/absent audio, is retained.
                # Source monitor interpretation/subclip offsets remain external.
                inputs = ['-copyts','-start_at_zero',*inputs]
                alias_head = audio_window(0,0,duration)
                if channel_index(info) is not None: alias_head[0] = alias_head[0].replace('[0:a]', '[0:a:0]', 1)
                alias_graph = ','.join(alias_head+input_chain(info))
                if not update(payload, {'status':'ingesting','proxy_status':'preparing','proxy_error':None,'task_id':task.id}): raise ValueError('Audio alias changed before preparation started')
            if info.get('has_video'):
                seek = [] if info.get('is_image') else ['-ss', str(min(1, info['duration']/2))]
                fields['thumb'] = encode('Thumbnail', [*source_options, *seek, '-i', path], ['-map','0:V:0','-frames:v','1','-update','1','-vf','scale=320:-2'], '.jpg')
                if info.get('is_image'): fields['strip'] = fields['thumb']
                else: fields['strip'] = encode('Filmstrip', inputs, ['-map','0:V:0','-vf', f"fps=10/{max(info['duration'],.1)},scale=160:-2,tile=10x1", '-frames:v','1','-update','1'], '.jpg')
            if info.get('has_audio'):
                waveform = (alias_graph+',' if alias_graph else '[0:a:0]')+'showwavespic=s=1600x80:colors=0x8fb6ff'
                fields['wave'] = encode('Waveform', inputs, ['-filter_complex',waveform,'-frames:v','1'], '.png')
            publish(artifacts, fields); artifacts.clear(); derivatives_ready = True
            if info.get('has_video') and not info.get('is_image') and info.get('duration', 0) > 0:
                policy = proxy_media.settings(payload.get('proxy_settings'))
                validate()
                update(payload, {'proxy_status': 'preparing', 'proxy_error': None, 'task_id': task.id})
                task.progress('Checking source timing', None)
                source = proxy_media.inspect_file(path, owned, ffprobe=ffprobe, input_opts=source_options)
                # Probe the original again: old projects may have incomplete metadata.
                measured = proxy_media.summarize(source)
                output_options = proxy_media.options(measured, policy, encoder=proxy_encoder)
                source_frames = proxy_media.inspect_file(path, owned, ffprobe=ffprobe, input_opts=source_options, frames=True)
                proxy = encode('Video proxy', ['-copyts', '-start_at_zero', *inputs], output_options, '.mp4', 'proxies', True)
                task.progress('Verifying proxy timing', None)
                staged = artifacts[-1][0]
                output = proxy_media.inspect_file(staged, owned, ffprobe=ffprobe)
                proxy_frames = proxy_media.inspect_file(staged, owned, ffprobe=ffprobe, frames=True)
                validation = proxy_media.check_proxy(source, output, source_frames, proxy_frames, measured, policy, check=task.check)
                validation['encoder'] = proxy_encoder
                from media_preview import file_stamp, digest
                validation['source_signature'] = digest(payload['stamp'])
                validation['file_stamp'] = file_stamp(staged)
            elif alias_graph:
                proxy = encode('Audio alias preview', inputs, ['-filter_complex',alias_graph+'[alias]', '-map','[alias]',
                    '-vn','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart'], '.m4a', 'proxies', True)
                staged = artifacts[-1][0]
                inspected = proxy_media.inspect_file(staged, owned, ffprobe=ffprobe)
                stream = next((s for s in inspected.get('streams',[]) if s.get('codec_type')=='audio'), {})
                start = float(stream.get('start_time',math.nan)); actual_duration = float(stream.get('duration',math.nan))
                if stream.get('sample_rate') != '48000' or stream.get('channels') != 2 or not math.isfinite(start) or abs(start)>1/48000 or not math.isfinite(actual_duration) or abs(actual_duration-duration)>1024/48000+.001:
                    raise ValueError('Audio alias preview does not preserve its full native time window')
                from media_preview import file_stamp, digest
                validation = {'codec':'aac','lossy':True,'sample_rate':48000,'channels':2,'native_duration':duration,
                    'measured_duration':actual_duration,'validation':'audio_alias_common_clock_metadata',
                    'source_signature':digest(payload['stamp']),'file_stamp':file_stamp(staged),'audio_alias_basis':info['audio_alias_basis'],
                    'audio_alias_source_generation':info['audio_alias_source_generation']}
                if channel_index(info) is not None: validation['channel_index'] = channel_index(info)
            elif info.get('has_audio') and not info.get('has_video') and Path(path).suffix.lower() not in WEB_AUDIO_EXT:
                proxy = encode('Audio proxy', inputs, ['-vn','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart'], '.m4a', 'proxies', True)
            else: return {'media_id': payload['media_id'], 'prepared': True}
            publish(artifacts, {'proxy': proxy, 'proxy_status': 'ready', 'proxy_error': None,
                'proxy_info': validation if info.get('has_video') or alias_graph else {'codec': 'aac', 'validation': 'encoder_completed'}})
            return {'media_id': payload['media_id'], 'prepared': True, 'proxy': proxy}
        except Exception as error:
            # Completed earlier derivatives remain usable. No partial file is linked.
            failed = 'cancelled' if task.holder.get('cancelled') else 'error'
            update(payload, {'ingest_error': None if derivatives_ready else str(error)[:500],
                'status': 'ready' if derivatives_ready else failed, 'task_id': task.id,
                'proxy_status': failed, 'proxy_error': str(error)[:500]})
            raise

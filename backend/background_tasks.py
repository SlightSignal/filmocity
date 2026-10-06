"""Bounded, persistent analysis/media tasks. Results never apply themselves to edits.

Workers run captured inputs. Shutdown/cancellation is cooperative, including
owned subprocesses. Restart preserves results and interrupts unfinished work.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

from project_sync import workspace_id
from project_transaction import write_atomic

ACTIVE = {'queued', 'running', 'cancelling', 'publishing', 'applying'}
STATES = ACTIVE | {'ready', 'done', 'applied', 'cancelled', 'error', 'interrupted'}
MAX_BYTES = 32 * 1024 * 1024


class TaskError(ValueError): pass
class MediaTaskBusy(TaskError):
    pass


class TaskCancelled(RuntimeError): pass


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


class TaskStore:
    def __init__(self, root):
        self.folder = Path(root) / 'tasks'; self.workspace = workspace_id(root)
        if self.folder.is_symlink() or getattr(self.folder, 'is_junction', lambda: False)(): raise TaskError('Linked task folders are not supported')
        self.folder.mkdir(exist_ok=True)

    def path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity): raise TaskError('Invalid task identity')
        return self.folder / (identity + '.json')

    def read(self, path):
        if path.is_symlink(): raise TaskError('Linked task receipts are not supported')
        with path.open('rb') as stream: raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES: raise TaskError('Task receipt exceeds size limit')
        envelope = json.loads(raw); value = envelope['value']
        if type(envelope.get('version')) is not int or envelope['version'] != 1 or envelope.get('sha256') != hashlib.sha256(packed(value)).hexdigest(): raise TaskError('Task receipt checksum mismatch')
        record = value['record']; self.path(record['id'])
        if record['id'] != path.name.split('.')[0] or record['status'] not in STATES or record['context']['workspace'] != self.workspace:
            raise TaskError('Task receipt identity does not match this workspace')
        if record['kind'] not in ('media', 'transcribe', 'package', 'package_import', 'collect', 'analysis', 'render_replace', 'sync', 'audio_analysis', 'recipe', 'cover') or not isinstance(value['payload'], dict): raise TaskError('Invalid task input')
        if not isinstance(record['context'].get('project'), str) or not math.isfinite(record['created']): raise TaskError('Invalid task owner/time')
        return value

    def save(self, value):
        path = self.path(value['record']['id'])
        raw = packed({'version': 1, 'value': value, 'sha256': hashlib.sha256(packed(value)).hexdigest()})
        if len(raw) > MAX_BYTES: raise TaskError('Task receipt exceeds size limit')
        if path.is_symlink(): raise TaskError('Linked task receipts are not overwritten')
        if path.exists():
            # Never replace a corrupt primary/backup with another corrupt copy.
            self.read(path)
            backup = path.with_suffix('.previous.json')
            if backup.is_symlink(): raise TaskError('Linked task backups are not overwritten')
            write_atomic(backup, path.read_bytes())
        write_atomic(path, raw)

    def restore(self):
        restored, unavailable = {}, []
        identities = {p.name.split('.')[0] for p in self.folder.glob('*.json')}
        for identity in sorted(identities):
            errors = []
            for suffix in ('.json', '.previous.json'):
                try:
                    path = self.path(identity).with_name(identity + suffix)
                    value = self.read(path); record = value['record']
                    if suffix != '.json': record['warning'] = 'Recovered previous task receipt; damaged primary preserved.'
                    if record['status'] == 'applying':
                        record.update(status='ready', message='Apply was interrupted. Check the project before applying again.')
                    elif record['status'] in ACTIVE:
                        record.update(status='interrupted', message='Interrupted by shutdown. Retry explicitly; no work was restarted.')
                    restored[identity] = value; break
                except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error: errors.append(str(error))
            else: unavailable.append({'id': identity, 'error': '; '.join(errors)[:600]})
        return restored, unavailable


class TaskContext:
    def __init__(self, manager, identity):
        self.manager, self.id = manager, identity; self.holder = {}

    def check(self):
        if self.holder.get('cancelled'): raise TaskCancelled('Cancelled')

    def progress(self, stage, fraction=None, message=''):
        self.check()
        if fraction is not None and (not math.isfinite(fraction) or not 0 <= fraction <= 1): raise TaskError('Invalid task progress')
        with self.manager.condition:
            self.check()
            record = self.manager.values[self.id]['record']
            record.update(stage=stage, progress=fraction, message=message)

    def publish(self, function):
        """Cancellation cannot cross the short final derived-media publication."""
        with self.manager.condition:
            self.check(); self.manager.values[self.id]['record']['status'] = 'publishing'
        try: return function()
        finally:
            with self.manager.condition:
                record = self.manager.values[self.id]['record']
                if record['status'] == 'publishing': record['status'] = 'running'
                if self.manager.stopping: self.holder['cancelled'] = True

    def commit_result(self, function, *, ready=False):
        """Publish a final folder/result without a later cancellation undoing its receipt."""
        with self.manager.condition:
            self.check(); self.manager.values[self.id]['record']['status'] = 'publishing'
        try: result = function()
        except BaseException:
            with self.manager.condition: self.manager.values[self.id]['record']['status'] = 'running'
            raise
        with self.manager.condition:
            value = self.manager.values[self.id]
            value['result'] = result
            value['record'].update(status='ready' if ready else 'done', stage='Ready to apply' if ready else 'Complete', progress=1, message='')
            self.holder['result_published'] = True
        return result


class TaskManager:
    def __init__(self, root, handlers, *, workers=2, capacity=256, start=True):
        self.store = TaskStore(root); self.handlers = handlers
        self.condition = threading.Condition(threading.RLock())
        self.values, self.unavailable = self.store.restore()
        self.capacity = capacity; self.stopping = False; self.contexts = {}; self.threads = []
        if not 1 <= workers <= 4: raise TaskError('Use 1–4 background workers')
        if start:
            try:
                for i in range(workers):
                    thread = threading.Thread(target=self._worker, name=f'Filmocity task {i+1}', daemon=True)
                    thread.start(); self.threads.append(thread)
            except BaseException:
                self.shutdown()
                raise

    def _persist(self, value, *, required=False):
        try: self.store.save(value)
        except Exception as error:
            if required: raise
            value['record']['warning'] = 'Task history could not be saved: ' + str(error)[:300]

    def submit(self, kind, name, context, payload, *, identity=None, retry_of=None):
        if kind not in self.handlers: raise TaskError('Unsupported background task')
        if context.get('workspace') != self.store.workspace: raise TaskError('Task belongs to another workspace')
        identity = uuid.uuid4().hex if identity is None else identity; self.store.path(identity)
        payload = copy.deepcopy(payload); context = copy.deepcopy(context)
        # Presentation/derived metadata changes do not launch duplicate speech work.
        inputs = {k: payload[k] for k in ('sequence', 'model', 'signature')} if kind == 'transcribe' and 'signature' in payload else payload
        if kind == 'cover': inputs = {k: payload[k] for k in ('version', 'sequence', 'signature')}
        if kind == 'recipe': inputs = {k: payload[k] for k in ('version', 'mode', 'sequence', 'signature')}
        if kind == 'sync': inputs = {k: payload[k] for k in ('version', 'mode', 'sequence', 'signature')}
        if kind == 'audio_analysis': inputs = {k: payload[k] for k in ('version', 'mode', 'scope', 'sequence', 'signature')}
        if kind == 'collect': inputs = {k: payload[k] for k in ('basis', 'stamps', 'destination', 'root')}
        key = hashlib.sha256(packed([kind, context['workspace'], context['project'], inputs])).hexdigest()
        with self.condition:
            if self.stopping: raise TaskError('Background tasks are shutting down')
            existing = self.values.get(identity)
            if existing:
                if existing['record']['input_key'] != key: raise TaskError('Task request identity was reused with different inputs')
                return self.public(existing)
            for value in self.values.values():
                r = value['record']
                if r['input_key'] == key and r['status'] in ACTIVE | {'ready'}: return self.public(value)
                if (kind == r['kind'] == 'media' and payload.get('media_id') and r['status'] in ACTIVE
                        and r['context']['project'] == context['project']
                        and value['payload'].get('media_id') == payload['media_id']
                        and value['payload'].get('token') == payload.get('token')):
                    raise MediaTaskBusy('This source is already being prepared. Wait, or cancel it in Tasks before changing proxy settings.')
            if sum(v['record']['status'] in ACTIVE for v in self.values.values()) >= self.capacity: raise TaskError('Background queue is full. Wait or cancel tasks, then retry.')
            value = {'record': {'id': identity, 'kind': kind, 'name': str(name)[:240], 'context': context,
                'status': 'queued', 'created': time.time(), 'stage': 'Queued', 'progress': None, 'message': '', 'input_key': key,
                'sequence': payload.get('sequence'), 'media_id': payload.get('media_id'), 'retry_of': retry_of}, 'payload': payload, 'result': None}
            self._persist(value, required=True)
            self.values[identity] = value; self.condition.notify_all()
            return self.public(value)

    def public(self, value):
        from work_budget import status
        result = copy.deepcopy(value['record'])
        if result['kind'] in ('package', 'package_import') and result['status'] == 'done':
            result['result'] = copy.deepcopy(value['result'])
        if result['kind'] == 'collect' and value['result'] is not None:
            result['result'] = {k: value['result'][k] for k in ('folder', 'manifest', 'manifest_sha256', 'copied', 'reused', 'verified')}
        context = self.contexts.get(result['id'])
        if context:
            state = status(context.holder)
            if state: result['resource'] = state
        return result

    def get(self, identity):
        with self.condition:
            if identity not in self.values: raise TaskError('Task not found')
            return copy.deepcopy(self.values[identity])

    def catalog(self, project=None):
        with self.condition:
            values = [v for v in self.values.values() if project is None or v['record']['context']['project'] == project]
            values.sort(key=lambda v: v['record']['created'], reverse=True)
            active = [v for v in values if v['record']['status'] in ACTIVE | {'ready'}]
            recent = [v for v in values if v['record']['status'] not in ACTIVE | {'ready'}][:50]
            return {'tasks': [self.public(v) for v in active + recent], 'unavailable': copy.deepcopy(self.unavailable),
                    'workers': len(self.threads), 'capacity': self.capacity, 'total': len(values)}

    def cancel(self, identity):
        with self.condition:
            value = self.values.get(identity)
            if not value: raise TaskError('Task not found')
            record = value['record']
            if record['status'] in ('publishing', 'applying'): raise TaskError('The result is being saved; wait for that step to finish')
            if record['status'] in ('queued', 'ready'):
                record.update(status='cancelled', message='Cancelled', finished=time.time()); self._persist(value)
            elif record['status'] in ('running', 'cancelling'):
                self.contexts[identity].holder['cancelled'] = True
                record.update(status='cancelling', message='Cancelling; waiting for the current operation to stop.'); self._persist(value)
            self.condition.notify_all(); return self.public(value)

    def begin_apply(self, identity):
        with self.condition:
            value = self.values.get(identity)
            if not value or value['record']['status'] != 'ready': raise TaskError('This result is not waiting to be applied')
            original = value['record']['status']; value['record']['status'] = 'applying'
            try: self._persist(value, required=True)
            except Exception: value['record']['status'] = original; raise
            return copy.deepcopy(value)

    def finish_apply(self, identity, *, success, message=''):
        with self.condition:
            value = self.values[identity]
            value['record'].update(status='applied' if success else 'ready', message=message, finished=time.time())
            self._persist(value); self.condition.notify_all(); return self.public(value)

    def _worker(self):
        while True:
            with self.condition:
                while True:
                    if self.stopping: return
                    speech_busy = any(self.values[i]['record']['kind'] == 'transcribe' for i in self.contexts)
                    value = next((v for v in self.values.values() if v['record']['status'] == 'queued' and not (speech_busy and v['record']['kind'] == 'transcribe')), None)
                    if value: break
                    self.condition.wait()
                record = value['record']; identity = record['id']; context = TaskContext(self, identity)
                self.contexts[identity] = context; record.update(status='running', started=time.time()); self._persist(value)
            try:
                result = self.handlers[record['kind']](copy.deepcopy(value['payload']), context)
                with self.condition:
                    if not context.holder.get('result_published'):
                        context.check()
                        value['result'] = result
                        ready = record['kind'] in ('transcribe', 'collect', 'analysis', 'render_replace', 'sync', 'audio_analysis', 'recipe', 'cover')
                        record.update(status='ready' if ready else 'done', progress=1,
                            stage='Ready to apply' if ready else 'Complete', message='')
                    # commit_result already owns result/status. In particular,
                    # do not revive a ready result cancelled before this worker
                    # returns from its handler's final cleanup.
            except Exception as error:
                with self.condition:
                    record.update(status='cancelled' if context.holder.get('cancelled') else 'error', message=str(error)[:1500])
            finally:
                with self.condition:
                    record['finished'] = time.time(); self._persist(value)
                    self.contexts.pop(identity, None); self.condition.notify_all()

    def shutdown(self, timeout=10):
        with self.condition:
            self.stopping = True
            for identity, context in self.contexts.items():
                if self.values[identity]['record']['status'] != 'publishing': context.holder['cancelled'] = True
            for value in self.values.values():
                if value['record']['status'] == 'queued': value['record'].update(status='interrupted', message='Stopped before starting. Retry explicitly.'); self._persist(value)
            self.condition.notify_all()
        deadline = time.monotonic() + timeout
        for thread in self.threads: thread.join(max(0, deadline - time.monotonic()))
        return all(not t.is_alive() for t in self.threads)

"""Bounded, expiring proposal snapshots. Render/media callers only read snapshots."""
import copy
import hashlib
import json
import threading
import time
import uuid


def plan_digest(project):
    # Decision metadata and save timestamps do not change the proposed edit.
    content = {key: value for key, value in project.items() if key not in ('updated', 'proposals')}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class PreviewUnavailable(ValueError):
    pass


class PreviewStore:
    def __init__(self, limit=8, lifetime=600, clock=time.monotonic, wall_clock=time.time):
        self.limit, self.lifetime, self.clock, self.wall_clock = limit, lifetime, clock, wall_clock
        self.views = {}; self.lock = threading.RLock()

    def _retire(self, key):
        view = self.views.pop(key, None)
        if view:
            for holder in view['_holders'].values(): holder['cancelled'] = True
        return view is not None

    def _expire(self):
        for key in list(self.views):
            if self.views[key]['_deadline'] <= self.clock(): self._retire(key)

    def create(self, proposal, items, project, context, warnings, *, details=None):
        with self.lock:
            self._expire()
            if len(self.views) >= self.limit:
                raise PreviewUnavailable('Too many proposal previews are open. Stop an existing preview and try again.')
            view = {'id': uuid.uuid4().hex, 'proposal': proposal, 'items': list(items), 'project': copy.deepcopy(project),
                    'context': dict(context), 'plan': plan_digest(project), 'warnings': list(warnings),
                    'expires_at': self.wall_clock() + self.lifetime,
                    '_deadline': self.clock() + self.lifetime, '_holders': {}, '_details': copy.deepcopy(details or {})}
            self.views[view['id']] = view
            return copy.deepcopy({key: value for key, value in view.items() if not key.startswith('_')})

    def get(self, key, context):
        with self.lock:
            self._expire()
            view = self.views.get(key)
            if not view: raise PreviewUnavailable('This proposal preview expired or was stopped. Preview the edits again.')
            if view['context'] != context:
                self._retire(key)
                raise PreviewUnavailable('The saved project changed. Preview the edits again before accepting.')
            return view

    def attach(self, key, context, holder):
        with self.lock:
            view = self.get(key, context); view['_holders'][id(holder)] = holder
            return view

    def detach(self, key, holder):
        with self.lock:
            if key in self.views: self.views[key]['_holders'].pop(id(holder), None)

    def release(self, key):
        with self.lock: return self._retire(key)

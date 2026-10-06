"""Process-local admission for heavy processing phases, not a CPU/RAM quota.

Acquire at a leaf encoder/probe/model phase, never around graph construction or
an entire job: nested renders must be able to finish before their parent starts.
Waiters are FIFO among eligible operations. Speech has a separate one-model cap.
Running phases are not preempted. Cancellation is checked while waiting; owners
keep the lease until their child/readers or lazy model iterator have finished.
"""
from contextlib import contextmanager
import threading
import time


class WorkBusy(RuntimeError): pass


class WorkBudget:
    def __init__(self, capacity=2, pending_limit=64):
        if type(capacity) is not int or not 1 <= capacity <= 8: raise ValueError('Use 1–8 processing slots')
        if type(pending_limit) is not int or pending_limit < 1: raise ValueError('Processing wait limit must be positive')
        self.capacity, self.pending_limit = capacity, pending_limit
        self.condition = threading.Condition(threading.RLock())
        self.pending, self.active = [], []
        self.owners = set()
        self.released = self.peak_active = 0

    def _eligible(self, request):
        return request['operation'] != 'speech' or not any(r['operation'] == 'speech' for r in self.active)

    def snapshot(self):
        with self.condition:
            return {'capacity': self.capacity, 'pending_limit': self.pending_limit, 'speech_limit': 1,
                    'active': len(self.active), 'waiting': len(self.pending), 'peak_active': self.peak_active,
                    'released_phases': self.released,
                    'active_operations': [r['operation'] for r in self.active],
                    'waiting_operations': [r['operation'] for r in self.pending]}

    @contextmanager
    def work(self, holder=None, operation='encode', *, check=None):
        if operation not in ('encode', 'probe', 'speech'): raise ValueError('Unknown processing operation')
        holder = holder if holder is not None else {}
        timeout = holder.get('resource_wait_timeout')
        if timeout is not None:
            import math
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
                raise ValueError('Processing wait timeout must be finite and nonnegative')
        def cancelled():
            if holder.get('cancelled'): raise RuntimeError('cancelled')
            if check: check()
        thread = threading.get_ident()
        request = {'operation': operation, 'since': time.monotonic(), 'state': 'waiting', 'ticket': object()}
        acquired = False
        with self.condition:
            cancelled()
            if thread in self.owners: raise RuntimeError('Processing slots belong to leaf operations; nested admission would deadlock')
            if len(self.pending) >= self.pending_limit: raise WorkBusy('Processing queue is full. Wait or cancel work, then retry.')
            self.pending.append(request); holder['resource_state'] = request
            try:
                while True:
                    cancelled()
                    first = next((r for r in self.pending if self._eligible(r)), None)
                    if len(self.active) < self.capacity and first is request:
                        self.pending.remove(request); self.active.append(request); self.owners.add(thread)
                        acquired = True; self.peak_active = max(self.peak_active, len(self.active))
                        request['wait_seconds'] = time.monotonic() - request['since']; request['state'] = 'running'
                        self.condition.notify_all(); break
                    elapsed = time.monotonic() - request['since']
                    if timeout is not None and elapsed >= timeout:
                        raise WorkBusy('Processing slots are busy. Wait or cancel background work, then retry.')
                    self.condition.wait(min(.05, max(0, timeout-elapsed)) if timeout is not None else .05)
            finally:
                if not acquired:
                    if request in self.pending: self.pending.remove(request)
                    if holder.get('resource_state') is request: holder.pop('resource_state', None)
                    self.condition.notify_all()
        try:
            cancelled()
            yield
        finally:
            with self.condition:
                self.active.remove(request); self.owners.remove(thread); self.released += 1
                holder['resource_wait_seconds'] = holder.get('resource_wait_seconds', 0) + request['wait_seconds']
                if holder.get('resource_state') is request: holder.pop('resource_state', None)
                self.condition.notify_all()


def status(holder):
    """Read-only presentation data, excluding native process/model handles."""
    request = (holder or {}).get('resource_state')
    if not request: return None
    return {'state': request['state'], 'operation': request['operation'],
            'wait_seconds': round(request.get('wait_seconds', time.monotonic()-request['since']), 3)}


BUDGET = WorkBudget()


def work(holder=None, operation='encode', *, check=None):
    return BUDGET.work(holder, operation, check=check)

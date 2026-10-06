"""Owned generated inputs, not captured source bytes or crash recovery.

An operation borrows a context until its encoders and pipe readers have exited.
Command inspection can retain an owned command and explicitly close it after use.
No scratch directory is allocated at import, and each allocation is exclusive.
"""
from contextlib import contextmanager
from functools import wraps
import logging
import os
import hashlib
import shutil
import tempfile
import threading
import time
import uuid


class RenderCleanupError(RuntimeError):
    pass


class RenderContext:
    def __init__(self, *, proc_holder=None, progress=None, scratch_parent=None, stall_timeout=900):
        self.holder = proc_holder if proc_holder is not None else {}
        self.progress = progress
        self.stall_timeout = stall_timeout
        self.scratch_parent = scratch_parent
        self.root = None
        self.nested_completed = {}
        self.nested_lock = threading.RLock()
        self._condition = threading.Condition(threading.RLock())
        self._users = 0
        self._depths = {}
        self._closing = self._closed = False
        self._cleanup_failed = False
        self._nested_progress = 0.0
        self._owner_scopes = threading.local()
        self.command_cwd = None
        self._font_files = {}

    def check_cancelled(self):
        if self.holder.get("cancelled"):
            raise RuntimeError("cancelled")
        if self._closed:
            raise RuntimeError("render context is closed")

    @contextmanager
    def use(self):
        with self._condition:
            thread = threading.get_ident()
            if self._closed or (self._closing and not self._depths.get(thread)):
                raise RuntimeError("render context is retiring or closed")
            self.check_cancelled()
            self._users += 1
            self._depths[thread] = self._depths.get(thread, 0) + 1
        try:
            yield self
        finally:
            with self._condition:
                self._users -= 1
                self._depths[thread] -= 1
                if not self._depths[thread]: del self._depths[thread]
                self._condition.notify_all()

    def new_file(self, suffix):
        """Reserve a unique pathname; writers close it before returning it to readers."""
        with self._condition:
            self.check_cancelled()
            if self._closing and not self._users:
                raise RuntimeError("render context is retiring")
            if self.root is None:
                parent = os.path.abspath(self.scratch_parent or tempfile.gettempdir())
                # Inherit the parent's Windows ACL, rather than mkdtemp's mode 0700.
                root = os.path.join(parent, "filmocity-render-" + uuid.uuid4().hex[:16])
                os.mkdir(root)
                self.root = root
            while True:
                path = os.path.join(self.root, uuid.uuid4().hex[:16] + suffix)
                try: descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError: continue
                os.close(descriptor)
                return path

    def write_text(self, text, suffix=".txt"):
        path = self.new_file(suffix)
        # FFmpeg treats CR and LF as separate drawtext line separators. Generated
        # inputs must preserve their authored bytes on Windows as well as POSIX.
        with open(path, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        self.check_cancelled()
        return path

    def font_file(self, path):
        """Capture fonts that Windows FreeType cannot open by absolute UTF-8 path.

        FFmpeg can otherwise succeed while silently selecting a different font.
        A generated ASCII basename is opened relative to the child's Unicode
        working directory; the font snapshot has this operation's lifetime.
        """
        source = os.path.abspath(os.fspath(path))
        if os.name != 'nt' or (source.isascii() and len(source) < 240):
            return source
        with self.use(), self._condition:
            self.check_cancelled()
            with open(source, 'rb') as reader:
                before = os.fstat(reader.fileno())
                key = (source, before.st_size, before.st_mtime_ns)
                if key in self._font_files:
                    return self._font_files[key]
                if before.st_size > 64 * 1024 * 1024:
                    raise ValueError('Selected font exceeds the 64 MiB render limit')
                captured = self.new_file('.ttf')
                digest = hashlib.sha256()
                copied = 0
                with open(captured, 'wb') as writer:
                    while True:
                        self.check_cancelled()
                        chunk = reader.read(1024 * 1024)
                        if not chunk: break
                        copied += len(chunk)
                        if copied > 64 * 1024 * 1024:
                            raise ValueError('Selected font exceeds the 64 MiB render limit')
                        writer.write(chunk); digest.update(chunk)
                after = os.fstat(reader.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Selected font changed during capture; render again')
            # Read back our private file before trusting it as a font input.
            with open(captured, 'rb') as reader:
                if hashlib.file_digest(reader, 'sha256').hexdigest() != digest.hexdigest():
                    raise ValueError('Selected font copy could not be verified')
            self.command_cwd = self.root
            result = os.path.basename(captured)
            self._font_files[key] = result
            return result

    def nested_progress(self, fraction):
        self.holder["last"] = time.time()
        self._nested_progress = max(self._nested_progress, 0.1 * fraction)
        if self.progress:
            self.progress(self._nested_progress)

    def close(self):
        """Wait for borrowers; cleanup denial is retryable and remains diagnostic."""
        with self._condition:
            if self._closed:
                return
            if self._depths.get(threading.get_ident()):
                raise RuntimeError("cannot retire render context from inside its consumer")
            self._closing = True
            while self._users:
                self._condition.wait()
            try:
                if self.root is not None and os.path.lexists(self.root):
                    if os.path.islink(self.root) or getattr(os.path, "isjunction", lambda _: False)(self.root):
                        raise OSError("owned scratch was replaced by a link/junction")
                    shutil.rmtree(self.root)
            except OSError as error:
                self._cleanup_failed = True
                message = f"Render scratch cleanup failed; retained at {self.root}: {error}"
                diagnostics = self.holder.get("scratch_diagnostics", [])
                self.holder["scratch_diagnostics"] = diagnostics[-7:] + [{"phase": "scratch_retirement", "path": self.root, "message": message[:500]}]
                raise RenderCleanupError(message) from error
            self.nested_completed.clear()
            self._cleanup_failed = False
            self._closed = True

    def __enter__(self):
        lease = self.use()
        lease.__enter__()
        scopes = getattr(self._owner_scopes, "scopes", None)
        if scopes is None: scopes = self._owner_scopes.scopes = []
        scopes.append(lease)
        return self

    def __exit__(self, exc_type, error, traceback):
        self._owner_scopes.scopes.pop().__exit__(exc_type, error, traceback)
        self._close_after(error)

    def _close_after(self, error=None):
        try:
            self.close()
        except RenderCleanupError as cleanup:
            if error is not None:
                raise RenderCleanupError(f"{error}; {cleanup}") from error
            raise

    def __del__(self):
        # Best-effort retirement for legacy callers dropping an owned command.
        # Explicit close/context-manager use is the supported lifetime contract.
        try:
            if self._cleanup_failed:
                logging.getLogger(__name__).error("Render scratch retained after failed retirement: %s", self.root)
                return
            self.close()
        except Exception:
            logging.getLogger(__name__).exception("Unretired render context")


class RenderCommand(list):
    """List-compatible FFmpeg argv with a retained, explicitly usable lifetime."""
    def __init__(self, argv, context, *, owns_context=False):
        super().__init__(argv)
        self.context = context
        self.owns_context = owns_context
        self._leases = threading.local()

    def close(self):
        if self.owns_context:
            self.context.close()

    @property
    def cwd(self):
        """Pass with argv when using the standard subprocess API directly."""
        return self.context.command_cwd

    def __getitem__(self, key):
        value = super().__getitem__(key)
        return RenderCommand(value, self.context, owns_context=self.owns_context) if isinstance(key, slice) else value

    def __add__(self, values):
        return RenderCommand(super().__add__(values), self.context, owns_context=self.owns_context)

    def __radd__(self, values):
        return RenderCommand(values + list(self), self.context, owns_context=self.owns_context)

    def copy(self):
        return self[:]

    def __enter__(self):
        lease = self.context.use()
        lease.__enter__()
        leases = getattr(self._leases, "leases", None)
        if leases is None: leases = self._leases.leases = []
        leases.append(lease)
        return self

    def __exit__(self, exc_type, error, traceback):
        self._leases.leases.pop().__exit__(exc_type, error, traceback)
        if self.owns_context:
            self.context._close_after(error)


def owned_operation(function):
    """Public synchronous entry points own a scope unless explicitly borrowed."""
    @wraps(function)
    def call(*args, context=None, **kwargs):
        holder = kwargs.get("proc_holder")
        # Existing public signatures permit positional holder/progress arguments.
        if holder is None and len(args) > 6:
            holder = args[6]
        progress = kwargs.get("progress", args[5] if len(args) > 5 else None)
        if context is None:
            with RenderContext(proc_holder=holder, progress=progress) as owned:
                with owned.use():
                    return function(*args, context=owned, **kwargs)
        if holder is not None and holder is not context.holder:
            raise ValueError("render context and proc_holder must share cancellation ownership")
        with context.use():
            return function(*args, context=context, **kwargs)
    return call


def context_helper(function):
    """Direct helper calls borrow the explicit caller-owned context too."""
    @wraps(function)
    def call(*args, context, **kwargs):
        with context.use():
            return function(*args, context=context, **kwargs)
    return call

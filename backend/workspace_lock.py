"""One cooperating Filmocity process per data folder, before migration or I/O.

The permanent marker's contents are irrelevant. Never replace or unlink it:
ownership is an OS lock on its open file, not the presence of a stale PID file.
The server retains ownership until process exit, including backend shutdown.
"""
import errno
import os
import stat
import threading

MARKER = '.filmocity.lock'
_PLATFORM = os.name
_held = {}
_guard = threading.RLock()


class WorkspaceLockError(RuntimeError):
    pass


class WorkspaceInUse(WorkspaceLockError):
    pass


def _lock(fd, *, release=False):
    if _PLATFORM == 'nt':
        import msvcrt
        # The byte range may extend beyond EOF. No marker writes are required.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1)
    elif _PLATFORM == 'posix':
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        raise WorkspaceLockError('This platform has no supported Filmocity data-folder lock.')


class WorkspaceLease:
    def __init__(self, root):
        self.root = os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(root))))
        self.pid = os.getpid()
        self.fd = None
        path = os.path.join(self.root, MARKER)
        fd = None
        try:
            os.makedirs(self.root, exist_ok=True)
            if os.path.islink(path):
                raise WorkspaceLockError(f'The data-folder lock must be a regular file: {path}')
            flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(path, flags, 0o600)
            os.set_inheritable(fd, False)
            opened, current = os.fstat(fd), os.stat(path, follow_symlinks=False)
            if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode)
                    or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
                    or opened.st_nlink != 1):
                raise WorkspaceLockError(f'The data-folder lock is linked or changed while opening: {path}')
            try:
                _lock(fd)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise WorkspaceInUse(
                        f'Filmocity is already using this data folder:\n{self.root}\n\n'
                        'Close that instance and wait for it to exit, or launch with --data pointing to a different folder.'
                    ) from error
                raise
            self.fd, fd = fd, None
        except OSError as error:
            raise WorkspaceLockError(f'Could not secure the Filmocity data folder:\n{self.root}\n\n{error}') from error
        finally:
            if fd is not None:
                os.close(fd)

    def close(self):
        """For process shutdown or an explicitly ended test/service lifetime."""
        with _guard:
            fd, self.fd = self.fd, None
            if fd is None:
                return
            try:
                # Unlocking an inherited flock could release the parent's lock.
                if self.pid == os.getpid():
                    _lock(fd, release=True)
            finally:
                os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def hold_workspace(root):
    """Retain a process-lifetime lease; repeated setup in this process is safe."""
    canonical = os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(root)))))
    with _guard:
        lease = _held.get(canonical)
        if lease is None or lease.fd is None or lease.pid != os.getpid():
            lease = WorkspaceLease(root)
            _held[canonical] = lease
        return lease


def _close_all():
    for lease in list(_held.values()):
        try:
            lease.close()
        except OSError:
            pass  # Never try to unlock the parent's file from a forked child.
    _held.clear()


def _after_fork():
    global _guard
    _guard = threading.RLock()
    _close_all()  # Close inherited handles without unlocking the parent's file.


# Keep raw descriptors until OS process teardown. Releasing at atexit could
# admit another editor while a daemon worker still finishes a project write.
if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork)

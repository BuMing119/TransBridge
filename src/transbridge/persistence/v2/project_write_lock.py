"""Shared reentrant Project publication boundary, fenced across OS processes."""

from contextlib import contextmanager
from threading import RLock, local
from weakref import WeakMethod

from .session_write_lease import persistence_write_lease


class ProjectWriteLock:
    def __init__(self, root, filesystem):
        self._root = root
        self._filesystem = filesystem
        self._lock = RLock()
        self._local = local()
        self._recovery = []

    def add_recovery(self, callback):
        with self._lock:
            self._recovery.append(WeakMethod(callback))

    def __enter__(self):
        return self._enter(recover=True)

    def acquire(self, blocking=True, timeout=-1):
        return bool(self._enter(recover=True, blocking=blocking, timeout=timeout))

    def release(self):
        self.__exit__(None, None, None)

    @contextmanager
    def reading(self):
        self._enter(recover=False)
        try:
            yield self
        finally:
            self.__exit__(None, None, None)

    def recover(self):
        if getattr(self._local, "recovering", False):
            return
        self._local.recovering = True
        self._local.recovered = False
        try:
            for reference in tuple(self._recovery):
                callback = reference()
                if callback is not None:
                    callback()
            self._recovery = [reference for reference in self._recovery if reference() is not None]
            self._local.recovered = True
        finally:
            self._local.recovering = False

    def _enter(self, *, recover, blocking=True, timeout=-1):
        if not self._lock.acquire(blocking, timeout):
            return False
        depth = getattr(self._local, "depth", 0)
        self._local.depth = depth + 1
        if depth:
            if recover and not getattr(self._local, "recovered", False):
                try:
                    self.recover()
                except BaseException:
                    self._local.depth -= 1
                    self._lock.release()
                    raise
            return self
        self._local.recovered = False
        lease = persistence_write_lease(self._root, "project-root", self._filesystem, "PROJECT_WRITER_BUSY")
        try:
            lease.__enter__()
            self._local.lease = lease
            if recover:
                self.recover()
        except BaseException:
            if getattr(self._local, "lease", None) is lease:
                lease.__exit__(None, None, None)
                self._local.lease = None
            self._local.depth = 0
            self._lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._local.depth -= 1
        try:
            if not self._local.depth:
                self._local.lease.__exit__(exc_type, exc, traceback)
                self._local.lease = None
        finally:
            self._lock.release()

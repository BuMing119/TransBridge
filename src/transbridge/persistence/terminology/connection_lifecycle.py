"""Keep project storage references valid when deletion releases a SQLite handle."""

from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from weakref import WeakSet

from transbridge.application.contracts import DomainError, ErrorCategory


class _LockedCursor:
    def __init__(self, cursor, owner) -> None:
        self._cursor = cursor
        self._owner = owner

    def __getattr__(self, name):
        with self._owner.lock:
            value = getattr(self._cursor, name)
        if not callable(value):
            return value

        def invoke(*args, **kwargs):
            with self._owner.lock:
                return getattr(self._cursor, name)(*args, **kwargs)

        return invoke

    def __iter__(self):
        return self

    def __next__(self):
        with self._owner.lock:
            return next(self._cursor)


class ProjectTerminologyConnection:
    """One stable connection reference shared by a project's repository slices.

    The repository lock also protects cursor operations. Deletion waits for
    existing repository operations, closes outstanding cursors and the handle,
    and restores the handle after a failed filesystem transaction. Existing UI
    profile services therefore remain usable after deletion rolls back.
    """

    def __init__(self, opened, *, reopen, lock) -> None:
        self.lock = lock
        self._connection = opened.connection
        self._path = opened.path
        self._reopen = reopen
        self._cursors = WeakSet()
        self.generation = 0

    def _require(self):
        if self._connection is None:
            raise sqlite3.ProgrammingError("项目术语数据库已关闭，工程可能正在删除或已经删除。")
        return self._connection

    def __getattr__(self, name):
        with self.lock:
            value = getattr(self._require(), name)
        if not callable(value):
            return value

        def invoke(*args, **kwargs):
            with self.lock:
                result = getattr(self._require(), name)(*args, **kwargs)
                if isinstance(result, sqlite3.Cursor):
                    result = _LockedCursor(result, self)
                    self._cursors.add(result)
                return result

        return invoke

    @property
    def row_factory(self):
        with self.lock:
            return self._require().row_factory

    @row_factory.setter
    def row_factory(self, value):
        with self.lock:
            self._require().row_factory = value

    def close(self) -> None:
        with self.lock:
            if self._connection is None:
                return
            for cursor in tuple(self._cursors):
                cursor.close()
            self._cursors.clear()
            self._connection.close()
            self._connection = None

    @contextmanager
    def deletion_scope(self):
        with self.lock:
            if self._require().in_transaction:
                raise DomainError(
                    ErrorCategory.CONFLICT,
                    "PROJECT_TERMINOLOGY_BUSY",
                    "项目术语数据库仍有未提交的事务，请等待术语操作完成后再删除工程。",
                )
            self.close()
            try:
                yield
            except Exception as deletion_error:
                try:
                    if not self._path.is_file():
                        raise FileNotFoundError(f"删除回滚未恢复术语数据库：{self._path}")
                    self._connection = self._reopen().connection
                    self.generation += 1
                except Exception as reopen_error:
                    raise ExceptionGroup(
                        "工程删除失败，且术语数据库无法重新打开。",
                        [deletion_error, reopen_error],
                    ) from deletion_error
                raise

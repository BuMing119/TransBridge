# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Keep application processes and a storage move from writing at the same time."""

from contextlib import contextmanager
import os

from .storage_location import StorageLocationError, location_directory


@contextmanager
def storage_access(*, exclusive: bool = False):
    directory = location_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "storage-access.lock"
    busy = "数据文件夹正在使用。请关闭其他 TransBridge 窗口和后台服务，稍后重试。"
    if os.name == "nt":
        import _winapi

        # Share read handles during normal use. A mover opens the same file without sharing.
        file_share_read, open_always, normal_attributes = 1, 4, 0x80
        try:
            handle = _winapi.CreateFile(
                str(path),
                _winapi.GENERIC_READ,
                0 if exclusive else file_share_read,
                0,
                open_always,
                normal_attributes,
                0,
            )
        except OSError as exc:
            if exc.winerror == 32:
                raise StorageLocationError(busy) from exc
            raise
        try:
            yield
        finally:
            _winapi.CloseHandle(handle)
    else:
        import fcntl

        with path.open("a+b") as stream:
            try:
                fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StorageLocationError(busy) from exc
            yield

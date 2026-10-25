"""Nonblocking process ownership of one logical recovery chain."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class TaskRecoveryLease:
    def __init__(self, root: Path, task_id: str):
        self.task_id = task_id
        self._stream = None
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{hashlib.sha256(task_id.encode()).hexdigest()}.lease"
        stream = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0, os.SEEK_END)
                if not stream.tell():
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise RuntimeError("此 AI 任务已在其他窗口或程序中运行，请先结束该执行再继续。") from exc
        self._stream = stream

    def close(self):
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

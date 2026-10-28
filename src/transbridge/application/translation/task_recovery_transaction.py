"""Short-lived cross-process transactions, independent of execution ownership."""

from contextlib import contextmanager
import hashlib
import os


@contextmanager
def recovery_transaction(root, task_id):
    directory = root / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hashlib.sha256(task_id.encode()).hexdigest()[:16]}.lock"
    stream = path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if not stream.tell():
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()

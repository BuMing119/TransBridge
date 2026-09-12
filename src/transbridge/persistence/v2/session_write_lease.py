"""Process-scoped OS leases for Session compare-and-save transactions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import os
from threading import local

from .filesystem import OsPersistenceFilesystem, PersistenceFilesystemPort, RepositoryPaths
from .models import ReadOnlyWriteRefused

_HELD = local()


@contextmanager
def session_write_lease(root: str, session_id: str, filesystem: PersistenceFilesystemPort) -> Iterator[None]:
    """Fail fast when another process owns this Session's write boundary.

    OS locks are released on process death. The persistent lock file is only an
    identity, never evidence that a dead process still owns a lease. Injected
    in-memory adapters share the repository root lock without touching real disk.
    """
    if not isinstance(filesystem, OsPersistenceFilesystem):
        yield
        return
    paths = RepositoryPaths(root, filesystem)
    key = hashlib.sha256(session_id.encode()).hexdigest()
    path = paths.guard(os.path.join(paths.root, ".leases", f"session-{key}.lock"))
    lease_key = os.path.normcase(path)
    held = getattr(_HELD, "paths", None)
    if held is None:
        held = _HELD.paths = set()
    if lease_key in held:
        yield
        return
    filesystem.make_dirs(os.path.dirname(path))
    # Opening the same persistent inode matters: unlinking lock files can allow
    # old/new processes to lock different inodes for the same Session.
    with open(path, "a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ReadOnlyWriteRefused(
                "SESSION_WRITER_BUSY", "Another process is writing this Session; reload and retry after it releases."
            ) from exc
        held.add(lease_key)
        try:
            yield
        finally:
            held.remove(lease_key)
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

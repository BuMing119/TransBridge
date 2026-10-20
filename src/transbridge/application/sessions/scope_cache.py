"""Reuse verified Session scope metadata only while its source record is unchanged."""

from collections.abc import Callable
import os

from transbridge.persistence.v2.ids import SessionRef

from .models import SessionSnapshot


class SessionScopeCache:
    def __init__(self, repository, read_snapshot: Callable[[SessionRef], SessionSnapshot]):
        self._repository = repository
        self._read_snapshot = read_snapshot
        self._entries = {}

    def read(self, ref: SessionRef) -> tuple[str | None, str | None]:
        signature = _record_signature(self._repository, ref)
        cached = self._entries.get(ref.identity.value)
        if signature is not None and cached is not None and cached[0] == signature:
            return cached[1]
        snapshot = self._read_snapshot(ref)
        scope = (
            None if snapshot.project_id is None else snapshot.project_id.value,
            None if snapshot.variant_id is None else snapshot.variant_id.value,
        )
        if signature is not None and signature == _record_signature(self._repository, ref):
            self._entries[ref.identity.value] = (signature, scope)
        else:
            self._entries.pop(ref.identity.value, None)
        return scope

    def retain(self, session_ids: set[str]) -> None:
        self._entries = {key: value for key, value in self._entries.items() if key in session_ids}


def _record_signature(repository, ref: SessionRef) -> tuple[int, ...] | None:
    path_for = getattr(repository, "path_for", None)
    if not callable(path_for):
        return None
    try:
        stat = os.stat(path_for(ref))
    except OSError:
        # Non-filesystem adapters and unavailable records always take the verified
        # read path; a missing file must never inherit its formerly cached scope.
        return None
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size

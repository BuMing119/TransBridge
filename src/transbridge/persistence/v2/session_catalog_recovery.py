"""Reconstruct derived Session catalog rows without changing conversation records."""

from __future__ import annotations

import base64
import binascii
import logging
import os
from pathlib import Path

from .filesystem import PersistenceFilesystemPort, RepositoryPaths
from .ids import SessionId, SessionRef
from .models import PersistenceV2Error
from .repository import SessionRepository

_logger = logging.getLogger(__name__)


def recover_catalog_rows(root: str, filesystem: PersistenceFilesystemPort) -> tuple[dict, ...]:
    # Import at use time: the application facade also imports the catalog repository.
    from transbridge.application.sessions.models import SessionSnapshot

    paths = RepositoryPaths(root, filesystem)
    repository = SessionRepository(root, filesystem)
    directory = paths.guard(os.path.join(root, "sessions"))
    rows = []
    seen = set()
    for candidate in filesystem.list_files(directory):
        try:
            path = paths.guard(candidate)
            identity = _filename_identity(path)
            if identity is None:
                continue
            ref = SessionRef(identity)
            if os.path.normcase(repository.path_for(ref)) != os.path.normcase(path):
                raise ValueError("Session record is not at its canonical path")
        except (ValueError, PersistenceV2Error):
            _logger.warning("Skipping a noncanonical Session record during catalog recovery: %s", candidate)
            continue
        if identity in seen:
            continue
        seen.add(identity)
        try:
            snapshot = SessionSnapshot.from_dto(repository.read_snapshot(ref), ref)
            rows.append({
                "session_id": identity.value,
                "name": snapshot.name,
                "last_active_at": snapshot.last_active_at,
                "message_count": len(snapshot.messages),
                "recovery": snapshot.recovery.value,
                "project_id": None if snapshot.project_id is None else snapshot.project_id.value,
                "variant_id": None if snapshot.variant_id is None else snapshot.variant_id.value,
            })
        except (OSError, ValueError, TypeError, KeyError, PersistenceV2Error):
            # Keep the record discoverable. Its identity is only a filename locator;
            # the facade still verifies the record and owner before any execution.
            _logger.warning("Preserving an unreadable Session in the recovered catalog: %s", path, exc_info=True)
            rows.append({
                "session_id": identity.value,
                "name": "暂不可用的会话",
                "last_active_at": "0001-01-01T00:00:00Z",
                "message_count": 0,
                "recovery": "degraded",
            })
    return tuple(rows)


def _filename_identity(path: str) -> SessionId | None:
    filename = Path(path)
    if filename.suffix != ".json" or not filename.stem.startswith("id-"):
        return None
    encoded = filename.stem[3:]
    try:
        value = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode("ascii")
        identity = SessionId(value)
    except (UnicodeError, ValueError, binascii.Error):
        return None
    return identity if identity.encoded == filename.stem else None

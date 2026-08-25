"""Root-confined atomic documents shared by lifecycle and repair services."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .filesystem import PersistenceFilesystemPort


class AtomicDocumentStore:
    """Publish small root documents through verified staging and atomic replace."""

    def __init__(self, root: str, filesystem: PersistenceFilesystemPort) -> None:
        if not os.path.isabs(root):
            raise ValueError("persistence document root must be absolute")
        self._filesystem = filesystem
        self._root = filesystem.canonicalize(root)

    def write_json(self, relative_path: str, document: dict[str, Any], token: str) -> None:
        destination = self.path(relative_path)
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.write_bytes(destination, payload, token)

    def path(self, relative_path: str) -> str:
        return self._guard(os.path.join(self._root, relative_path))

    def write_bytes(self, destination: str, payload: bytes, token: str) -> None:
        destination = self._guard(destination)
        suffix = hashlib.sha256(token.encode()).hexdigest()
        stage = self._guard(os.path.join(self._root, ".staging", f"document-{suffix}.tmp"))
        self._filesystem.make_dirs(os.path.dirname(destination))
        self._filesystem.make_dirs(os.path.dirname(stage))
        self._filesystem.remove(stage, missing_ok=True)
        try:
            self._filesystem.write_bytes(stage, payload)
            if self._filesystem.read_bytes(stage) != payload:
                raise OSError("document staging verification failed")
            self._filesystem.replace(stage, destination)
        except Exception:
            try:
                self._filesystem.remove(stage, missing_ok=True)
            except Exception:
                # Cleanup failure must not replace the publication failure.
                pass
            raise

    def _guard(self, path: str) -> str:
        canonical = self._filesystem.canonicalize(path)
        try:
            common = os.path.commonpath((self._root, canonical))
        except ValueError as exc:
            raise ValueError("document path is on a different root") from exc
        if os.path.normcase(common) != os.path.normcase(self._root):
            raise ValueError("document path escapes persistence root")
        return canonical


__all__ = ["AtomicDocumentStore"]

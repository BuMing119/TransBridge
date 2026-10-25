"""Restore mutable task references after a rejected local term publication."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import fields
from typing import TYPE_CHECKING

from .shared_term_snapshot import FrozenTermEntry
from .term_formats import TermEntry

if TYPE_CHECKING:
    from .term_database import DynamicTermDatabase


@contextmanager
def dynamic_term_transaction(database: DynamicTermDatabase):
    """Keep reload/save/acknowledgement private until the write is accepted.

    Reload and add mutate records retained by manager source views. Rollback
    restores those exact objects, then invalidates lookup caches by revision.
    Files are never restored here: they may now belong to an external writer.
    """
    with database._lock:
        original_list = database._entries
        originals = tuple((entry, FrozenTermEntry.capture(entry)) for entry in original_list)
        try:
            yield
        except Exception:
            for entry, snapshot in originals:
                restored = snapshot.mutable_copy()
                for field in fields(TermEntry):
                    setattr(entry, field.name, getattr(restored, field.name))
            original_list[:] = [entry for entry, _snapshot in originals]
            database._entries = original_list
            database._revision += 1
            raise

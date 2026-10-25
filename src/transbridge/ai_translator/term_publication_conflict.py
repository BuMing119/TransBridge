"""An external terminology edit prevents publication, not translation use."""

from __future__ import annotations


class TermPublicationConflict(RuntimeError):
    """Refuse an automatic term write while preserving task-owned references.

    Only an observed external write/ownership conflict belongs to this type.
    Storage errors, corrupt initial data and unexpected failures must propagate
    separately, so callers cannot mistake them for a harmless skipped write.
    """

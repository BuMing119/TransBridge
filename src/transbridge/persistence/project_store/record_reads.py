# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read-only record directory and indexed retrieval of one record's fields."""

import json
from types import SimpleNamespace

from transbridge.application.dialogue.record_fields import record_identity

from .entry_codec import decode_key, unpack_text
from .reads import read_entries


def read_record_entries(connection, token, identity, cache):
    """Build a minimal key directory once per source revision, never changing a project file."""
    source = connection.execute(
        """SELECT s.id, s.fingerprint FROM project_source AS ps
            JOIN source AS s ON s.id = ps.source_id WHERE ps.namespace = ?""",
        (identity.namespace.value,),
    ).fetchone()
    if source is None:
        return ()
    cache_key = token.project_id, token.project_revision, identity.namespace, source["id"], source["fingerprint"]
    groups = cache.get(cache_key)
    if groups is None:
        groups = {}
        for row in connection.execute(
            """SELECT ? AS namespace, local_key, identity_original, has_identity_original, context, metadata
                FROM source_entry WHERE source_id = ? ORDER BY ordinal""",
            (identity.namespace.value, source["id"]),
        ):
            key = decode_key(row)
            entry = SimpleNamespace(
                identity=key,
                context=None if row["context"] is None else unpack_text(row["context"]),
                metadata=tuple(json.loads(row["metadata"]).items()),
            )
            record = record_identity(entry)
            if record is not None:
                groups.setdefault(record, []).append(key)
        # Keep one source revision. Replacements and source switches release
        # the previous directory rather than accumulating project-wide caches.
        cache.clear()
        cache[cache_key] = groups
    # Existing read_entries batches 200 keys per indexed query and preserves
    # requested order, including large quest records and original-bound keys.
    return read_entries(connection, token.variant_id, groups.get(identity, ()))

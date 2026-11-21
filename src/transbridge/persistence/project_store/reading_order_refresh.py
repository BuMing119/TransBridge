# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Refresh legacy derived SQL ranks once from saved plugin business facts."""

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import sqlite3
import stat

from transbridge.application.io.dialogue_reading_view import ReadingEntry, build_dialogue_reading_view
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.io.plugin_dialogue_order import METADATA_KEY, dialogue_order_from_snapshot
from transbridge.application.projects.source_extraction import reconstructed_source_snapshot
from transbridge.parser.plugin.dialogue_order_evidence import evidence_payload

from .connection import project_connection
from .entry_codec import decode_key, encode_json, immutable_json, unpack_text
from .format import ProjectStorageConflict, ProjectStoreError
from .reads import read_sources

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Refresh:
    source_id: int
    source_identity: tuple
    expected_fields: str
    updated_fields: str
    ranks: tuple[tuple[int, int], ...]


def refresh_saved_reading_order(path: Path, checked: set[tuple], *, namespace: SourceNamespace | None = None) -> None:
    """Use the native source revision/evidence version as the durable marker.

    Callers serialize this work per store and run it in their existing project
    open/page worker. The hot path reads source/project revision identities only. Preparation never
    opens reference paths, reads translations, or holds the write lease.
    """
    with project_connection(path) as connection:
        source_filter = "" if namespace is None else " AND ps.namespace = ?"
        args = () if namespace is None else (namespace.value,)
        sources = tuple(
            connection.execute(
                f"""SELECT s.id, ps.namespace, s.fingerprint, p.identity, p.revision FROM project_source AS ps
                JOIN source AS s ON s.id = ps.source_id
                CROSS JOIN project AS p
                WHERE s.format = 'plugin.sse'{source_filter}""",
                args,
            )
        )
        identities = {_identity(row) for row in sources}
        namespaces = {row["namespace"] for row in sources}
        if namespace is None:
            checked.intersection_update(identities)
        else:
            checked.difference_update(key for key in tuple(checked) if key[1] in namespaces and key not in identities)
        pending = [row for row in sources if _identity(row) not in checked]
        if not pending:
            return
        plans = tuple(plan for row in pending if (plan := _prepare(connection, row)) is not None)
    completed = {row["id"] for row in pending} - {plan.source_id for plan in plans}
    if plans:
        try:
            if not path.stat().st_mode & stat.S_IWRITE:
                raise PermissionError("project database is read-only")
            with project_connection(path, write=True) as connection:
                for plan in plans:
                    if _apply(connection, plan):
                        completed.add(plan.source_id)
        except (PermissionError, ProjectStoreError) as exc:
            cause = exc.__cause__
            if not isinstance(exc, PermissionError) and (
                not isinstance(cause, sqlite3.Error)
                or getattr(cause, "sqlite_errorcode", 0) & 0xFF != sqlite3.SQLITE_READONLY
            ):
                raise
            _logger.warning(
                "PROJECT_READING_ORDER_NOT_WRITABLE: retained saved ranks; open a writable project copy to refresh: %s",
                exc,
            )
            completed.update(plan.source_id for plan in plans)
    checked.update(_identity(row) for row in pending if row["id"] in completed)


def _identity(row):
    # SQLite may reuse INTEGER PRIMARY KEYs after removal. Project revision also
    # distinguishes a restored source with the same namespace and content hash.
    return row["id"], row["namespace"], row["fingerprint"], row["identity"], row["revision"]


def _prepare(connection, source) -> _Refresh | None:
    (version,) = connection.execute(
        "SELECT json_extract(format_fields, '$.plugin_dialogue_order[0]') FROM source WHERE id = ?", (source["id"],)
    ).fetchone()
    if type(version) is not int or version != 1:
        return None
    (info,) = read_sources(connection, SourceNamespace(source["namespace"]))
    fields = dict(info.format_fields)
    value = fields.get(METADATA_KEY)
    if not isinstance(value, tuple) or len(value) != 3 or type(value[0]) is not int or value[0] != 1:
        return None
    evidence = dialogue_order_from_snapshot(reconstructed_source_snapshot(info, ()))
    if evidence is None or not evidence.complete:
        _logger.warning("PROJECT_READING_ORDER_UNAVAILABLE: retained saved ranks for %s", info.namespace.value)
        return None
    entries = []
    identities = []
    for row in connection.execute(
        """SELECT id, local_key, identity_original, has_identity_original, original, context, metadata,
            ? AS namespace FROM source_entry WHERE source_id = ? ORDER BY ordinal""",
        (info.namespace.value, source["id"]),
    ):
        key = decode_key(row)
        identities.append((row["id"], key))
        entries.append(
            ReadingEntry(
                key,
                None if row["context"] is None else unpack_text(row["context"]),
                tuple((key, immutable_json(value)) for key, value in json.loads(row["metadata"]).items()),
                bool(unpack_text(row["original"]).strip()),
            )
        )
    view = build_dialogue_reading_view(entries, evidence)
    if len(view.ordered_keys) != len(identities):
        raise ValueError("refreshed reading order must cover the complete stored plugin source")
    ranks = {key: index for index, key in enumerate(view.ordered_keys)}
    (expected_fields,) = connection.execute("SELECT format_fields FROM source WHERE id = ?", (source["id"],)).fetchone()
    # Saved catalog topics support the v2 fallback, but have no phase provenance.
    # Only an explicit source refresh may publish phase-aware extraction v3.
    fields[METADATA_KEY] = (2, info.fingerprint, evidence_payload(evidence))
    return _Refresh(
        source["id"],
        _identity(source),
        expected_fields,
        encode_json(fields),
        tuple((ranks[key], entry_id) for entry_id, key in identities),
    )


def _apply(connection, plan: _Refresh) -> bool:
    # Source rows/identities are immutable revisions. Source replacement or a
    # concurrent refresh must never apply a plan to another installed revision.
    current = connection.execute(
        """SELECT s.id, ps.namespace, s.fingerprint, p.identity, p.revision, s.format_fields
        FROM source AS s JOIN project_source AS ps ON ps.source_id = s.id CROSS JOIN project AS p
        WHERE s.id = ?""",
        (plan.source_id,),
    ).fetchone()
    if (
        current is None
        or _identity(current) != plan.source_identity
        or current["format_fields"] != plan.expected_fields
    ):
        return False
    connection.execute("UPDATE source_entry SET reading_ordinal = NULL WHERE source_id = ?", (plan.source_id,))
    connection.executemany("UPDATE source_entry SET reading_ordinal = ? WHERE id = ?", plan.ranks)
    changed = connection.execute(
        "UPDATE source SET format_fields = ? WHERE id = ? AND format_fields = ?",
        (plan.updated_fields, plan.source_id, plan.expected_fields),
    )
    if changed.rowcount != 1:
        raise ProjectStorageConflict("saved reading order changed before its refresh could be published")
    return True

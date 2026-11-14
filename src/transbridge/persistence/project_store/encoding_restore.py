# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Recover entry identities between saved source revisions of the same input."""

from collections import defaultdict
from dataclasses import replace
import json

from transbridge.application.io import FormatId, Stage
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.source_encoding import TEXT_ENCODING_FORMATS
from transbridge.application.projects.text_encoding_matching import match_encoding_rows

from .entry_codec import decode_entry
from .history import snapshot_entry_rows


def encoding_restore_entries(connection, identity, current_rows):
    sources = {
        SourceNamespace(row["namespace"]): FormatId(row["format"])
        for row in connection.execute(
            """
            SELECT live.namespace, live.format, live.format_fields, live.options,
                old.format_fields AS old_format_fields, old.options AS old_options
            FROM project_source AS ps JOIN source AS live ON live.id = ps.source_id
            JOIN source AS old ON old.namespace = live.namespace AND old.fingerprint = live.fingerprint
                AND old.format = live.format AND old.id != live.id
            JOIN snapshot_source AS ss ON ss.snapshot_id = ? AND ss.source_id = old.id
            """,
            (identity,),
        )
        if FormatId(row["format"]) in TEXT_ENCODING_FORMATS
        and _read_facts(row["format_fields"], row["options"])
        != _read_facts(row["old_format_fields"], row["old_options"])
    }
    if not sources:
        return {}, set()
    old_rows = defaultdict(list)
    for row in snapshot_entry_rows(connection, identity, -1, 0):
        entry = decode_entry(row).snapshot
        old_rows[entry.entry_key.namespace].append(entry)
    remapped = {}
    changed = set()
    for namespace, fmt in sources.items():
        rows_before = tuple(row.snapshot for row in current_rows[namespace])
        rows_after = old_rows[namespace]
        matches = match_encoding_rows(rows_before, rows_after, plugin_order=fmt is FormatId.PLUGIN_SSE)
        keys = {EntryKey(namespace, *new): EntryKey(namespace, *old) for new, old in matches.items()}
        remapped.update(keys)
        prior = {row.entry_key: (row.original, row.context) for row in rows_after}
        changed.update(
            row.entry_key for row in rows_before if (row.original, row.context) != prior[keys[row.entry_key]]
        )
    return remapped, changed


def rekey_states(entries, remapped, changed, *, mark_review):
    for item in entries:
        key = remapped.get(item.entry_key, item.entry_key)
        review = (
            mark_review
            and item.entry_key in changed
            and item.translation
            and item.stage not in {Stage.HIDDEN, Stage.LOCKED}
        )
        if key != item.entry_key or review:
            item = replace(
                item, entry_key=key, revision=item.revision.next(), stage=Stage.QUESTIONABLE if review else item.stage
            )
        yield item


def _read_facts(format_fields, parse_options):
    fields = json.loads(format_fields)
    options = json.loads(parse_options)
    return (
        fields.get("encoding", options.get("input_encoding")),
        fields.get("source_language", options.get("source_language")),
        fields.get("text_read_policy", options.get("text_read_policy")),
        fields.get("localized_language", options.get("language")),
    )

# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Validate portable project content before any live directory is published."""

from __future__ import annotations

import json
import math
import re

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, Provenance, SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.io.sst_export import PLUGIN_SST_MASTERS
from transbridge.application.projects.remote_binding import ParaTranzProjectBinding
from transbridge.application.projects.source_content import source_content_identity
from transbridge.application.projects.source_fields import (
    business_fields,
    entry_fields,
    format_fields,
    parse_options,
    source_name,
)
from transbridge.application.projects.source_registry import SourceRegistrySnapshot
from transbridge.application.projects.storage_contracts import StoredEntry
from transbridge.config.text_encoding import (
    TEXT_INPUT_ENCODING,
    TEXT_OUTPUT_ENCODING,
    TEXT_SOURCE_LANGUAGE,
    TEXT_TARGET_LANGUAGE,
    TextEncodings,
)
from transbridge.persistence.terminology.project_selection import decode_selection
from transbridge.persistence.v2.ids import VariantId
from transbridge.persistence.v2.variant import SourceFingerprint, VariantEntryState

from .archive_native import validate_format, validate_native_entry, validate_xml_record
from .entry_codec import encode_json, pack_text, unpack_text
from .format import UnsupportedProjectFormat
from .reads import read_summary
from .records import _source_registration
from .source_references import validate_references

_SETTINGS = frozenset({
    "sources",
    "source_relations",
    "source_registry_diagnostics",
    TEXT_INPUT_ENCODING,
    TEXT_OUTPUT_ENCODING,
    TEXT_SOURCE_LANGUAGE,
    TEXT_TARGET_LANGUAGE,
})
_SOURCE_PATH = re.compile(r"(?i)^(?:file:|[a-z]:[\\/]|[\\/])")


def archive_json(data):
    return json.loads(data, object_pairs_hook=_unique_pairs, parse_constant=_invalid_number, parse_float=_finite_float)


def validate_archive_database(connection, manifest_project_id):
    try:
        if (
            connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise ValueError("database is damaged or has invalid references")
        summary = read_summary(connection)
        if summary.identity.value != manifest_project_id:
            raise ValueError("project identity differs from its manifest")
        _display_text(_text(connection.execute("SELECT name FROM project").fetchone()[0]))
        _reject_invalid_references(connection)
        validate_references(connection)
        source_rows = _validate_sources(connection)
        settings = connection.execute("SELECT settings FROM project").fetchone()[0]
        active_sources = (source_rows[row[0]] for row in connection.execute("SELECT source_id FROM project_source"))
        _validate_settings(settings, tuple(active_sources), allow_bindings=True)
        _validate_variants(connection, summary.identity.value)
        _validate_snapshots(connection, source_rows)
        _validate_entry_rows(connection, source_rows)
        _validate_variant_states(connection, "entry_default")
        _validate_variant_states(connection, "entry_state")
        _validate_variant_states(connection, "snapshot_entry_state")
        return summary
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise UnsupportedProjectFormat(f"Invalid portable project content: {exc}") from exc


def _reject_invalid_references(connection):
    queries = (
        ("SELECT 1 FROM commit_receipt LIMIT 1", "package contains task application receipts"),
        (
            """
            SELECT 1 FROM project_source AS ps JOIN source AS s ON s.id = ps.source_id
            WHERE ps.namespace != s.namespace LIMIT 1
            """,
            "active source namespace differs from its source revision",
        ),
        (
            """
            SELECT 1 FROM source AS s WHERE s.entry_count != (
                SELECT count(*) FROM source_entry AS e WHERE e.source_id = s.id
            ) LIMIT 1
            """,
            "source entry count differs from its stored entries",
        ),
        (
            """
            SELECT 1 FROM source_entry AS e JOIN source AS s ON s.id = e.source_id
            WHERE (s.format = 'plugin.sse' AND (e.reading_ordinal IS NULL OR e.reading_ordinal >= s.entry_count))
                OR (s.format != 'plugin.sse' AND e.reading_ordinal IS NOT NULL) LIMIT 1
            """,
            "source reading ranks do not cover the expected translation range",
        ),
        (
            """
            SELECT 1 FROM source AS s WHERE NOT EXISTS (SELECT 1 FROM project_source AS ps WHERE ps.source_id = s.id)
            AND NOT EXISTS (SELECT 1 FROM snapshot_source AS ss WHERE ss.source_id = s.id) LIMIT 1
            """,
            "package contains an unreferenced source revision",
        ),
        (
            """
            SELECT 1 FROM entry_state AS st JOIN source_entry AS e ON e.id = st.entry_id
            WHERE NOT EXISTS (SELECT 1 FROM project_source AS ps WHERE ps.source_id = e.source_id) LIMIT 1
            """,
            "Variant state references an inactive source revision",
        ),
        (
            """
            SELECT 1 FROM snapshot_entry_state AS st JOIN source_entry AS e ON e.id = st.entry_id
            WHERE NOT EXISTS (SELECT 1 FROM snapshot_source AS ss WHERE ss.snapshot_id = st.snapshot_id
                AND ss.source_id = e.source_id) LIMIT 1
            """,
            "snapshot state references another source revision",
        ),
        (
            """
            SELECT 1 FROM snapshot_source AS ss JOIN source AS s ON s.id = ss.source_id
            GROUP BY ss.snapshot_id, s.namespace HAVING count(*) > 1 OR min(ss.ordinal) < 0 LIMIT 1
            """,
            "snapshot contains duplicate sources or invalid source ordering",
        ),
    )
    for sql, error in queries:
        if connection.execute(sql).fetchone() is not None:
            raise ValueError(error)


def _validate_sources(connection):
    sources = {}
    for row in connection.execute("SELECT * FROM source"):
        namespace = SourceNamespace(row["namespace"])
        if namespace.value != row["namespace"]:
            raise ValueError("source namespace is not canonical")
        SourceFingerprint(namespace, row["fingerprint"])
        name = _display_text(_text(row["name"]))
        if source_name(name) != name or _SOURCE_PATH.match(name):
            raise ValueError("source name contains a historical path")
        format_id = FormatId(row["format"])
        options = _object(row["options"])
        fields = _object(row["format_fields"])
        _canonical_fields(options, parse_options)
        _canonical_fields(fields, format_fields)
        validate_format(format_id, fields, row["fingerprint"])
        sources[row["id"]] = (namespace, format_id, row["fingerprint"], PLUGIN_SST_MASTERS in fields)
    for row in connection.execute(
        "SELECT source_id, record, tail FROM source_format_record ORDER BY source_id, ordinal"
    ):
        validate_xml_record(sources[row["source_id"]][1], row["record"], row["tail"])
    return sources


def _validate_settings(value, sources, *, allow_bindings=False):
    settings = _object(value)
    if settings.keys() - (_SETTINGS | ({"remote_bindings"} if allow_bindings else set())):
        raise ValueError("project settings contain unapproved fields")
    TextEncodings.from_project_data(settings)
    for item in _list(settings.get("sources", [])):
        if not isinstance(item, dict) or encode_json(_source_registration(item)) != encode_json(item):
            raise ValueError("source registration contains a historical path or unapproved fields")
        _path_free(item.get("plugin_scope"))
        for value in item.get("legacy", {}).values():
            if value is not None and not isinstance(value, str):
                raise ValueError("source identity metadata must be text")
            _path_free(value)
    registry = SourceRegistrySnapshot.from_project_data(settings)
    available = {namespace.value: (format_id, fingerprint) for namespace, format_id, fingerprint, _ in sources}
    folded = {item.from_source_id for item in registry.relations if item.folded_into_baseline}
    for item in registry.sources:
        namespace = source_content_identity(item.to_dict())
        stored = available.get(namespace)
        if item.source_id not in folded and stored != (item.format_id, item.fingerprint):
            raise ValueError("source registration does not match a stored translation revision")
    relations = {relation.relation_id: relation.to_dict() for relation in registry.relations}
    for item in _list(settings.get("source_relations", [])):
        if not isinstance(item, dict) or item != relations.get(item.get("relation_id")):
            raise ValueError("source relation contains unapproved fields")
    for item in _list(settings.get("source_registry_diagnostics", [])):
        if not isinstance(item, dict) or item.keys() - {"code", "source_id"} or not isinstance(item.get("code"), str):
            raise ValueError("source registry diagnostic must contain a code and identity")
        for value in item.values():
            if not isinstance(value, str):
                raise ValueError("source registry diagnostic fields must be text")
            _path_free(value)
    if "remote_bindings" in settings:
        binding = settings["remote_bindings"]
        if not isinstance(binding, dict) or set(binding) != {"paratranz"} or not isinstance(binding["paratranz"], dict):
            raise ValueError("project external binding type is unsupported")
        if ParaTranzProjectBinding.from_mapping(binding["paratranz"]).to_dict() != binding["paratranz"]:
            raise ValueError("external binding contains unapproved data")


def _validate_variants(connection, project_id):
    for row in connection.execute("SELECT identity, name, label_library, terminology_selection FROM variant"):
        _display_text(_text(row["name"]))
        _validate_labels(row["label_library"])
        if row["terminology_selection"] is not None:
            archive_json(row["terminology_selection"])
            decode_selection(row["terminology_selection"], project_id, row["identity"])


def _validate_snapshots(connection, sources):
    for row in connection.execute("SELECT * FROM named_snapshot"):
        VariantId(row["identity"])
        VariantId(row["variant_id"])
        EntryRevision(row["revision"])
        _display_text(_text(row["name"]))
        _validate_labels(row["label_library"])
        referenced = connection.execute(
            "SELECT source_id FROM snapshot_source WHERE snapshot_id = ?", (row["identity"],)
        )
        _validate_settings(row["source_settings"], tuple(sources[item[0]] for item in referenced))


def _validate_entry_rows(connection, sources):
    for row in connection.execute("SELECT * FROM source_entry"):
        namespace, format_id, fingerprint, has_plugin_sst = sources[row["source_id"]]
        original = _text(row["original"])
        key = EntryKey(namespace, _text(row["local_key"]), original if row["has_identity_original"] else None)
        if key.local_key != _text(row["local_key"]):
            raise ValueError("entry key is not canonical")
        metadata = _object(row["metadata"])
        _canonical_fields(metadata, entry_fields)
        snapshot = EntrySnapshot(
            key,
            _text(row["legacy_id"]),
            original,
            "",
            0,
            _text(row["context"]) if row["context"] is not None else None,
            (),
            EntryRevision(),
            (),
            tuple(metadata.items()),
            row["string_id"],
            key.original is not None,
        )
        validate_native_entry(
            format_id,
            StoredEntry(snapshot, VariantEntryState(key)),
            metadata,
            fingerprint,
            row["ordinal"],
            has_plugin_sst,
        )


def _validate_variant_states(connection, table):
    key = EntryKey(SourceNamespace("archive-validation"), "entry")
    for row in connection.execute(f"SELECT * FROM {table}"):
        if row["tombstone"] not in (0, 1):
            raise ValueError("invalid entry removal state")
        VariantEntryState(
            key,
            _text(row["translation"]),
            row["stage"],
            _strings(row["labels"]),
            tuple(_business_item(item, Provenance) for item in _list(archive_json(row["provenance"]))),
            EntryRevision(row["revision"]),
            bool(row["tombstone"]),
            _strings(row["inferred_fields"]),
            tuple(_business_item(item, ExternalEntryRef) for item in _list(archive_json(row["external_refs"]))),
        )


def _business_item(raw, item_type):
    if not isinstance(raw, dict):
        raise ValueError("entry business reference must be an object")
    item = item_type.from_dict(raw)
    if item.to_dict() != raw:
        raise ValueError("entry business reference has unapproved fields")
    business_fields(item.metadata)
    for name in ("source", "scope", "opaque_id"):
        _path_free(raw.get(name))
    return item


def _validate_labels(value):
    for label_id, item in _object(value).items():
        if not label_id.strip() or not isinstance(item, dict) or set(item) - {"name", "color"}:
            raise ValueError("label metadata contains unsupported fields")
        _display_text(item.get("name", ""))
        if "color" in item and not isinstance(item["color"], str):
            raise ValueError("label color must be text")


def _canonical_fields(values, normalize):
    if encode_json(normalize(values.items())) != encode_json(values):
        raise ValueError("source fields contain unapproved metadata")


def _path_free(value):
    if isinstance(value, str) and _SOURCE_PATH.match(value):
        raise ValueError("project metadata contains a historical source path")


def _display_text(value):
    if not isinstance(value, str) or not value.strip() or any(character in value for character in "\x00\r\n\t"):
        raise ValueError("project display text is invalid")
    return value


def _text(value):
    decoded = unpack_text(value)
    if not isinstance(decoded, str) or pack_text(decoded) != value:
        raise ValueError("entry text is not a canonical stored string")
    return decoded


def _strings(value):
    items = _list(archive_json(value))
    if any(not isinstance(item, str) or not item for item in items):
        raise ValueError("entry label and inferred fields must contain strings")
    return tuple(items)


def _object(value):
    result = archive_json(value)
    if not isinstance(result, dict):
        raise ValueError("database metadata must be an object")
    return result


def _list(value):
    if not isinstance(value, list):
        raise ValueError("database metadata must be an array")
    return value


def _unique_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("database metadata contains duplicate keys")
        result[key] = value
    return result


def _invalid_number(_value):
    raise ValueError("database metadata contains a non-finite number")


def _finite_float(value):
    number = float(value)
    return number if math.isfinite(number) else _invalid_number(value)

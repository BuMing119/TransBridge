# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Canonical ordinal identities for complete xTranslator SST records."""

from dataclasses import asdict, replace
import hashlib
import json

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.parser.xt.sst_codec import SstDocument, SstRecord
from transbridge.parser.xt.sst_parser import SST_Entry

from .translation_entry import TranslationEntry

SST_CODEC_VERSION = 2


def record_signature(record: SstRecord) -> str:
    """Bind immutable record fields and source text; translation is editable."""
    fields = asdict(record)
    fields.pop("translation")
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()


def create_sst_entry(sst_entry: SST_Entry, namespace: SourceNamespace | None = None) -> TranslationEntry:
    """Map a parsed physical record without fabricating unavailable file context."""
    if sst_entry.ordinal < 0 or sst_entry.record is None:
        raise ValueError("SST identity requires a parsed physical record and ordinal; reimport the SST source")
    record = sst_entry.to_record()
    ordinal = sst_entry.ordinal
    key = f"sst-v2:{ordinal}"
    return TranslationEntry(
        id=key,
        key=key,
        original=record.original,
        translation=record.translation,
        stage=1 if record.translation else 0,
        context=f"{record.record_type}:{record.field_type}",
        string_id=record.string_id,
        dsd_type=f"{record.record_type} {record.field_type}",
        dsd_index=record.index + 1,
        entry_key=EntryKey(namespace or SourceNamespace.legacy(), key),
        metadata=(
            ("sst.codec_version", SST_CODEC_VERSION),
            ("sst.record_index", ordinal),
            ("sst.record_signature", record_signature(record)),
            ("sst.form_id", record.form_id),
            ("sst.list_index", record.list_index),
            ("sst.index", record.index),
        ),
    )


def document_entries(document: SstDocument, namespace: SourceNamespace | None = None) -> tuple[TranslationEntry, ...]:
    """Include the exact file binding needed for safe original-format writes."""
    content = document.to_bytes()
    digest = hashlib.sha256(content).hexdigest()
    format_id = "sst.ssu8" if document.magic == b"SSU8" else "sst.ssu9"
    namespace = namespace or SourceNamespace.from_fingerprint(format_id, digest)
    result = []
    for ordinal, record in enumerate(document.records):
        entry = create_sst_entry(SST_Entry.from_record(record, ordinal), namespace)
        result.append(
            replace(entry, metadata=(*entry.metadata, ("io.format", format_id), ("sst.source_sha256", digest)))
        )
    return tuple(result)

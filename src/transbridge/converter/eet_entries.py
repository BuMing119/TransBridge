"""Versioned physical identities for complete binary EET records."""

from collections.abc import Callable
from dataclasses import replace
import hashlib

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.parser.eet_binary_codec import EetDocument, EetRecord

from .translation_entry import TranslationEntry

EET_CODEC_VERSION = 1


def record_signature(record: EetRecord) -> str:
    """Bind every source field except the editable translation and its cache."""
    source = replace(record, translation="", translation_variables=b"")
    return hashlib.sha256(source.to_bytes()).hexdigest()


def document_entries(
    document: EetDocument,
    namespace: SourceNamespace | None = None,
    *,
    source_sha256: str | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> tuple[TranslationEntry, ...]:
    digest = source_sha256 or hashlib.sha256(document.to_bytes()).hexdigest()
    namespace = namespace or SourceNamespace.from_fingerprint("binary.eet", digest)
    entries = []
    for ordinal, record in enumerate(document.records):
        if check_cancelled is not None:
            check_cancelled()
        key = f"eet-v1:{ordinal}"
        entries.append(
            TranslationEntry(
                id=key,
                key=key,
                original=record.original,
                translation=record.translation,
                stage=1 if record.translation else 0,
                context=f"{record.group}:{record.field}",
                editor_id=record.editor_id,
                dsd_type=f"{record.group} {record.field}",
                dsd_index=record.index,
                entry_key=EntryKey(namespace, key),
                metadata=(
                    ("io.format", "binary.eet"),
                    ("eet.codec_version", EET_CODEC_VERSION),
                    ("eet.source_sha256", digest),
                    ("eet.record_index", ordinal),
                    ("eet.record_signature", record_signature(record)),
                    ("eet.form_id", record.form_id),
                    ("eet.editor_id", record.editor_id),
                    ("eet.index", record.index),
                    ("eet.group", record.group),
                    ("eet.field", record.field),
                ),
            )
        )
    return tuple(entries)

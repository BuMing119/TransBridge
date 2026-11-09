"""Lossless native translation fields for binary EET and SST sources."""

from __future__ import annotations

from collections.abc import Sequence

from transbridge.application.io.contracts import FormatId
from transbridge.parser.eet_binary_codec import EetDocument, EetRecord
from transbridge.parser.xt.sst_codec import SstDocument, SstRecord

from .storage_contracts import StoredEntry

SST_FORMATS = frozenset({FormatId.SST_SSU8, FormatId.SST_SSU9})


def extract_native_fields(format_id: FormatId, content: bytes) -> tuple[str, tuple, tuple[tuple, ...]]:
    if format_id is FormatId.BINARY_EET:
        document = EetDocument.from_bytes(content)
        return (
            "eet",
            (document.version, document.game, document.phrases, document.type_dt),
            tuple(
                (
                    record.translation,
                    record.personal,
                    record.status,
                    record.text_id,
                    record.comment,
                    record.original_hash,
                    record.normalized_original,
                    record.normalized_hash,
                    record.original_variables.hex(),
                    record.translation_variables.hex(),
                    record.icon,
                )
                for record in document.records
            ),
        )
    if format_id in SST_FORMATS:
        document = SstDocument.from_bytes(content)
        return (
            "sst",
            (document.magic.decode("ascii"), document.flag, document.masters, document.labels),
            tuple(
                (
                    record.translation,
                    record.record_type,
                    record.field_type,
                    record.index_max,
                    record.edid_hash,
                    record.collab_id,
                    record.params,
                )
                for record in document.records
            ),
        )
    raise ValueError(f"unsupported native translation format {format_id}")


def reconstruct_native(format_id: FormatId, header: tuple, entries: Sequence[StoredEntry]) -> bytes:
    if format_id is FormatId.BINARY_EET:
        version, game, phrases, type_dt = header
        return EetDocument(version, game, tuple(_eet_record(entry) for entry in entries), phrases, type_dt).to_bytes()
    if format_id in SST_FORMATS:
        magic, flag, masters, labels = header
        return SstDocument(
            magic.encode("ascii"), flag, masters, labels, tuple(_sst_record(e) for e in entries)
        ).to_bytes()
    raise ValueError(f"unsupported native translation format {format_id}")


def _eet_record(entry: StoredEntry) -> EetRecord:
    metadata = dict(entry.snapshot.metadata)
    translation, personal, status, text_id, comment, raw_hash, normalized, norm_hash, raw_vars, trans_vars, icon = (
        metadata["eet.native"]
    )
    return EetRecord(
        metadata["eet.group"],
        metadata["eet.form_id"],
        metadata["eet.editor_id"],
        metadata["eet.field"],
        entry.snapshot.original,
        translation,
        personal,
        metadata["eet.index"],
        status,
        text_id,
        comment,
        raw_hash,
        normalized,
        norm_hash,
        bytes.fromhex(raw_vars),
        bytes.fromhex(trans_vars),
        icon,
    )


def _sst_record(entry: StoredEntry) -> SstRecord:
    metadata = dict(entry.snapshot.metadata)
    translation, record_type, field_type, index_max, edid_hash, collab_id, params = metadata["sst.native"]
    return SstRecord(
        metadata["sst.list_index"],
        entry.snapshot.string_id,
        metadata["sst.form_id"],
        record_type,
        field_type,
        metadata["sst.index"],
        index_max,
        edid_hash,
        collab_id,
        params,
        entry.snapshot.original,
        translation,
    )

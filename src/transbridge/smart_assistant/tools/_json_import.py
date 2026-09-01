"""Select JSON contracts explicitly without converting remote IDs into local IDs."""

from __future__ import annotations

import json
from pathlib import Path

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SourceSnapshot, TranslationIoUseCase
from transbridge.application.io.catalog import json_format_evidence
from transbridge.application.io.identity import SourceNamespace
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def parse_json_source(path: str, args: dict):
    source = Path(path)
    content = source.read_bytes()
    payload = json.loads(content.decode("utf-8-sig"))
    records = payload.get("entries", []) if isinstance(payload, dict) else payload
    choice = args.get("format", "auto")
    formats = {"paratranz": FormatId.JSON_PARATRANZ, "transbridge": FormatId.JSON_TRANSBRIDGE, "dsd": FormatId.JSON_DSD}
    if choice != "auto":
        if choice not in formats:
            raise ValueError("format 必须是 auto/paratranz/transbridge/dsd")
        format_id = formats[choice]
    elif isinstance(records, list) and any(isinstance(item, dict) and "entry_key" in item for item in records):
        format_id = FormatId.JSON_TRANSBRIDGE
    else:
        candidates = json_format_evidence(content)
        if len(candidates) != 1:
            raise ValueError("JSON 格式存在歧义；请指定 format=paratranz/transbridge/dsd。")
        format_id = next(iter(candidates))
    descriptor = SourceDescriptor(str(source), source.name, len(content))
    if format_id is FormatId.JSON_PARATRANZ:
        project_id = args.get("project_id")
        if project_id is not None and (
            isinstance(project_id, bool) or not isinstance(project_id, int) or project_id < 1
        ):
            raise ValueError("project_id 必须是正整数")
        snapshot = SourceSnapshot.from_bytes(descriptor, format_id, content)
        namespace = SourceNamespace.from_fingerprint(format_id.value, snapshot.sha256)
        options = (
            ("external_scope", f"project:{project_id}" if project_id is not None else "offline"),
            ("source_namespace", namespace.value),
        )
        parsed = TranslationIoUseCase().parse(
            ParseRequest(
                descriptor,
                RequestContext("smart-assistant-parser"),
                format_id,
                source_namespace=namespace,
                options=options,
            )
        )
        if parsed.outcome is not OperationOutcome.COMPLETED:
            raise ValueError("；".join(item.message for item in parsed.diagnostics))
        return (
            TranslationEntryCollection(entry.to_translation_entry() for entry in parsed.entries),
            parsed.source_snapshot,
            format_id,
            options,
        )
    if not isinstance(records, list):
        raise ValueError("JSON 条目必须是数组")
    if format_id is FormatId.JSON_DSD:
        from transbridge.converter.translation_entry import TranslationEntry

        collection = TranslationEntryCollection(TranslationEntry.from_dsd_dict(item) for item in records)
    else:
        from transbridge.converter.translation_entry import TranslationEntry

        collection = TranslationEntryCollection(TranslationEntry.from_dict(item) for item in records)
    return collection, SourceSnapshot.from_bytes(descriptor, format_id, content), format_id, ()

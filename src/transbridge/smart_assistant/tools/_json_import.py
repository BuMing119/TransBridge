"""Select JSON contracts explicitly without converting remote IDs into local IDs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path

from transbridge.application.contracts import Diagnostic, OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, SourceSnapshot
from transbridge.application.io.catalog import json_format_evidence
from transbridge.application.io.identity import SourceNamespace
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


@dataclass(frozen=True)
class ParsedJsonSource:
    collection: TranslationEntryCollection
    snapshot: SourceSnapshot
    format_id: FormatId
    options: tuple = ()
    outcome: OperationOutcome = OperationOutcome.COMPLETED
    diagnostics: tuple = ()
    raw_entries: tuple = ()
    unmapped: tuple = ()

    def __iter__(self):
        """Preserve the existing four-value source unpacking contract."""
        yield from (self.collection, self.snapshot, self.format_id, self.options)


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
        from transbridge.application.io.paratranz import ParatranzJsonAdapter

        parsed = ParatranzJsonAdapter().parse_content(
            ParseRequest(
                descriptor,
                RequestContext("smart-assistant-parser"),
                format_id,
                source_namespace=namespace,
                options=options,
            ),
            content,
        )
        if parsed.outcome not in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
            raise ValueError("；".join(item.message for item in parsed.diagnostics))
        return ParsedJsonSource(
            TranslationEntryCollection(entry.to_translation_entry() for entry in parsed.entries),
            parsed.source_snapshot,
            format_id,
            options,
            parsed.outcome,
            parsed.diagnostics,
            tuple(records),
        )
    if not isinstance(records, list):
        raise ValueError("JSON 条目必须是数组")
    if format_id is FormatId.JSON_DSD:
        from transbridge.converter.dsd_entries import parse_dsd_entries

        collection = TranslationEntryCollection(parse_dsd_entries(records))
    else:
        from transbridge.converter.translation_entry import TranslationEntry

        grouped, diagnostics, unmapped = defaultdict(list), [], []
        for index, item in enumerate(records):
            try:
                entry = TranslationEntry.from_dict(item)
            except (KeyError, TypeError, ValueError) as exc:
                unmapped.append(item)
                diagnostics.append(
                    Diagnostic("TRANSBRIDGE_ENTRY_INVALID", str(exc), details=(("record_index", index),))
                )
                continue
            grouped[entry.identity].append((index, item, entry))
        selected = []
        for key, candidates in grouped.items():
            if len(candidates) == 1:
                selected.append(candidates[0][2])
                continue
            unmapped.extend(item for _index, item, _entry in candidates)
            diagnostics.append(
                Diagnostic(
                    "TRANSBRIDGE_ENTRY_IDENTITY_DUPLICATED",
                    "同一完整 EntryKey 存在多份候选，未选择其中任何一份。",
                    details=(("entry_key", key.to_dict()), ("record_indexes", [item[0] for item in candidates])),
                )
            )
        return ParsedJsonSource(
            TranslationEntryCollection(selected),
            SourceSnapshot.from_bytes(descriptor, format_id, content),
            format_id,
            outcome=OperationOutcome.PARTIAL if diagnostics else OperationOutcome.COMPLETED,
            diagnostics=tuple(diagnostics),
            raw_entries=tuple(records),
            unmapped=tuple(unmapped),
        )
    return ParsedJsonSource(collection, SourceSnapshot.from_bytes(descriptor, format_id, content), format_id)

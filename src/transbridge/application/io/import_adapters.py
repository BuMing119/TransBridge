# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read-only source adapters for existing JSON translation readers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import struct

from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.converter.translation_entry import TranslationEntry

from .catalog import FORMAT_POLICY_CEILINGS, FormatCatalog
from .contracts import (
    CapabilityLevel,
    FormatCapability,
    FormatId,
    FormatProbe,
    ParseRequest,
    ParseResult,
    ParseStats,
    ProbeRequest,
    ProbeStatus,
    SourceSnapshot,
    WriteRequest,
)
from .identity import SourceNamespace
from .sst_adapter import SstAdapter as SstImportAdapter

__all__ = ["JsonImportAdapter", "SstImportAdapter"]


class _ReadOnlyImportAdapter:
    adapter_version = "1.0"

    def __init__(self, format_id: FormatId) -> None:
        self.format_id = format_id
        self.adapter_id = f"transbridge.io.import.{format_id.value}"

    def capabilities(self) -> FormatCapability:
        supported = CapabilityLevel.SUPPORTED
        return FormatCapability(read=supported, gui=supported, agent=supported, cancel=supported).bounded_by(
            FORMAT_POLICY_CEILINGS[self.format_id]
        )

    def probe(self, request: ProbeRequest) -> FormatProbe:
        probe = FormatCatalog().resolve(replace(request, format_hint=None))
        if probe.status is ProbeStatus.EXACT and probe.candidates == (self.format_id,):
            return probe
        return FormatProbe(ProbeStatus.UNSUPPORTED)

    def validate_write(self, request: WriteRequest) -> OperationResult[None]:
        return OperationResult(
            OperationOutcome.FAILED,
            diagnostics=(Diagnostic("FORMAT_WRITE_UNSUPPORTED", f"{self.format_id.value} 当前仅支持读取。"),),
            counts=OperationCounts(failed=1),
            run_id=request.context.run_id,
        )

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        return self.validate_write(request)

    def parse(self, request: ParseRequest) -> ParseResult:
        def cancelled():
            return ParseResult(
                OperationOutcome.CANCELLED, self.format_id, request.source, stats=ParseStats(cancelled=1)
            )

        if request.cancellation is not None and request.cancellation.is_cancelled:
            return cancelled()
        try:
            if request.format_hint not in (None, self.format_id):
                raise ValueError("所选格式与解析器不一致。")
            content = Path(request.source.uri).read_bytes()
            probe = FormatCatalog().resolve(ProbeRequest(request.source, content))
            if probe.status is ProbeStatus.EXACT and self.format_id not in probe.candidates:
                raise ValueError("所选格式与文件内容不一致。")
            if probe.status is ProbeStatus.UNSUPPORTED:
                raise ValueError("无法识别文件内容。")
            entries = self._entries(request, content)
            snapshot = SourceSnapshot.from_bytes(request.source, self.format_id, content)
            fallback = SourceNamespace.from_fingerprint(self.format_id.value, snapshot.sha256)
            entries = tuple(
                replace(
                    entry,
                    entry_key=replace(
                        entry.identity,
                        namespace=request.source_namespace
                        or (
                            fallback
                            if entry.identity.namespace == SourceNamespace.legacy()
                            else entry.identity.namespace
                        ),
                    ),
                )
                for entry in entries
            )
            if len({entry.identity for entry in entries}) != len(entries):
                raise ValueError("来源包含重复词条身份，未选择或覆盖任何候选。")
            namespaces = {entry.identity.namespace for entry in entries}
            if len(namespaces) > 1:
                raise ValueError("该文件包含多个来源身份，请按来源分别导入。")
            namespace = next(iter(namespaces), request.source_namespace or fallback)
            snapshot = replace(snapshot, metadata=(("source_namespace", namespace.value),))
        except (OSError, ValueError, TypeError, KeyError, struct.error) as exc:
            return ParseResult(
                OperationOutcome.FAILED,
                self.format_id,
                request.source,
                diagnostics=(Diagnostic("IMPORT_SOURCE_INVALID", f"{self.format_id.value} 无法导入：{exc}"),),
                stats=ParseStats(failed=1),
            )
        if request.cancellation is not None and request.cancellation.is_cancelled:
            return cancelled()
        return ParseResult.completed(
            self.format_id,
            request.source,
            snapshot,
            entries,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )

    def _entries(self, request: ParseRequest, content: bytes) -> tuple[TranslationEntry, ...]:
        raise NotImplementedError


class JsonImportAdapter(_ReadOnlyImportAdapter):
    def __init__(self, format_id: FormatId) -> None:
        if format_id not in {FormatId.JSON_DSD, FormatId.JSON_TRANSBRIDGE}:
            raise ValueError("JSON import adapter requires DSD or TransBridge format")
        super().__init__(format_id)

    def _entries(self, request: ParseRequest, content: bytes) -> tuple[TranslationEntry, ...]:
        from .json_source import parse_json_source

        parsed = parse_json_source(
            request.source.uri, {"format": self.format_id.value.removeprefix("json.")}, content=content
        )
        if parsed.outcome is not OperationOutcome.COMPLETED:
            raise ValueError("；".join(item.message for item in parsed.diagnostics))
        if len(parsed.collection) != len(parsed.raw_entries):
            raise ValueError("来源包含无法唯一定位的重复条目，请先消除冲突。")
        return tuple(parsed.collection)

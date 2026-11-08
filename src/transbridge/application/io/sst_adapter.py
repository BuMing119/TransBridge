"""Snapshot-bound SSU8/SSU9 imports and original-format translation writes."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile

from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.converter.sst_entries import SST_CODEC_VERSION, document_entries, record_signature
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.xt.sst_codec import SstDocument

from .catalog import FORMAT_POLICY_CEILINGS
from .contracts import (
    FormatCapability,
    FormatId,
    FormatProbe,
    ParseRequest,
    ParseResult,
    ParseStats,
    ProbeConfidence,
    ProbeEvidence,
    ProbeEvidenceKind,
    ProbeRequest,
    ProbeStatus,
    SourceSnapshot,
    WriteRequest,
)
from .identity import EntryKey, SourceNamespace
from .sst_export import SstExportCancelled, SstExportError
from .stage_policy import DEFAULT_STAGE_POLICY, StageOperation

_MAGIC = {FormatId.SST_SSU8: b"SSU8", FormatId.SST_SSU9: b"SSU9"}
_VERSION = SST_CODEC_VERSION


class _SstWriteError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SstAdapter:
    """One physical SST record owns one ordinal identity, including duplicates."""

    adapter_version = "2.0"

    def __init__(self, format_id: FormatId) -> None:
        if format_id not in _MAGIC:
            raise ValueError("SST adapter requires SSU8 or SSU9 format")
        self.format_id = format_id
        self.adapter_id = f"transbridge.io.{format_id.value}"

    def capabilities(self) -> FormatCapability:
        return FORMAT_POLICY_CEILINGS[self.format_id]

    def probe(self, request: ProbeRequest) -> FormatProbe:
        magic = _MAGIC[self.format_id]
        if not request.content.startswith(magic):
            return FormatProbe(ProbeStatus.UNSUPPORTED)
        return FormatProbe(
            ProbeStatus.EXACT,
            (self.format_id,),
            (ProbeEvidence(self.format_id, ProbeEvidenceKind.MAGIC, magic.decode(), ProbeConfidence.EXACT),),
        )

    def parse(self, request: ParseRequest) -> ParseResult:
        if _cancelled(request.cancellation):
            return self._cancelled_parse(request)
        try:
            content = _local_path(request.source.uri).read_bytes()
        except (OSError, ValueError) as exc:
            return self._failed_parse(request, exc)
        return self.parse_bytes(request, content)

    def parse_bytes(self, request: ParseRequest, content: bytes) -> ParseResult:
        if _cancelled(request.cancellation):
            return self._cancelled_parse(request)
        try:
            if request.format_hint not in (None, self.format_id):
                raise ValueError("所选 SST 格式与解析器不一致。")
            document = SstDocument.from_bytes(content)
            if document.magic != _MAGIC[self.format_id]:
                raise ValueError("所选 SST 格式与文件签名不一致。")
            snapshot = SourceSnapshot.from_bytes(request.source, self.format_id, content)
            namespace = request.source_namespace or SourceNamespace.from_fingerprint(
                self.format_id.value, snapshot.sha256
            )
            entries = document_entries(document, namespace)
            if _cancelled(request.cancellation):
                return self._cancelled_parse(request)
            snapshot = replace(
                snapshot,
                metadata=(("source_namespace", namespace.value), ("sst_codec_version", _VERSION)),
            )
        except (TypeError, ValueError) as exc:
            return self._failed_parse(request, exc)
        return ParseResult.completed(
            self.format_id,
            request.source,
            snapshot,
            tuple(entries),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )

    def validate_write(self, request: WriteRequest) -> OperationResult[None]:
        if _cancelled(request.cancellation):
            return OperationResult.cancelled(run_id=request.context.run_id)
        try:
            self.render_document(request)
        except SstExportCancelled:
            return OperationResult.cancelled(run_id=request.context.run_id)
        except (OSError, TypeError, ValueError) as exc:
            return _failed_write(request, exc)
        if _cancelled(request.cancellation):
            return OperationResult.cancelled(run_id=request.context.run_id)
        return OperationResult.completed(run_id=request.context.run_id)

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        if _cancelled(request.cancellation):
            return OperationResult.cancelled(run_id=request.context.run_id)
        try:
            document = self.render_document(request)
            content = document.to_bytes()
            if SstDocument.from_bytes(content) != document:
                raise ValueError("SST 序列化后完整字段验证失败。")
            if _cancelled(request.cancellation):
                return OperationResult.cancelled(run_id=request.context.run_id)
            target = _local_path(request.target.uri)
            target.parent.mkdir(parents=True, exist_ok=True)
            if not _write_atomic(target, content, request.cancellation):
                return OperationResult.cancelled(run_id=request.context.run_id)
        except SstExportCancelled:
            return OperationResult.cancelled(run_id=request.context.run_id)
        except (OSError, TypeError, ValueError) as exc:
            return _failed_write(request, exc)
        artifact = str(target)
        return OperationResult.completed(
            (artifact,),
            counts=OperationCounts(succeeded=len(request.entries)),
            artifact_refs=(artifact,),
            run_id=request.context.run_id,
        )

    def render_document(self, request: WriteRequest) -> SstDocument:
        """Validate identities and produce the complete expected publication."""
        if request.format_id != self.format_id:
            raise _SstWriteError("FORMAT_MISMATCH", "SST 输出格式与适配器不一致。")
        if Path(request.target.display_name or request.target.uri).suffix.lower() != ".sst":
            raise _SstWriteError("SST_TARGET_EXTENSION", "SST 输出文件必须使用 .sst 扩展名。")
        snapshot = request.source_snapshot
        if snapshot is not None and snapshot.format_id is FormatId.PLUGIN_SSE:
            from .sst_export import build_sst_from_plugin

            if self.format_id is not FormatId.SST_SSU9:
                raise _SstWriteError("FORMAT_MISMATCH", "插件导出 SST 只支持 SSU9。")
            target_path = os.path.normcase(str(_local_path(request.target.uri).resolve()))
            source_path = os.path.normcase(str(_local_path(snapshot.source.uri).resolve()))
            if target_path == source_path:
                raise _SstWriteError(
                    "SST_TARGET_IS_SOURCE_PLUGIN", "SST 导出不得覆盖原插件文件，请选择新的 .sst 文件。"
                )
            return build_sst_from_plugin(request)
        if snapshot is None or snapshot.content is None or snapshot.format_id != self.format_id:
            raise _SstWriteError("SOURCE_SNAPSHOT_REQUIRED", "SST 写回需要完整的原格式来源快照。")
        metadata = dict(snapshot.metadata)
        namespace = metadata.get("source_namespace")
        if metadata.get("sst_codec_version") != _VERSION or not isinstance(namespace, str):
            raise _SstWriteError("SST_REIMPORT_REQUIRED", "此 SST 来源使用旧解析身份，请重新导入或迁移后再写回。")
        if hashlib.sha256(snapshot.content).hexdigest() != snapshot.sha256:
            raise _SstWriteError("SOURCE_FINGERPRINT_CONFLICT", "SST 来源快照校验失败。")
        if dict(request.options).get("source_authority") != "hydration-v2":
            try:
                current = _local_path(snapshot.source.uri).read_bytes()
            except OSError as exc:
                raise _SstWriteError("SOURCE_SNAPSHOT_UNAVAILABLE", f"无法重新校验 SST 来源：{exc}") from exc
            if hashlib.sha256(current).hexdigest() != snapshot.sha256:
                raise _SstWriteError("SOURCE_FINGERPRINT_CONFLICT", "SST 原文件已变化，请重新导入后写回。")
        document = SstDocument.from_bytes(snapshot.content)
        if document.magic != _MAGIC[self.format_id]:
            raise _SstWriteError("FORMAT_MISMATCH", "SST 快照签名与输出格式不一致。")
        records = list(document.records)
        consumed = set()
        policy = request.stage_policy or DEFAULT_STAGE_POLICY
        for entry in request.entries:
            ordinal = _validate_entry(entry, snapshot, namespace, document)
            if ordinal in consumed:
                raise _SstWriteError("SOURCE_LOCATOR_CONFLICT", "写回包含重复 SST 物理记录身份。")
            consumed.add(ordinal)
            decision = policy.evaluate(entry.stage, entry.translation, StageOperation.PUBLISH, original=entry.original)
            if decision.blocks_publish:
                diagnostic = decision.diagnostic or Diagnostic("STAGE_PUBLISH_BLOCKED", "词条状态不允许写回。")
                raise _SstWriteError(diagnostic.code, diagnostic.message)
            if not decision.preserve_source:
                # SST is bilingual exchange data. Preserve explicit empty/draft
                # translations and all native status bits, just like XML writes.
                records[ordinal] = replace(records[ordinal], translation=entry.translation)
        return replace(document, records=tuple(records))

    def _cancelled_parse(self, request: ParseRequest) -> ParseResult:
        return ParseResult(OperationOutcome.CANCELLED, self.format_id, request.source, stats=ParseStats(cancelled=1))

    def _failed_parse(self, request: ParseRequest, error: Exception) -> ParseResult:
        return ParseResult(
            OperationOutcome.FAILED,
            self.format_id,
            request.source,
            diagnostics=(Diagnostic("SST_PARSE_FAILED", f"SST 文件无法完整解析：{error}"),),
            stats=ParseStats(failed=1),
        )


def _validate_entry(entry: object, snapshot: SourceSnapshot, namespace: str, document: SstDocument) -> int:
    if not isinstance(entry, TranslationEntry):
        raise _SstWriteError("ENTRY_INVALID", "SST 写回词条类型无效。")
    metadata = dict(entry.metadata)
    if metadata.get("sst.codec_version") != _VERSION:
        raise _SstWriteError("SST_REIMPORT_REQUIRED", "词条缺少新版 SST 物理记录身份，请重新导入或迁移。")
    ordinal = metadata.get("sst.record_index")
    if (
        isinstance(ordinal, bool)
        or not isinstance(ordinal, int)
        or not 0 <= ordinal < len(document.records)
        or entry.identity != EntryKey(SourceNamespace(namespace), f"sst-v2:{ordinal}")
        or metadata.get("io.format") != snapshot.format_id.value
        or metadata.get("sst.source_sha256") != snapshot.sha256
    ):
        raise _SstWriteError("SST_IDENTITY_CONFLICT", "SST 词条身份与来源快照不一致，未写入任何记录。")
    record = document.records[ordinal]
    if metadata.get("sst.record_signature") != record_signature(record) or entry.original != record.original:
        raise _SstWriteError("SST_IDENTITY_CONFLICT", "SST 词条原文或记录签名与来源快照不一致。")
    if not isinstance(entry.translation, str):
        raise _SstWriteError("ENTRY_TRANSLATION_INVALID", "SST 译文必须是文本。")
    return ordinal


def _failed_write(request: WriteRequest, error: Exception) -> OperationResult:
    code = error.code if isinstance(error, (_SstWriteError, SstExportError)) else "SST_WRITE_FAILED"
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic(code, f"SST 写回失败：{error}"),),
        counts=OperationCounts(failed=1),
        run_id=request.context.run_id,
    )


def _cancelled(token) -> bool:
    if token is None:
        return False
    state = token.is_cancelled
    return bool(state() if callable(state) else state)


def _write_atomic(target: Path, content: bytes, cancellation) -> bool:
    # PublishCoordinator owns the final product replacement. This also keeps
    # direct adapter writes from exposing a partially written file.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if _cancelled(cancellation):
            return False
        os.replace(temporary, target)
        temporary = None
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _local_path(uri: str) -> Path:
    if "://" in uri:
        raise ValueError("SST 文件入口只接受本地路径。")
    return Path(uri)

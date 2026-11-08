"""Snapshot-bound binary EET v3 reads and original-format translation writes."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import tempfile

from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.converter.eet_entries import EET_CODEC_VERSION, document_entries, record_signature
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.eet_binary_codec import EetDocument
from transbridge.parser.eet_number_cache import build_number_cache

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
from .stage_policy import DEFAULT_STAGE_POLICY, StageOperation


class EetCancelled(Exception):
    """Cancellation while building a complete binary EET result."""


class EetWriteError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class EetAdapter:
    format_id = FormatId.BINARY_EET
    adapter_id = "transbridge.io.binary.eet"
    adapter_version = "1.0"

    def capabilities(self) -> FormatCapability:
        return FORMAT_POLICY_CEILINGS[self.format_id]

    def probe(self, request: ProbeRequest) -> FormatProbe:
        if not request.content.startswith(b"EET_"):
            return FormatProbe(ProbeStatus.UNSUPPORTED)
        return FormatProbe(
            ProbeStatus.EXACT,
            (self.format_id,),
            (ProbeEvidence(self.format_id, ProbeEvidenceKind.MAGIC, "EET_", ProbeConfidence.EXACT),),
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
        try:
            _check_cancelled(request.cancellation)
            if request.format_hint not in (None, self.format_id):
                raise ValueError("所选格式与二进制 EET 解析器不一致。")
            document = EetDocument.from_bytes(content)
            snapshot = SourceSnapshot.from_bytes(request.source, self.format_id, content)
            namespace = request.source_namespace or SourceNamespace.from_fingerprint(
                self.format_id.value, snapshot.sha256
            )
            entries = document_entries(
                document,
                namespace,
                source_sha256=snapshot.sha256,
                check_cancelled=lambda: _check_cancelled(request.cancellation),
            )
            _check_cancelled(request.cancellation)
            snapshot = replace(
                snapshot,
                metadata=(("source_namespace", namespace.value), ("eet_codec_version", EET_CODEC_VERSION)),
            )
        except EetCancelled:
            return self._cancelled_parse(request)
        except (TypeError, ValueError) as exc:
            return self._failed_parse(request, exc)
        return ParseResult.completed(
            self.format_id,
            request.source,
            snapshot,
            entries,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )

    def validate_write(self, request: WriteRequest) -> OperationResult[None]:
        try:
            self.render_document(request)
        except EetCancelled:
            return OperationResult.cancelled(run_id=request.context.run_id)
        except (OSError, TypeError, ValueError) as exc:
            return _failed_write(request, exc)
        return OperationResult.completed(run_id=request.context.run_id)

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        try:
            document = self.render_document(request)
            content = document.to_bytes()
            if EetDocument.from_bytes(content) != document:
                raise ValueError("EET 序列化后完整字段验证失败。")
            _check_cancelled(request.cancellation)
            target = _local_path(request.target.uri)
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(target, content, request.cancellation)
        except EetCancelled:
            return OperationResult.cancelled(run_id=request.context.run_id)
        except (OSError, TypeError, ValueError) as exc:
            return _failed_write(request, exc)
        return OperationResult.completed(
            (str(target),),
            counts=OperationCounts(succeeded=len(request.entries)),
            artifact_refs=(str(target),),
            run_id=request.context.run_id,
        )

    def render_document(self, request: WriteRequest) -> EetDocument:
        """Retain all native fields and rebuild only changed translation caches."""
        _check_cancelled(request.cancellation)
        if request.format_id is not self.format_id:
            raise EetWriteError("FORMAT_MISMATCH", "EET 输出格式与适配器不一致。")
        if Path(request.target.display_name or request.target.uri).suffix.lower() != ".eet":
            raise EetWriteError("EET_TARGET_EXTENSION", "二进制 EET 输出必须使用 .eet 扩展名。")
        snapshot = request.source_snapshot
        if snapshot is None or snapshot.content is None or snapshot.format_id is not self.format_id:
            raise EetWriteError("SOURCE_SNAPSHOT_REQUIRED", "EET 写回需要完整的二进制来源快照。")
        metadata = dict(snapshot.metadata)
        namespace = metadata.get("source_namespace")
        if not _current_version(metadata.get("eet_codec_version")) or not isinstance(namespace, str):
            raise EetWriteError("EET_REIMPORT_REQUIRED", "此 EET 来源缺少物理记录身份，请重新导入或迁移。")
        if hashlib.sha256(snapshot.content).hexdigest() != snapshot.sha256:
            raise EetWriteError("SOURCE_FINGERPRINT_CONFLICT", "EET 来源快照校验失败。")
        if dict(request.options).get("source_authority") != "hydration-v2":
            try:
                current = _local_path(snapshot.source.uri).read_bytes()
            except OSError as exc:
                raise EetWriteError("SOURCE_SNAPSHOT_UNAVAILABLE", f"无法重新校验 EET 来源：{exc}") from exc
            if hashlib.sha256(current).hexdigest() != snapshot.sha256:
                raise EetWriteError("SOURCE_FINGERPRINT_CONFLICT", "EET 原文件已变化，请重新导入后写回。")
        document = EetDocument.from_bytes(snapshot.content)
        records = list(document.records)
        consumed = set()
        policy = request.stage_policy or DEFAULT_STAGE_POLICY
        for entry in request.entries:
            _check_cancelled(request.cancellation)
            ordinal = _validate_entry(entry, snapshot, namespace, document)
            if ordinal in consumed:
                raise EetWriteError("SOURCE_LOCATOR_CONFLICT", "写回包含重复 EET 物理记录身份。")
            consumed.add(ordinal)
            decision = policy.evaluate(entry.stage, entry.translation, StageOperation.PUBLISH, original=entry.original)
            if decision.blocks_publish:
                diagnostic = decision.diagnostic or Diagnostic("STAGE_PUBLISH_BLOCKED", "词条状态不允许写回。")
                raise EetWriteError(diagnostic.code, diagnostic.message)
            record = records[ordinal]
            if not decision.preserve_source and record.translation != entry.translation:
                cache = build_number_cache(entry.translation, group=record.group, field=record.field)
                records[ordinal] = replace(
                    record, translation=entry.translation, translation_variables=cache.to_payload() if cache else b""
                )
        _check_cancelled(request.cancellation)
        return replace(document, records=tuple(records))

    def _cancelled_parse(self, request: ParseRequest) -> ParseResult:
        return ParseResult(OperationOutcome.CANCELLED, self.format_id, request.source, stats=ParseStats(cancelled=1))

    def _failed_parse(self, request: ParseRequest, error: Exception) -> ParseResult:
        return ParseResult(
            OperationOutcome.FAILED,
            self.format_id,
            request.source,
            diagnostics=(Diagnostic("EET_PARSE_FAILED", f"二进制 EET 文件无法完整解析：{error}"),),
            stats=ParseStats(failed=1),
        )


def _current_version(value: object) -> bool:
    return type(value) is int and value == EET_CODEC_VERSION


def _validate_entry(entry: object, snapshot: SourceSnapshot, namespace: str, document: EetDocument) -> int:
    if not isinstance(entry, TranslationEntry):
        raise EetWriteError("ENTRY_INVALID", "EET 写回词条类型无效。")
    metadata = dict(entry.metadata)
    if not _current_version(metadata.get("eet.codec_version")):
        raise EetWriteError("EET_REIMPORT_REQUIRED", "词条缺少 EET 物理记录身份，请重新导入或迁移。")
    ordinal = metadata.get("eet.record_index")
    if (
        type(ordinal) is not int
        or not 0 <= ordinal < len(document.records)
        or entry.identity != EntryKey(SourceNamespace(namespace), f"eet-v1:{ordinal}")
        or metadata.get("io.format") != snapshot.format_id.value
        or metadata.get("eet.source_sha256") != snapshot.sha256
    ):
        raise EetWriteError("EET_IDENTITY_CONFLICT", "EET 词条身份与来源快照不一致。")
    record = document.records[ordinal]
    if metadata.get("eet.record_signature") != record_signature(record) or entry.original != record.original:
        raise EetWriteError("EET_IDENTITY_CONFLICT", "EET 词条原文或记录签名与来源快照不一致。")
    if not isinstance(entry.translation, str):
        raise EetWriteError("ENTRY_TRANSLATION_INVALID", "EET 译文必须是文本。")
    return ordinal


def _failed_write(request: WriteRequest, error: Exception) -> OperationResult:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic(error.code if isinstance(error, EetWriteError) else "EET_WRITE_FAILED", str(error)),),
        counts=OperationCounts(failed=1),
        run_id=request.context.run_id,
    )


def _cancelled(token) -> bool:
    if token is None:
        return False
    state = token.is_cancelled
    return bool(state() if callable(state) else state)


def _check_cancelled(token) -> None:
    if _cancelled(token):
        raise EetCancelled()


def _write_atomic(target: Path, content: bytes, cancellation) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
        ) as out:
            temporary = Path(out.name)
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        _check_cancelled(cancellation)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _local_path(uri: str) -> Path:
    if "://" in uri:
        raise ValueError("EET 文件入口只接受本地路径。")
    return Path(uri)

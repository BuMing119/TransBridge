# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Fidelity-first adapters for Skyrim STRINGS, DLSTRINGS, and ILSTRINGS."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import hashlib
from pathlib import Path

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.config.text_encoding import LEGACY_READ_POLICY, normalize_text_encoding
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.localized_string_records import (
    LocalizedStringRecord as LocalizedStringRecord,
    encode_chunk as _encode_chunk,
    parse_records as _parse_records,
    render as _render,
)

from .contracts import (
    CapabilityLevel,
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
from .stage_policy import DEFAULT_STAGE_POLICY, StageOperation, StagePolicyPort
from .text_source_options import request_text_read_options, source_text_fallback_diagnostics, source_text_metadata

_VARIANTS: dict[FormatId, tuple[str, bool]] = {
    FormatId.STRINGS: (".strings", False),
    FormatId.DLSTRINGS: (".dlstrings", True),
    FormatId.ILSTRINGS: (".ilstrings", True),
}


class LocalizedStringsAdapter:
    adapter_version = f"2.0-stage-{DEFAULT_STAGE_POLICY.version}"

    def __init__(self, format_id: FormatId) -> None:
        if format_id not in _VARIANTS:
            raise ValueError(f"Unsupported localized strings format: {format_id}")
        self.format_id = format_id
        self.adapter_id = f"transbridge.io.{format_id.value}"
        self._suffix, self._length_prefixed = _VARIANTS[format_id]

    def capabilities(self) -> FormatCapability:
        supported = CapabilityLevel.SUPPORTED
        unavailable = CapabilityLevel.UNAVAILABLE
        return FormatCapability(
            read=supported,
            write=supported,
            round_trip=supported,
            localized=supported,
            streaming=unavailable,
            cancel=supported,
            fidelity=supported,
            gui=supported,
            agent=supported,
            mcp=unavailable,
            publish=unavailable,
        )

    def probe(self, request: ProbeRequest) -> FormatProbe:
        if request.source.suffix != self._suffix:
            return FormatProbe(ProbeStatus.UNSUPPORTED)
        try:
            _parse_records(request.content, self._length_prefixed, None)
        except ValueError:
            return FormatProbe(ProbeStatus.UNSUPPORTED)
        return FormatProbe(
            ProbeStatus.EXACT,
            (self.format_id,),
            (
                ProbeEvidence(
                    self.format_id,
                    ProbeEvidenceKind.STRUCTURE,
                    f"localized-strings-v2:{self._suffix}",
                    ProbeConfidence.EXACT,
                ),
            ),
        )

    def parse(self, request: ParseRequest) -> ParseResult:
        if _cancelled(request.cancellation):
            return self.parse_bytes(request, b"")
        try:
            content = _local_path(request.source.uri).read_bytes()
        except (OSError, ValueError) as exc:
            return _failed_parse(request, self, exc)
        return self.parse_bytes(request, content)

    def parse_bytes(self, request: ParseRequest, content: bytes, *, legacy_linked: bool = False) -> ParseResult:
        """Parse immutable source bytes without reopening the source descriptor URI."""
        if _cancelled(request.cancellation):
            return ParseResult(
                OperationOutcome.CANCELLED,
                self.format_id,
                request.source,
                diagnostics=(Diagnostic("PARSE_CANCELLED", "The localized strings parse was cancelled."),),
                stats=ParseStats(cancelled=1),
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                capability=self.capabilities(),
            )
        try:
            read_options = request_text_read_options(request.options)
            records = _parse_records(
                content, self._length_prefixed, read_options=read_options, legacy_linked=legacy_linked
            )
        except ValueError as exc:
            return _failed_parse(request, self, exc)

        duplicate_diagnostics = _duplicate_id_diagnostics(records)
        if duplicate_diagnostics:
            return ParseResult(
                OperationOutcome.FAILED,
                self.format_id,
                request.source,
                diagnostics=duplicate_diagnostics,
                stats=ParseStats(failed=len(duplicate_diagnostics)),
                adapter_id=self.adapter_id,
                adapter_version=self.adapter_version,
                capability=self.capabilities(),
            )

        namespace = request.source_namespace or _namespace(self.format_id, records)
        entries = tuple(
            TranslationEntry(
                id=str(record.string_id),
                key=_local_key(record.string_id),
                original=record.text,
                translation="",
                stage=0,
                context=self.format_id.value,
                string_id=record.string_id,
                entry_key=EntryKey(namespace, _local_key(record.string_id)),
                metadata=(
                    ("io.format", self.format_id.value),
                    ("strings.directory_index", record.directory_index),
                    ("strings.encoding", record.encoding),
                ),
            )
            for record in records
        )
        snapshot = SourceSnapshot.from_bytes(
            request.source,
            self.format_id,
            content,
            encoding=None if read_options.text_read_policy == LEGACY_READ_POLICY else read_options.input_encoding,
            metadata=(
                ("source_namespace", namespace.value),
                ("stage_policy_version", DEFAULT_STAGE_POLICY.version),
                ("strings.variant", self._suffix),
                ("strings.order", tuple(record.string_id for record in records)),
                ("strings.records", records),
                *source_text_metadata(read_options),
            ),
        )
        return ParseResult.completed(
            self.format_id,
            request.source,
            snapshot,
            entries,
            diagnostics=source_text_fallback_diagnostics(sum(record.used_fallback(read_options) for record in records)),
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
            capability=self.capabilities(),
        )

    def validate_write(self, request: WriteRequest) -> OperationResult[None]:
        if request.format_id is not self.format_id:
            return _failed_write(
                (Diagnostic("FORMAT_MISMATCH", f"{self.adapter_id} cannot write {request.format_id.value}."),),
                request.context.run_id,
            )
        if _cancelled(request.cancellation):
            return OperationResult.cancelled(
                Diagnostic("WRITE_CANCELLED", "The localized strings write was cancelled."),
                run_id=request.context.run_id,
            )
        snapshot = request.source_snapshot
        if snapshot is None or snapshot.format_id is not self.format_id or snapshot.content is None:
            return _failed_write(
                (
                    Diagnostic(
                        "SOURCE_SNAPSHOT_REQUIRED", "Localized strings writes require a complete source snapshot."
                    ),
                ),
                request.context.run_id,
            )
        # SourceSnapshot already verifies its immutable bytes and digest. Only
        # path-backed writes depend on the live source remaining unchanged.
        if dict(request.options).get("source_authority") != "hydration-v2":
            try:
                current = _local_path(snapshot.source.uri).read_bytes()
            except (OSError, ValueError) as exc:
                return _failed_write(
                    (
                        Diagnostic(
                            "SOURCE_SNAPSHOT_UNAVAILABLE",
                            f"The source snapshot cannot be reopened ({type(exc).__name__}).",
                        ),
                    ),
                    request.context.run_id,
                )
            actual_hash = hashlib.sha256(current).hexdigest()
            if actual_hash != snapshot.sha256:
                return _failed_write(
                    (
                        Diagnostic(
                            "SOURCE_FINGERPRINT_CONFLICT",
                            "The localized strings source changed after parsing; reparse before writing.",
                            details=(("expected_sha256", snapshot.sha256), ("actual_sha256", actual_hash)),
                        ),
                    ),
                    request.context.run_id,
                )

        policy = request.stage_policy or DEFAULT_STAGE_POLICY
        stage_diagnostics: list[Diagnostic] = []
        for index, entry in enumerate(request.entries):
            diagnostic = _stage_diagnostic(entry, index, policy)
            if diagnostic is not None:
                stage_diagnostics.append(diagnostic)
        diagnostics = tuple(stage_diagnostics)
        if diagnostics:
            return _failed_write(diagnostics, request.context.run_id)
        return OperationResult.completed(run_id=request.context.run_id)

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        validation = self.validate_write(request)
        if validation.outcome is not OperationOutcome.COMPLETED:
            return OperationResult(
                validation.outcome,
                diagnostics=validation.diagnostics,
                counts=validation.counts,
                run_id=validation.run_id,
            )
        snapshot = request.source_snapshot
        if snapshot is None or snapshot.content is None:
            return _failed_write(
                (Diagnostic("SOURCE_SNAPSHOT_REQUIRED", "Localized strings writes require source bytes."),),
                request.context.run_id,
            )
        try:
            output_encoding = normalize_text_encoding(dict(request.options).get("output_encoding", "utf-8"))
            chunks: list[tuple[int, bytes]] = []
            expected: list[tuple[int, str]] = []
            lossy_stages: set[int] = set()
            for record, text, stage in strings_write_plan(request):
                chunks.append((record.string_id, _encode_record(record, text, output_encoding, self._length_prefixed)))
                expected.append((record.string_id, text))
                if stage:
                    lossy_stages.add(stage)
            rendered = _render(chunks)
            reparsed = _parse_records(rendered, self._length_prefixed, output_encoding)
            if [(record.string_id, record.text) for record in reparsed] != expected:
                raise ValueError("localized strings fidelity check changed IDs, order, or text")
            if _cancelled(request.cancellation):
                return OperationResult.cancelled(run_id=request.context.run_id)
            target = _local_path(request.target.uri)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(rendered)
        except _MissingStringIds as exc:
            return _failed_write(
                (
                    Diagnostic(
                        "SOURCE_LOCATOR_NOT_FOUND",
                        "One or more localized string IDs are absent from the source snapshot.",
                        details=(("string_ids", exc.string_ids),),
                    ),
                ),
                request.context.run_id,
            )
        except (OSError, TypeError, ValueError) as exc:
            return _failed_write(
                (
                    Diagnostic(
                        "LOCALIZED_STRINGS_WRITE_FAILED",
                        f"The localized strings write failed: {request.target.uri} ({exc}).",
                    ),
                ),
                request.context.run_id,
            )

        artifact = str(target)
        warnings = ()
        if lossy_stages:
            warnings = (
                Diagnostic(
                    "STAGE_MAPPING_LOSSY",
                    "The localized strings format stores text but cannot encode exact stage values.",
                    DiagnosticSeverity.WARNING,
                    details=(("stages", tuple(sorted(lossy_stages))),),
                ),
            )
        return OperationResult.completed(
            (artifact,),
            diagnostics=warnings,
            counts=OperationCounts(succeeded=len(expected)),
            artifact_refs=(artifact,),
            run_id=request.context.run_id,
        )


class _MissingStringIds(ValueError):
    def __init__(self, string_ids: tuple[int, ...]) -> None:
        self.string_ids = string_ids
        super().__init__("localized string IDs are absent from the source snapshot")


def strings_write_plan(request: WriteRequest) -> Iterator[tuple[LocalizedStringRecord, str, int]]:
    """Apply the stage policy in physical ID order, including unreferenced text."""
    snapshot = request.source_snapshot
    if snapshot is None:
        raise ValueError("localized strings writes require a source snapshot")
    records = _snapshot_records(snapshot)
    entries = _index_write_entries(request.entries, _snapshot_namespace(snapshot))
    policy = request.stage_policy or DEFAULT_STAGE_POLICY
    for record in records:
        entry = entries.pop(record.string_id, None)
        if entry is None:
            yield record, record.text, 0
            continue
        decision = policy.evaluate(
            getattr(entry, "stage", None),
            getattr(entry, "translation", ""),
            StageOperation.PUBLISH,
            original=getattr(entry, "original", ""),
        )
        if decision.publish_text is None:
            raise ValueError("blocking stage decision passed validation")
        text = record.text if decision.preserve_source else decision.publish_text
        stage = decision.stage.value if decision.stage is not None and not decision.preserve_source else 0
        yield record, text, stage
    if entries:
        raise _MissingStringIds(tuple(sorted(entries)))


def _encode_record(record: LocalizedStringRecord, text: str, encoding: str, length_prefixed: bool) -> bytes:
    if text == record.text and encoding == record.encoding:
        try:
            if record.raw_payload.decode(encoding) == text:
                return record.raw_chunk
        except UnicodeDecodeError:
            # A legacy linked table may have contained a replacement character.
            pass
    try:
        return _encode_chunk(text, encoding, length_prefixed)
    except ValueError as exc:
        raise ValueError(f"String ID {record.string_id}: {exc}") from exc


def _duplicate_id_diagnostics(records: tuple[LocalizedStringRecord, ...]) -> tuple[Diagnostic, ...]:
    indices: dict[int, list[int]] = {}
    for record in records:
        indices.setdefault(record.string_id, []).append(record.directory_index)
    return tuple(
        Diagnostic(
            "LOCALIZED_STRING_ID_DUPLICATE",
            "A localized strings source contains a duplicate string ID.",
            details=(("string_id", string_id), ("record_indices", tuple(record_indices))),
        )
        for string_id, record_indices in indices.items()
        if len(record_indices) > 1
    )


def _namespace(format_id: FormatId, records: tuple[LocalizedStringRecord, ...]) -> SourceNamespace:
    material = b"".join(record.string_id.to_bytes(4, "little") for record in records)
    return SourceNamespace.from_fingerprint(format_id.value, hashlib.sha256(material).hexdigest())


def _local_key(string_id: int) -> str:
    return f"strings:{string_id}"


def _snapshot_records(snapshot: SourceSnapshot) -> tuple[LocalizedStringRecord, ...]:
    value = dict(snapshot.metadata).get("strings.records")
    if not isinstance(value, tuple) or not all(isinstance(record, LocalizedStringRecord) for record in value):
        raise ValueError("localized strings snapshot records are unavailable")
    return value


def _snapshot_namespace(snapshot: SourceSnapshot) -> SourceNamespace:
    value = dict(snapshot.metadata).get("source_namespace")
    if not isinstance(value, str):
        raise ValueError("localized strings snapshot namespace is unavailable")
    return SourceNamespace(value)


def _index_write_entries(entries: tuple[object, ...], namespace: SourceNamespace) -> dict[int, object]:
    result: dict[int, object] = {}
    for index, entry in enumerate(entries):
        entry_key = getattr(entry, "entry_key", None)
        string_id = getattr(entry, "string_id", None)
        if (
            not isinstance(entry_key, EntryKey)
            or entry_key.namespace != namespace
            or isinstance(string_id, bool)
            or not isinstance(string_id, int)
            or entry_key.local_key != _local_key(string_id)
        ):
            raise ValueError(f"write entry {index} has invalid localized identity")
        if string_id in result:
            raise ValueError(f"write entry {index} duplicates localized string ID {string_id}")
        result[string_id] = entry
    return result


def _stage_diagnostic(entry: object, index: int, policy: StagePolicyPort) -> Diagnostic | None:
    decision = policy.evaluate(
        getattr(entry, "stage", None),
        getattr(entry, "translation", ""),
        StageOperation.PUBLISH,
        original=getattr(entry, "original", ""),
    )
    if not decision.blocks_publish:
        return None
    diagnostic = decision.diagnostic or Diagnostic("STAGE_PUBLISH_BLOCKED", "The stage blocks publication.")
    return replace(
        diagnostic,
        details=(*diagnostic.details, ("record_index", index), ("string_id", getattr(entry, "string_id", None))),
    )


def _failed_parse(
    request: ParseRequest,
    adapter: LocalizedStringsAdapter,
    error: OSError | ValueError,
) -> ParseResult:
    return ParseResult(
        OperationOutcome.FAILED,
        adapter.format_id,
        request.source,
        diagnostics=(
            Diagnostic(
                "LOCALIZED_STRINGS_PARSE_FAILED",
                f"The localized strings source is invalid: {request.source.uri} ({error}).",
            ),
        ),
        stats=ParseStats(failed=1),
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.adapter_version,
        capability=adapter.capabilities(),
    )


def _failed_write(
    diagnostics: tuple[Diagnostic, ...],
    run_id: str | None,
) -> OperationResult:
    return OperationResult(
        OperationOutcome.FAILED,
        diagnostics=diagnostics,
        counts=OperationCounts(failed=max(len(diagnostics), 1)),
        run_id=run_id,
    )


def _cancelled(token: object | None) -> bool:
    if token is None:
        return False
    state = token.is_cancelled
    return bool(state() if callable(state) else state)


def _local_path(uri: str) -> Path:
    if "://" in uri:
        raise ValueError("localized strings adapters only accept local paths")
    return Path(uri)

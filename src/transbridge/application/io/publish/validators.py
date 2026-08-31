"""Format renderer facade and parse-write-reparse fidelity validator."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from typing import Protocol

from transbridge.application.contracts import OperationOutcome, OperationResult
from transbridge.application.io.contracts import (
    FormatId,
    FormatProbe,
    ParseRequest,
    ProbeRequest,
    ProbeStatus,
    SourceDescriptor,
    WriteRequest,
)
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.ports import FormatAdapter
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY, StageOperation

from .filesystem import PublishFilesystemPort
from .models import ValidationReport


class ArtifactRenderer(Protocol):
    def render(self, request: WriteRequest, staging_path: str) -> OperationResult[tuple[str, ...]]: ...


class ArtifactValidator(Protocol):
    def validate(self, request: WriteRequest, staging_path: str) -> ValidationReport: ...


class FormatAdapterRenderer:
    def __init__(self, adapter: FormatAdapter) -> None:
        self._adapter = adapter

    def render(self, request: WriteRequest, staging_path: str) -> OperationResult[tuple[str, ...]]:
        staged_request = replace(
            request,
            target=SourceDescriptor(staging_path, display_name=request.target.display_name),
        )
        return self._adapter.write(staged_request)


class FormatRoundTripValidator:
    """Validate structure, reparse, and critical entry content through one adapter."""

    def __init__(self, adapter: FormatAdapter, filesystem: PublishFilesystemPort) -> None:
        self._adapter = adapter
        self._filesystem = filesystem

    def validate(self, request: WriteRequest, staging_path: str) -> ValidationReport:
        try:
            content = self._filesystem.read_bytes(staging_path)
            probe = self._adapter.probe(
                ProbeRequest(
                    SourceDescriptor(staging_path, display_name=request.target.display_name),
                    content,
                    request.format_id,
                )
            )
        except (OSError, TypeError, ValueError) as exc:
            return _invalid(request, "STRUCTURE_VALIDATION_FAILED", type(exc).__name__)
        structure_valid = _probe_accepts(probe, request)
        if not structure_valid:
            return ValidationReport(
                False,
                request.format_id,
                False,
                False,
                False,
                0,
                code="FORMAT_PROBE_REJECTED",
                message="staged artifact does not match the requested format",
            )

        namespace = _entry_namespace(request.entries)
        parsed = self._adapter.parse(
            ParseRequest(
                SourceDescriptor(staging_path, display_name=request.target.display_name),
                request.context,
                request.format_id,
                namespace,
                cancellation=request.cancellation,
            )
        )
        if parsed.outcome is not OperationOutcome.COMPLETED:
            return ValidationReport(
                False,
                request.format_id,
                True,
                False,
                False,
                0,
                code="REPARSE_FAILED",
                message="staged artifact could not be reparsed completely",
            )
        try:
            expected = _expected_summary(request)
            actual = _parsed_summary(
                parsed.entries,
                frozenset(item[0] for item in expected),
            )
        except (TypeError, ValueError) as exc:
            return _invalid(request, "FIDELITY_SUMMARY_FAILED", type(exc).__name__, structure=True, reparse=True)
        valid = expected == actual
        summary_hash = _summary_hash(actual)
        return ValidationReport(
            valid,
            request.format_id,
            True,
            True,
            valid,
            len(parsed.entries),
            summary_hash,
            "VALID" if valid else "FIDELITY_MISMATCH",
            "artifact round-trip fidelity passed" if valid else "reparsed entries differ from the write request",
        )


def _probe_accepts(probe: FormatProbe, request: WriteRequest) -> bool:
    return probe.status is ProbeStatus.EXACT and probe.candidates == (request.format_id,)


def _entry_namespace(entries: tuple[object, ...]) -> SourceNamespace | None:
    namespaces = {
        entry_key.namespace
        for entry in entries
        if isinstance((entry_key := getattr(entry, "entry_key", None)), EntryKey)
    }
    return next(iter(namespaces)) if len(namespaces) == 1 else None


def _entry_key(entry: object) -> EntryKey:
    entry_key = getattr(entry, "entry_key", None)
    if not isinstance(entry_key, EntryKey):
        raise TypeError("fidelity entries require a canonical EntryKey")
    return entry_key


def _expected_summary(request: WriteRequest) -> tuple[tuple[str, str, str], ...]:
    policy = request.stage_policy or DEFAULT_STAGE_POLICY
    values: list[tuple[str, str, str]] = []
    for entry in request.entries:
        identity = _entry_key(entry)
        decision = policy.evaluate(
            getattr(entry, "stage", None),
            getattr(entry, "translation", ""),
            StageOperation.PUBLISH,
            original=getattr(entry, "original", ""),
        )
        if decision.publish_text is None:
            raise ValueError("blocking stage reached fidelity validation")
        if request.format_id in {
            FormatId.PLUGIN_SSE,
            FormatId.STRINGS,
            FormatId.DLSTRINGS,
            FormatId.ILSTRINGS,
        }:
            values.append((identity.serialize(), decision.publish_text, ""))
        else:
            # Bilingual exchange formats persist the translation field itself,
            # including empty or draft translations, rather than game-ready text.
            values.append((
                identity.serialize(),
                str(getattr(entry, "original", "")),
                str(getattr(entry, "translation", "")),
            ))
    return tuple(sorted(values))


def _parsed_summary(
    entries: tuple[object, ...],
    expected_keys: frozenset[str],
) -> tuple[tuple[str, str, str], ...]:
    values: list[tuple[str, str, str]] = []
    for entry in entries:
        identity = _entry_key(entry)
        serialized = identity.serialize()
        if serialized in expected_keys:
            values.append((
                serialized,
                str(getattr(entry, "original", "")),
                str(getattr(entry, "translation", "")),
            ))
    return tuple(sorted(values))


def _summary_hash(summary: tuple[tuple[str, str, str], ...]) -> str:
    payload = json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _invalid(
    request: WriteRequest,
    code: str,
    detail: str,
    *,
    structure: bool = False,
    reparse: bool = False,
) -> ValidationReport:
    return ValidationReport(
        False,
        request.format_id,
        structure,
        reparse,
        False,
        0,
        code=code,
        message=f"artifact validation failed ({detail})",
    )

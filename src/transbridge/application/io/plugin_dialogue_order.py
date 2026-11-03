"""Derive dialogue topology only from immutable, captured plugin authority."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from threading import RLock

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.parser.plugin.dialogue_order_binary import check_cancelled
from transbridge.parser.plugin.dialogue_order_evidence import (
    EXTRACTION_VERSION,
    DialogueOrderEvidence,
    evidence_from_payload,
    evidence_payload,
    read_dialogue_order_bytes,
    read_dialogue_order_evidence,
)

from .contracts import FormatId, SourceSnapshot

METADATA_KEY = "plugin_dialogue_order"
_CACHE_LIMIT = 4
_cache: OrderedDict[tuple[str, int], DialogueOrderEvidence] = OrderedDict()
_metadata_cache: OrderedDict[tuple[str, int, int], tuple[tuple, DialogueOrderEvidence]] = OrderedDict()
_cache_lock = RLock()
_IMMUTABLE_SCALARS = frozenset((str, int, float, bool, bytes, type(None)))


def capture_dialogue_order_metadata(plugin, sha256: str, *, cancellation=None) -> tuple[str, tuple]:
    """Return one metadata pair while the initial complete plugin is available."""
    try:
        evidence = read_dialogue_order_evidence(plugin, cancellation=cancellation)
    except (ValueError, UnicodeError) as exc:
        evidence = _invalid_binary(exc)
    _remember((sha256, EXTRACTION_VERSION), evidence)
    payload = evidence_payload(evidence)
    _remember_metadata((sha256, EXTRACTION_VERSION, id(payload)), payload, evidence)
    return METADATA_KEY, (EXTRACTION_VERSION, sha256, payload)


def dialogue_order_from_snapshot(snapshot: SourceSnapshot, *, cancellation=None) -> DialogueOrderEvidence | None:
    """Read validated metadata, or scan old captured bytes once per content hash.

    No source URI, plugin masters, XML, or sibling strings are read. The bounded
    cache retains only small immutable evidence, never the captured plugin bytes.
    Extraction belongs in the caller's existing source hydration worker.
    """
    if snapshot.format_id is not FormatId.PLUGIN_SSE:
        return None
    cancelled = (lambda: cancellation.is_cancelled) if cancellation is not None else None
    check_cancelled(cancelled)
    value = dict(snapshot.metadata).get(METADATA_KEY)
    metadata_diagnostic = None
    if value is not None:
        try:
            if not isinstance(value, tuple) or len(value) != 3:
                raise ValueError("Invalid dialogue metadata envelope")
            version, digest, payload = value
            if type(version) is not int or version != EXTRACTION_VERSION or digest != snapshot.sha256:
                raise ValueError("Dialogue metadata version or source digest differs")
            metadata_key = digest, version, id(payload)
            with _cache_lock:
                cached = _metadata_cache.get(metadata_key)
                if cached is not None and cached[0] is payload:
                    _metadata_cache.move_to_end(metadata_key)
                    check_cancelled(cancelled)
                    return cached[1]
            evidence = evidence_from_payload(payload)
            check_cancelled(cancelled)
            _remember_metadata(metadata_key, payload, evidence)
            return evidence
        except (ValueError, TypeError) as exc:
            metadata_diagnostic = Diagnostic(
                "DIALOGUE_ORDER_METADATA_INVALID",
                f"Saved dialogue metadata was rejected and captured bytes will be used: {exc}.",
                DiagnosticSeverity.WARNING,
            )
    key = snapshot.sha256, EXTRACTION_VERSION
    # Serialize first extraction for an identical snapshot. This lock never
    # protects UI work and prevents concurrent workers decoding the same source.
    with _cache_lock:
        check_cancelled(cancelled)
        evidence = _cache.get(key)
        if evidence is not None:
            _cache.move_to_end(key)
        elif snapshot.content is None:
            evidence = DialogueOrderEvidence(
                diagnostics=(
                    Diagnostic(
                        "DIALOGUE_ORDER_CAPTURE_REQUIRED",
                        "Dialogue order requires captured plugin bytes; the source location was not opened.",
                        DiagnosticSeverity.WARNING,
                    ),
                ),
                complete=False,
            )
        else:
            try:
                evidence = read_dialogue_order_bytes(snapshot.content, cancellation=cancellation)
            except (ValueError, UnicodeError) as exc:
                evidence = _invalid_binary(exc)
            _remember(key, evidence)
    check_cancelled(cancelled)
    if metadata_diagnostic is not None:
        evidence = replace(evidence, diagnostics=(*evidence.diagnostics, metadata_diagnostic))
    return evidence


def _remember(key: tuple[str, int], evidence: DialogueOrderEvidence) -> None:
    with _cache_lock:
        _cache[key] = evidence
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_LIMIT:
            _cache.popitem(last=False)


def _remember_metadata(key, payload, evidence):
    # Cache the exact validated immutable payload, not just its claimed digest.
    # Mutable legacy payloads remain supported but must be validated on every read.
    def immutable(value):
        return type(value) in _IMMUTABLE_SCALARS or (type(value) is tuple and all(immutable(item) for item in value))

    if not immutable(payload):
        return
    with _cache_lock:
        _metadata_cache[key] = payload, evidence
        _metadata_cache.move_to_end(key)
        while len(_metadata_cache) > _CACHE_LIMIT:
            _metadata_cache.popitem(last=False)


def _invalid_binary(error: Exception) -> DialogueOrderEvidence:
    return DialogueOrderEvidence(
        diagnostics=(
            Diagnostic(
                "DIALOGUE_ORDER_BINARY_INVALID",
                f"Captured plugin dialogue structure could not be read: {error}.",
                DiagnosticSeverity.WARNING,
            ),
        ),
        complete=False,
    )

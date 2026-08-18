from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib

import pytest

from transbridge.application.contracts import Diagnostic, OperationOutcome, RequestContext
from transbridge.application.io import (
    FormatId,
    ParseResult,
    ParseStats,
    SourceDescriptor,
    SourceSnapshot,
    WriteRequest,
)


def _source(name: str = "empty.json") -> SourceDescriptor:
    return SourceDescriptor(f"memory:///{name}", name)


def _snapshot(content: bytes = b"[]") -> SourceSnapshot:
    return SourceSnapshot.from_bytes(_source(), FormatId.JSON_PARATRANZ, content, encoding="utf-8")


def test_source_snapshot_binds_content_hash_and_is_immutable() -> None:
    snapshot = _snapshot()

    assert snapshot.sha256 == hashlib.sha256(b"[]").hexdigest()
    with pytest.raises(FrozenInstanceError):
        snapshot.size_bytes = 100  # type: ignore[misc]
    with pytest.raises(ValueError, match="hash"):
        SourceSnapshot(_source(), FormatId.JSON_PARATRANZ, "0" * 64, 2, b"[]")


def test_large_source_snapshot_requires_explicit_lease() -> None:
    source = SourceDescriptor("file:///large.esp", "large.esp", size_bytes=1_000_000)

    with pytest.raises(ValueError, match="lease_id"):
        SourceSnapshot(source, FormatId.PLUGIN_SSE, hashlib.sha256(b"x").hexdigest(), 1_000_000)

    snapshot = SourceSnapshot(
        source,
        FormatId.PLUGIN_SSE,
        hashlib.sha256(b"x").hexdigest(),
        1_000_000,
        lease_id="lease-1",
    )
    assert snapshot.content is None


def test_legal_empty_parse_is_completed_not_failed() -> None:
    result = ParseResult.completed(FormatId.JSON_PARATRANZ, _source(), _snapshot(), ())

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.entries == ()
    assert result.stats.total == 0


def test_partial_failed_and_cancelled_are_mutually_distinct() -> None:
    diagnostic = Diagnostic("ENTRY_DAMAGED", "Entry 2 is damaged.")
    partial = ParseResult(
        OperationOutcome.PARTIAL,
        FormatId.JSON_PARATRANZ,
        _source(),
        _snapshot(),
        ("entry-1",),
        (diagnostic,),
        ParseStats(parsed=1, failed=1),
    )
    failed = ParseResult(
        OperationOutcome.FAILED,
        FormatId.JSON_PARATRANZ,
        _source(),
        diagnostics=(diagnostic,),
        stats=ParseStats(failed=1),
    )
    cancelled = ParseResult(
        OperationOutcome.CANCELLED,
        FormatId.JSON_PARATRANZ,
        _source(),
        stats=ParseStats(cancelled=1),
    )

    assert partial.entries == ("entry-1",)
    assert partial.source_snapshot is not None
    assert failed.entries == () and failed.source_snapshot is None
    assert cancelled.entries == () and cancelled.source_snapshot is None


def test_exception_cannot_be_represented_as_completed_empty() -> None:
    with pytest.raises(ValueError, match="valid snapshot"):
        ParseResult(OperationOutcome.COMPLETED, FormatId.JSON_DSD, _source())
    with pytest.raises(ValueError, match="no failures"):
        ParseResult(
            OperationOutcome.COMPLETED,
            FormatId.JSON_DSD,
            _source(),
            _snapshot(),
            diagnostics=(Diagnostic("PARSE_ERROR", "Invalid JSON."),),
        )


def test_cancelled_parse_never_carries_publishable_snapshot() -> None:
    with pytest.raises(ValueError, match="publishable"):
        ParseResult(
            OperationOutcome.CANCELLED,
            FormatId.JSON_PARATRANZ,
            _source(),
            source_snapshot=_snapshot(),
            stats=ParseStats(cancelled=1),
        )


def test_write_request_requires_snapshot_or_explicit_template() -> None:
    context = RequestContext("contract-test")
    target = SourceDescriptor("file:///output.json", "output.json")

    with pytest.raises(ValueError, match="exactly one"):
        WriteRequest(target, FormatId.JSON_PARATRANZ, (), 0, context)
    request = WriteRequest(target, FormatId.JSON_PARATRANZ, (), 0, context, new_template=b"")
    assert request.new_template == b""

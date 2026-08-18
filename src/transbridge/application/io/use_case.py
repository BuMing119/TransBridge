"""Shared parse/write use case used by GUI and Agent process adapters."""

from __future__ import annotations

from transbridge.application.contracts import (
    Diagnostic,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)

from .catalog import FormatCatalog, default_format_catalog
from .contracts import FormatId, ParseRequest, ParseResult, ParseStats, WriteRequest


class TranslationIoUseCase:
    def __init__(self, catalog: FormatCatalog | None = None) -> None:
        self._catalog = catalog or default_format_catalog()

    def parse(self, request: ParseRequest) -> ParseResult:
        format_id = request.format_hint
        if format_id is None:
            return ParseResult(
                OperationOutcome.FAILED,
                FormatId.JSON_TRANSBRIDGE,
                request.source,
                diagnostics=(
                    Diagnostic(
                        "FORMAT_SELECTION_REQUIRED",
                        "A format hint is required before the source can be parsed.",
                    ),
                ),
                stats=ParseStats(failed=1),
            )
        adapter = self._catalog.adapter(format_id)
        if adapter is None:
            return ParseResult(
                OperationOutcome.FAILED,
                format_id,
                request.source,
                diagnostics=(
                    Diagnostic("FORMAT_ADAPTER_UNAVAILABLE", f"No adapter is registered for {format_id.value}."),
                ),
                stats=ParseStats(failed=1),
            )
        return adapter.parse(request)

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]:
        adapter = self._catalog.adapter(request.format_id)
        if adapter is None:
            return OperationResult(
                OperationOutcome.FAILED,
                diagnostics=(
                    Diagnostic(
                        "FORMAT_ADAPTER_UNAVAILABLE",
                        f"No adapter is registered for {request.format_id.value}.",
                    ),
                ),
                counts=OperationCounts(failed=1),
                run_id=request.context.run_id,
            )
        return adapter.write(request)

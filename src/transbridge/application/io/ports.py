"""Translation I/O application ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from transbridge.application.contracts import OperationResult

from .contracts import (
    FormatCapability,
    FormatId,
    FormatProbe,
    ParseRequest,
    ParseResult,
    ProbeRequest,
    WriteRequest,
)


@runtime_checkable
class FormatAdapter(Protocol):
    """The only parser/writer contract visible to the application layer."""

    @property
    def format_id(self) -> FormatId: ...

    @property
    def adapter_id(self) -> str: ...

    @property
    def adapter_version(self) -> str: ...

    def probe(self, request: ProbeRequest) -> FormatProbe: ...

    def parse(self, request: ParseRequest) -> ParseResult: ...

    def validate_write(self, request: WriteRequest) -> OperationResult[None]: ...

    def write(self, request: WriteRequest) -> OperationResult[tuple[str, ...]]: ...

    def capabilities(self) -> FormatCapability: ...

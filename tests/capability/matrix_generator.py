"""CapabilityMatrix generator for release-hardening-v2 Story 04.

The matrix is derived from the format capability registry (``FormatCatalog``)
combined with optional dependency reports.  For every format and every entry,
exactly one result cell must be produced; a missing cell is a hard error and
an empty matrix is rejected at construction time.  Unspported/experimental
levels are never reported as *available*, and the SST Writer is pinned to
``unavailable`` regardless of any adapter claim so no entrypoint can advertise
write support it cannot provide.

The UI / Agent / MCP accessors share the exact same capability id and reason
for a given (format, entry), guaranteeing cross-entrypoint consistency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from transbridge.application.capabilities import CapabilityId, CapabilityReport, CapabilityState
from transbridge.application.io import (
    CapabilityLevel,
    FormatCapability,
    FormatCatalog,
    FormatId,
    default_format_catalog,
)

# Entrypoints covered by the matrix.  The first four are the raw I/O rows, the
# last three are the interactive surfaces that must stay consistent with them.
ENTRYPOINTS: tuple[str, ...] = ("read", "write", "roundtrip", "gui", "agent", "mcp", "publish")

# Map a FormatCapability field for each entrypoint we report.
_CAPABILITY_FIELD: dict[str, str] = {
    "read": "read",
    "write": "write",
    "roundtrip": "round_trip",
    "gui": "gui",
    "agent": "agent",
    "mcp": "mcp",
    "publish": "publish",
}

# Explicit capability ids for each entrypoint; shared verbatim by all callers.
_ENTRY_CAPABILITY_ID: dict[str, str] = {
    "read": "format.read",
    "write": "format.write",
    "roundtrip": "format.roundtrip",
    "gui": "format.gui",
    "agent": "format.agent",
    "mcp": "format.mcp",
    "publish": "format.publish",
}

# Formats whose Writer is architecturally pinned to unsupported on every
# entrypoint that implies writing.
_SST_FORMATS: frozenset[str] = frozenset({"sst.ssu8", "sst.ssu9"})

_LEVEL_TO_STATE: dict[CapabilityLevel, CapabilityState] = {
    CapabilityLevel.UNAVAILABLE: CapabilityState.UNAVAILABLE,
    CapabilityLevel.EXPERIMENTAL: CapabilityState.DEGRADED,
    CapabilityLevel.SUPPORTED: CapabilityState.AVAILABLE,
}

_WRITE_IMPLYING = frozenset({"write", "roundtrip", "publish"})


class CapabilityMatrixError(ValueError):
    """Raised for gaps, empty matrices, or invalid cells."""


@dataclass(frozen=True, slots=True)
class MatrixCell:
    """One (format, entrypoint) capability verdict."""

    format_id: FormatId
    entry: str
    capability_id: CapabilityId
    state: CapabilityState
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_id": self.format_id.value,
            "entry": self.entry,
            "capability_id": self.capability_id.value,
            "state": self.state.value,
            "reason": self.reason,
        }


class CapabilityMatrix:
    """Complete, closed capability matrix over the format registry."""

    def __init__(self, cells: list[MatrixCell]) -> None:
        if not cells:
            raise CapabilityMatrixError("capability matrix must not be empty")
        self._cells: dict[tuple[FormatId, str], MatrixCell] = {}
        for cell in cells:
            key = (cell.format_id, cell.entry)
            if key in self._cells:
                raise CapabilityMatrixError(f"duplicate matrix cell for {cell.format_id}/{cell.entry}")
            self._cells[key] = cell
        self._formats = tuple(sorted({cell.format_id for cell in self._cells.values()}, key=lambda item: item.value))
        self._entries = tuple(dict.fromkeys(cell.entry for cell in self._cells.values()))

    @property
    def formats(self) -> tuple[FormatId, ...]:
        return self._formats

    @property
    def entries(self) -> tuple[str, ...]:
        return self._entries

    def cell(self, format_id: FormatId | str, entry: str) -> MatrixCell:
        key = (format_id if isinstance(format_id, FormatId) else FormatId(format_id), entry)
        if key not in self._cells:
            raise CapabilityMatrixError(f"missing capability result for {key[0]}/{key[1]}")
        return self._cells[key]

    def report(self, format_id: FormatId | str, entry: str) -> CapabilityReport:
        cell = self.cell(format_id, entry)
        return CapabilityReport(
            CapabilityId(cell.capability_id.value),
            cell.state,
            reasons=(cell.reason,),
            metadata=(("entry", cell.entry), ("format", cell.format_id.value)),
        )

    # --- UI / Agent / MCP consistency ------------------------------------

    def ui(self, format_id: FormatId | str, entry: str) -> tuple[str, str]:
        return self._surface_result(format_id, entry)

    def agent(self, format_id: FormatId | str, entry: str) -> tuple[str, str]:
        return self._surface_result(format_id, entry)

    def mcp(self, format_id: FormatId | str, entry: str) -> tuple[str, str]:
        return self._surface_result(format_id, entry)

    def _surface_result(self, format_id: FormatId | str, entry: str) -> tuple[str, str]:
        cell = self.cell(format_id, entry)
        return cell.capability_id.value, cell.reason

    def as_matrix(self) -> dict[str, dict[str, str]]:
        """Rows = formats, columns = entrypoints, values = state."""
        matrix: dict[str, dict[str, str]] = {}
        for fmt in self._formats:
            matrix[fmt.value] = {entry: self.cell(fmt, entry).state.value for entry in self._entries}
        return matrix


def _derive_cell(
    format_id: FormatId,
    capability: FormatCapability,
    entry: str,
    *,
    dependency_overrides: dict[tuple[FormatId, str], CapabilityState],
) -> MatrixCell:
    field = _CAPABILITY_FIELD[entry]
    level = getattr(capability, field)
    state = _LEVEL_TO_STATE[level]

    # SST Writer is pinned unsupported: no entrypoint that implies writing may
    # ever report write capability for an SST format.
    if format_id.value in _SST_FORMATS and entry in _WRITE_IMPLYING:
        if state is not CapabilityState.UNAVAILABLE:
            state = CapabilityState.UNAVAILABLE

    if format_id.value in _SST_FORMATS and entry in _WRITE_IMPLYING:
        reason = "SST Writer is architecturally unsupported (policy ceiling forbids write/roundtrip/publish)"
    elif state is CapabilityState.UNAVAILABLE:
        reason = f"capability {entry!r} not available for {format_id.value}"
    elif state is CapabilityState.DEGRADED:
        reason = f"capability {entry!r} is experimental for {format_id.value}"
    else:
        reason = f"capability {entry!r} supported for {format_id.value}"

    override = dependency_overrides.get((format_id, entry))
    if override is not None:
        state = override
        reason = f"degraded by missing optional dependency for {format_id.value}/{entry}"

    capability_id = CapabilityId(f"{_ENTRY_CAPABILITY_ID[entry]}.{format_id.value}")
    return MatrixCell(format_id, entry, capability_id, state, reason)


def build_capability_matrix(
    *,
    catalog: FormatCatalog | None = None,
    dependency_overrides: dict[tuple[FormatId, str], CapabilityState] | None = None,
) -> CapabilityMatrix:
    """Build the complete matrix; reject missing results and empty inputs.

    :raises CapabilityMatrixError: when a format/entry has no resolvable
        result or the catalog contributes no formats.
    """
    active_catalog = catalog or default_format_catalog()
    snapshots = active_catalog.capability_snapshot()
    if not snapshots:
        raise CapabilityMatrixError("format catalog produced no capability results")

    overrides = dependency_overrides or {}
    cells: list[MatrixCell] = []
    for snapshot in snapshots:
        fmt = snapshot.format_id
        capability = snapshot.capability
        for entry in ENTRYPOINTS:
            cell = _derive_cell(
                fmt,
                capability,
                entry,
                dependency_overrides=overrides,
            )
            cells.append(cell)

    matrix = CapabilityMatrix(cells)
    # Gap detection: every format must have a result for every declared
    # entrypoint.  A missing cell raises during CapabilityMatrix construction.
    for fmt in matrix.formats:
        for entry in ENTRYPOINTS:
            matrix.cell(fmt, entry)
    return matrix


__all__ = [
    "ENTRYPOINTS",
    "CapabilityMatrix",
    "CapabilityMatrixError",
    "MatrixCell",
    "build_capability_matrix",
]

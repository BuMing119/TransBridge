"""Tests for the CapabilityMatrix generator (release-hardening-v2 Story 04).

Asserts that every format and every entrypoint has exactly one result, that
the SST Writer is pinned to unsupported, that missing results and empty
matrices are rejected, and that UI/Agent/MCP surfaces return identical
capability ids and reasons.
"""

from __future__ import annotations

import pytest

from tests.capability.matrix_generator import (
    ENTRYPOINTS,
    CapabilityMatrix,
    CapabilityMatrixError,
    MatrixCell,
    build_capability_matrix,
)
from transbridge.application.capabilities import CapabilityId, CapabilityReport, CapabilityState
from transbridge.application.io import FormatCatalog, FormatId
from transbridge.application.io.contracts import FormatCapability, FormatCapabilitySnapshot


def _snapshot(format_id: FormatId, capability: FormatCapability) -> FormatCapabilitySnapshot:
    return FormatCapabilitySnapshot(format_id, capability, capability)


def test_every_format_and_entrypoint_has_a_result() -> None:
    matrix = build_capability_matrix()

    for fmt in matrix.formats:
        for entry in ENTRYPOINTS:
            cell = matrix.cell(fmt, entry)
            assert cell.capability_id.value
            assert cell.reason

    # The default catalog exposes every known format id.
    assert set(matrix.formats) == set(FormatId)
    assert matrix.entries == ENTRYPOINTS


def test_sst_writer_is_pinned_unsupported() -> None:
    matrix = build_capability_matrix()

    for fmt in (FormatId.SST_SSU8, FormatId.SST_SSU9):
        for entry in ("write", "roundtrip", "publish"):
            cell = matrix.cell(fmt, entry)
            assert cell.state is CapabilityState.UNAVAILABLE
            assert "SST Writer" in cell.reason


def test_unsupported_and_experimental_never_reported_available() -> None:
    matrix = build_capability_matrix()

    # BINARY_EET writer is unavailable in the policy ceiling.
    assert matrix.cell(FormatId.BINARY_EET, "write").state is CapabilityState.UNAVAILABLE
    # JSON_DSD has no V2 adapter, so even though its ceiling is experimental it
    # must never be reported as available.
    assert matrix.cell(FormatId.JSON_DSD, "read").state is not CapabilityState.AVAILABLE
    # A supported format is available.
    assert matrix.cell(FormatId.XML_EET, "read").state is CapabilityState.AVAILABLE


def test_ui_agent_mcp_return_identical_id_and_reason() -> None:
    matrix = build_capability_matrix()

    for fmt in matrix.formats:
        for entry in ENTRYPOINTS:
            ui = matrix.ui(fmt, entry)
            agent = matrix.agent(fmt, entry)
            mcp = matrix.mcp(fmt, entry)
            assert ui == agent == mcp
            assert ui == (matrix.cell(fmt, entry).capability_id.value, matrix.cell(fmt, entry).reason)


def test_capability_id_is_stable_and_scoped() -> None:
    matrix = build_capability_matrix()

    read_id = matrix.cell(FormatId.XML_EET, "read").capability_id
    write_id = matrix.cell(FormatId.XML_EET, "write").capability_id
    assert read_id != write_id
    assert read_id.value.startswith("format.read.")
    assert write_id.value.startswith("format.write.")


def test_missing_result_is_rejected() -> None:
    matrix = build_capability_matrix()

    with pytest.raises(CapabilityMatrixError, match="missing capability result"):
        matrix.cell(FormatId.XML_EET, "nonexistent-entry")


def test_empty_matrix_is_rejected() -> None:
    with pytest.raises(CapabilityMatrixError, match="must not be empty"):
        CapabilityMatrix([])


def test_duplicate_cell_is_rejected() -> None:
    cell = MatrixCell(
        FormatId.XML_EET,
        "read",
        CapabilityId("format.read.xml.eet"),
        CapabilityState.AVAILABLE,
        "reason",
    )
    with pytest.raises(CapabilityMatrixError, match="duplicate matrix cell"):
        CapabilityMatrix([cell, cell])


def test_catalog_with_no_formats_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = FormatCatalog(policy_ceilings={FormatId.XML_EET: FormatCapability.unavailable()})
    # Simulate a registry that yields zero capability results.
    monkeypatch.setattr(catalog, "capability_snapshot", lambda: ())
    with pytest.raises(CapabilityMatrixError, match="no capability results"):
        build_capability_matrix(catalog=catalog)


def test_dependency_override_degrades_entry_and_reason() -> None:
    overrides = {(FormatId.PLUGIN_SSE, "agent"): CapabilityState.DEGRADED}
    matrix = build_capability_matrix(dependency_overrides=overrides)

    cell = matrix.cell(FormatId.PLUGIN_SSE, "agent")
    assert cell.state is CapabilityState.DEGRADED
    assert "missing optional dependency" in cell.reason
    # Non-overridden cells are unaffected.
    assert matrix.cell(FormatId.PLUGIN_SSE, "read").state is CapabilityState.AVAILABLE


def test_report_roundtrips_through_capability_report() -> None:
    matrix = build_capability_matrix()
    report = matrix.report(FormatId.XML_EET, "read")
    assert isinstance(report, CapabilityReport)
    restored = CapabilityReport.from_dict(report.to_dict())
    assert restored == report
    assert restored.capability.value == matrix.cell(FormatId.XML_EET, "read").capability_id.value


def test_matrix_rejects_unambiguous_empty_catalog_states() -> None:
    # A ceiling of all-unavailable still yields a complete (non-empty) matrix.
    ceilings = {FormatId.XML_EET: FormatCapability.unavailable()}
    catalog = FormatCatalog(policy_ceilings=ceilings)
    matrix = build_capability_matrix(catalog=catalog)

    assert matrix.cell(FormatId.XML_EET, "read").state is CapabilityState.UNAVAILABLE
    assert matrix.cell(FormatId.XML_EET, "publish").state is CapabilityState.UNAVAILABLE

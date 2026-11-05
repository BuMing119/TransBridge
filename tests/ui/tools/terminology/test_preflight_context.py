from dataclasses import replace

import pytest

from tests.application.terminology.test_input_capture import (
    _Baselines,
    _Capabilities,
    _Leases,
    _Projects,
    _registry,
    _state,
)
from transbridge.application.contracts import (
    Diagnostic,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.projects.source_registry import SourceRegistrySnapshot
from transbridge.application.terminology.input_capture import BuildInputCaptureService
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices


def _context(**values):
    return RequestContext(
        "operator",
        project_id="project-1",
        variant_id="main",
        metadata=(("project_name", "真实项目名称"), ("variant_name", "简体中文")),
        **values,
    )


def test_source_limit_failure_keeps_actual_context_and_source_count_before_any_file_is_captured():
    first = _registry().sources[0]
    registrations = tuple(replace(first, source_id=f"source-{index}") for index in range(51))
    leases = _Leases()
    inputs = BuildInputCaptureService(
        _Projects(_state(SourceRegistrySnapshot(registrations))),
        leases,
        _Capabilities(),
        _Baselines(),
        max_unstreamed_source_count=50,
    )
    presenter = TerminologyPresenter(TerminologyUiServices(build_inputs=inputs), _context())
    state = presenter.preflight()
    assert not state.ready and presenter.snapshot is None
    assert state.sources == () and state.source_count == 51
    assert state.project_display_name == "真实项目名称"
    assert state.variant_display_name == "简体中文"
    assert "51" in state.message and "50" in state.message
    assert "导入已有术语" in state.message
    assert "完整载入构建器" not in state.message
    assert leases.acquired == []
    technical = {item.label: item.value for item in state.technical_details}
    assert technical["source_count"] == "51" and technical["limit_count"] == "50"
    assert "project-1" not in state.project_label


@pytest.mark.parametrize(
    "code,count",
    [
        ("TERMINOLOGY_SOURCE_REQUIRED", 0),
        ("TERMINOLOGY_SOURCE_CHANGED_DURING_CAPTURE", None),
        ("TERMINOLOGY_SOURCE_CAPABILITY_UNAVAILABLE", None),
    ],
)
def test_failed_checks_distinguish_zero_sources_from_unknown_count(code, count):
    class Inputs:
        def capture_build_input(self, _context, *, config):
            return OperationResult(
                OperationOutcome.FAILED,
                diagnostics=(Diagnostic(code, "specific failure"),),
                counts=OperationCounts(failed=1),
            )

    state = TerminologyPresenter(TerminologyUiServices(build_inputs=Inputs()), _context()).preflight()
    assert not state.ready and state.source_count == count
    assert state.project_display_name == "真实项目名称"
    assert state.variant_display_name == "简体中文"
    if count is None:
        assert "0 个" not in state.scope_label
        assert "尚未确认" in state.scope_label


@pytest.mark.parametrize("value", [-1, True, "51", None])
def test_untrusted_or_absent_diagnostic_count_is_unknown(value):
    class Inputs:
        def capture_build_input(self, _context, *, config):
            return OperationResult(
                OperationOutcome.FAILED,
                diagnostics=(
                    Diagnostic("TERMINOLOGY_SOURCE_READ_FAILED", "读取失败", details=(("source_count", value),)),
                ),
                counts=OperationCounts(failed=1),
            )

    state = TerminologyPresenter(TerminologyUiServices(build_inputs=Inputs()), _context()).preflight()
    assert state.source_count is None


def test_missing_services_preserve_known_names_without_claiming_zero_sources():
    state = TerminologyPresenter(TerminologyUiServices(), _context()).preflight()
    assert not state.ready
    assert state.project_display_name == "真实项目名称"
    assert state.variant_display_name == "简体中文"
    assert state.source_count is None


def test_unknown_names_do_not_display_internal_ids_as_project_names():
    context = RequestContext("operator", project_id="internal-project-id", variant_id="internal-variant-id")
    state = TerminologyPresenter(TerminologyUiServices(), context).preflight()
    assert state.project_display_name == "项目名称待确认"
    assert state.variant_display_name == "翻译版本名称待确认"
    assert "internal-" not in state.project_label + state.variant_label
    assert "internal-project-id" in {item.value for item in state.technical_details}


def test_successful_capture_source_count_is_explicit_and_does_not_change_safety_limits():
    inputs = BuildInputCaptureService(_Projects(_state(_registry())), _Leases(), _Capabilities(), _Baselines())
    state = TerminologyPresenter(TerminologyUiServices(build_inputs=inputs), _context()).preflight()
    assert state.ready and state.source_count == len(state.sources) == 1

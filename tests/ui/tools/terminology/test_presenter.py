from __future__ import annotations

from types import SimpleNamespace

from transbridge.application.contracts import (
    Diagnostic,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)
from transbridge.application.io import FormatId
from transbridge.application.terminology.conflicts import ConflictResolutionOperation
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.view_models import business_diagnostic


class _BuildInputs:
    def __init__(self, result) -> None:
        self.result = result

    def capture_build_input(self, _context, *, config):
        assert config == {}
        return self.result


def test_preflight_maps_prerequisite_failure_to_business_language() -> None:
    result = OperationResult(
        OperationOutcome.FAILED,
        diagnostics=(Diagnostic("TERMINOLOGY_SOURCE_REQUIRED", "missing"),),
        counts=OperationCounts(failed=1),
    )
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=_BuildInputs(result)),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )

    state = presenter.preflight()

    assert not state.ready
    assert state.title == "没有可用来源"
    assert "已有历史" in state.message
    assert state.diagnostic_code == "TERMINOLOGY_SOURCE_REQUIRED"


def test_preflight_projects_scope_without_reading_sqlite_or_counting_rows() -> None:
    registration = SimpleNamespace(
        source_id="source-1",
        display_name="Main.esm",
        location="D:/project/Main.esm",
        format_id=FormatId.PLUGIN_SSE,
    )
    source = SimpleNamespace(
        registration=registration,
        lease=SimpleNamespace(actual_fingerprint="f" * 64),
        adapter_id="plugin",
        adapter_version="1",
    )
    snapshot = SimpleNamespace(
        project_id="project",
        project_revision=3,
        variant_id="variant",
        variant_revision=4,
        sources=(source,),
        effective_version_id=None,
        config_digest="c" * 64,
    )
    result = OperationResult.completed(snapshot)
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=_BuildInputs(result)),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )

    state = presenter.preflight()

    assert state.ready
    assert state.scope_label == "来源范围 · 1 个已启用来源"
    assert state.current_version_label == "当前版本 · 尚无已发布版本"
    assert state.expected_scale_label == "构建后显示准确规模"
    assert state.sources[0].name == "Main.esm"
    assert state.action_label == "创建术语库"


def test_preflight_prefers_display_names_for_the_visual_project_context() -> None:
    snapshot = SimpleNamespace(
        project_id="project-id",
        project_revision=3,
        variant_id="variant-id",
        variant_revision=4,
        sources=(),
        effective_version_id="v8",
        config_digest="c" * 64,
    )
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=_BuildInputs(OperationResult.completed(snapshot))),
        RequestContext(
            "operator",
            project_id="project-id",
            variant_id="variant-id",
            metadata=(("project_name", "Skyrim SE 汉化项目"), ("variant_name", "简体中文")),
        ),
    )

    state = presenter.preflight()

    assert state.project_display_name == "Skyrim SE 汉化项目"
    assert state.variant_display_name == "简体中文"
    assert state.current_version_value == "v8"


def test_preflight_uses_update_action_when_a_published_version_exists() -> None:
    snapshot = SimpleNamespace(
        project_id="project",
        project_revision=3,
        variant_id="variant",
        variant_revision=4,
        sources=(),
        effective_version_id="version-1",
        config_digest="c" * 64,
    )
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=_BuildInputs(OperationResult.completed(snapshot))),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )

    state = presenter.preflight()

    assert state.ready
    assert state.action_label == "更新术语库"
    assert state.title == "可以更新术语库"
    assert state.current_version_label == "当前版本 · version-1"


def test_progressive_notices_explain_impact_history_and_recovery() -> None:
    stale = business_diagnostic("TERMINOLOGY_BUILD_STALE")
    suppressed = business_diagnostic("TERM_SUPPRESSED")
    log_failed = business_diagnostic("CHANGELOG_RENDER_FAILED")

    assert "不能发布" in stale.impact and "重新构建" in stale.recovery
    assert "历史" in suppressed.message and "重新启用" in suppressed.recovery
    assert log_failed.retry_label == "重试生成更新日志"
    assert "无需重新发布" in log_failed.recovery


class _Commands:
    def __init__(self) -> None:
        self.calls = []

    def apply_decision(self, operation, context, **values):
        self.calls.append(("decision", operation, context.project_id, values))
        return SimpleNamespace(ref="draft-ref")

    def resolve_conflict(self, conflict, operation, context, **values):
        self.calls.append(("conflict", conflict, operation, context.variant_id, values))
        return SimpleNamespace(draft=SimpleNamespace(ref="resolved-draft"))

    def compare(self, version, context):
        self.calls.append(("compare", version, context.project_id))
        from transbridge.application.contracts import JobRef

        return JobRef("compare", context.owner_id, "compare-run")

    def restore(self, version, context):
        self.calls.append(("restore", version, context.variant_id))
        from transbridge.application.contracts import JobRef

        return JobRef("restore", context.owner_id, "restore-run")


def test_manual_conflict_compare_and_restore_delegate_to_production_command_boundary() -> None:
    commands = _Commands()
    presenter = TerminologyPresenter(
        TerminologyUiServices(commands=commands),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    conflict = object()
    version = object()

    assert presenter.add_term("Dragon", "巨龙").ref == "draft-ref"
    assert presenter.change_translation("term-1", "飞龙").ref == "draft-ref"
    assert presenter.set_suppressed("term-1", suppressed=True).ref == "draft-ref"
    assert (
        presenter.resolve_conflict(
            conflict,
            ConflictResolutionOperation.PLUGIN_EXCEPTION,
            translation="巨龙",
            plugin_id="Main.esm",
        ).draft.ref
        == "resolved-draft"
    )
    assert presenter.compare(version).run_id == "compare-run"
    assert presenter.restore(version).run_id == "restore-run"
    assert [call[0] for call in commands.calls] == [
        "decision",
        "decision",
        "decision",
        "conflict",
        "compare",
        "restore",
    ]

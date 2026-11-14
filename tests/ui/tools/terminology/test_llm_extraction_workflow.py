from copy import deepcopy
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import Diagnostic, JobRef, OperationResult, RequestContext
from transbridge.application.terminology.models import BuildCompleteness, BuildFreshness, LlmExtractionStatus
from transbridge.ui.tools.terminology.build_projection import build_completion_message, project_build, task_detail
from transbridge.ui.tools.terminology.extraction_options import ExtractionOptionsView
from transbridge.ui.tools.terminology.preflight_presentation import preflight_failed
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.task_adapter import TerminologyTaskViewState
from transbridge.ui.tools.terminology.window import TerminologyWindow
from transbridge.ui.tools.terminology.workflow_state import TerminologyWorkflowState

_APP = QApplication.instance() or QApplication([])


def _snapshot():
    return SimpleNamespace(
        project_id="project",
        variant_id="variant",
        project_revision=1,
        variant_revision=1,
        sources=(),
        effective_version_id=None,
        config_digest="c" * 64,
        extraction=SimpleNamespace(display_label="Test Provider / selected-model"),
    )


def test_build_and_preflight_merge_extraction_choices_without_losing_source_scope():
    captures, submissions = [], []
    snapshot = _snapshot()

    def capture(context, *, config):
        captures.append(deepcopy(config))
        config["source_ids"].append("mutation")
        return OperationResult.completed(snapshot)

    def submit(value, context):
        submissions.append(value)
        return JobRef("job", "operator", "run")

    presenter = TerminologyPresenter(
        TerminologyUiServices(
            build_inputs=SimpleNamespace(capture_build_input=capture), commands=SimpleNamespace(start_build=submit)
        ),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    presenter.set_source_scope(("selected-source",))
    state = presenter.preflight(config={"llm_enabled": True, "force_llm": False, "matching": "exact"})
    presenter.start_build(config={"llm_enabled": False, "force_llm": False})

    assert state.extraction_label == "使用模型：Test Provider / selected-model"
    assert submissions == [snapshot]
    assert captures == [
        {"source_ids": ["selected-source"], "matching": "exact", "llm_enabled": True, "force_llm": False},
        {"source_ids": ["selected-source"], "matching": "exact", "llm_enabled": False, "force_llm": False},
    ]


def test_rules_only_mode_disables_force_and_explains_that_text_is_not_extracted():
    view = ExtractionOptionsView()
    assert view.configuration() == {"llm_enabled": True, "force_llm": False}
    assert "无差异项默认采用" in view.description.text()
    view.force.setChecked(True)
    assert view.configuration()["force_llm"]
    view.mode.setCurrentIndex(1)
    assert view.configuration() == {"llm_enabled": False, "force_llm": False}
    assert not view.force.isEnabled()
    assert "不调用 LLM" in view.model.text()
    assert "不从对话或正文中识别术语" in view.description.text()
    view.close()


def test_window_captures_controls_before_scheduling_build_or_preflight(monkeypatch):
    presenter = TerminologyPresenter(
        TerminologyUiServices(), RequestContext("operator", project_id="project", variant_id="variant")
    )
    window = TerminologyWindow(presenter)
    queued, received = [], []
    try:
        monkeypatch.setattr(window._actions, "run", lambda command, *_args, **_kwargs: queued.append(command))
        monkeypatch.setattr(presenter, "preflight", lambda *, config_revision: received.append(config_revision))
        monkeypatch.setattr(presenter, "start_build", lambda *, config_revision: received.append(config_revision))
        window.run_preflight(repair=False)
        window._start_build()
        window.build_view.extraction_options.mode.blockSignals(True)
        window.build_view.extraction_options.mode.setCurrentIndex(1)
        for command in queued:
            command()
        assert received == [1, 2]
        assert presenter._build_config == {"llm_enabled": True, "force_llm": False}
    finally:
        window.close()


@pytest.mark.parametrize("status", [LlmExtractionStatus.PARTIAL, LlmExtractionStatus.UNAVAILABLE])
def test_llm_failure_is_not_reported_as_success_even_with_complete_sources(status):
    result = SimpleNamespace(
        summary=SimpleNamespace(source_count=2, candidate_count=2, conflict_count=0),
        completeness=BuildCompleteness.FULL,
        freshness=BuildFreshness.CURRENT,
        llm_status=status,
        candidates=(
            SimpleNamespace(extraction_method="deterministic_name"),
            SimpleNamespace(extraction_method="llm_text"),
        ),
        diagnostics=("LLM_BATCH_FAILED:TimeoutError", "LLM_PROPOSAL_NOT_LOCATED:e1"),
        ref=SimpleNamespace(build_key="build", content_digest="digest"),
    )
    state = project_build(result)
    assert state.is_partial
    assert "规则收集 1 个，LLM 抽取 1 个" in state.result
    assert "失败批次 1，过滤候选 1" in state.result
    assert "LLM 抽取未完整完成" in build_completion_message(state)
    assert "来源整理完成" not in build_completion_message(state)
    assert "LLM_BATCH_FAILED:TimeoutError" in state.technical_details[-1].value


def test_task_detail_exposes_llm_progress_instead_of_only_source_counts():
    from transbridge.application.tasks import JobState
    from transbridge.application.terminology.workloads import TerminologyWorkloadType

    state = TerminologyTaskViewState(
        "run",
        TerminologyWorkloadType.BUILD,
        JobState.RUNNING,
        "extract",
        2,
        5,
        "model",
        "执行中",
        1,
        1,
        llm_submitted=4,
        llm_completed=2,
        llm_waiting=2,
        llm_retries=1,
        llm_elapsed_ms=1500,
    )
    text = task_detail(state)
    assert "已提交 4，已完成 2，等待 2" in text
    assert "重试 1" in text and "1.5 秒" in text


def test_unready_llm_recovers_via_ai_settings_or_explicit_rules_only_mode():
    state = preflight_failed(
        RequestContext("operator", project_id="project", variant_id="variant"),
        Diagnostic("TERMINOLOGY_LLM_CONFIG_INVALID", "缺少模型配置。"),
    )
    assert not state.ready
    assert state.title == "LLM 抽取尚未就绪"
    assert "AI 服务" in state.message and "仅规则整理" in state.message
    assert "请检查项目来源" not in state.message


def test_partial_llm_draft_routes_to_retry_instead_of_publication():
    state = TerminologyWorkflowState.from_assets(
        SimpleNamespace(
            draft=SimpleNamespace(decisions=()),
            version_ref=None,
            review_counts=SimpleNamespace(review_required_terms=0, unresolved_conflicts=0),
            build=SimpleNamespace(completeness="full", freshness="current", llm_status="partial"),
        )
    )
    assert not state.can_publish and state.next_action == "build"
    assert "LLM" in state.title and "未完成批次" in state.detail


@pytest.mark.parametrize(
    "error,advice",
    [
        ("TimeoutError", "网络连接"),
        ("APIConnectionError", "AI 服务地址"),
        ("AuthenticationError", "API 密钥"),
        ("PermissionDeniedError", "模型访问权限"),
        ("RateLimitError", "降低 AI 请求并发"),
        ("StructuredOutputError", "结构化输出"),
    ],
)
def test_batch_failure_provides_actionable_recovery(error, advice):
    result = SimpleNamespace(
        summary=SimpleNamespace(source_count=1, candidate_count=0, conflict_count=0),
        completeness=BuildCompleteness.PARTIAL,
        freshness=BuildFreshness.CURRENT,
        llm_status=LlmExtractionStatus.PARTIAL,
        candidates=(),
        diagnostics=(f"LLM_BATCH_FAILED:2:{error}",),
        ref=SimpleNamespace(build_key="build", content_digest="digest"),
    )
    assert advice in project_build(result).next_action

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.tasks import JobState, TaskRuntime
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.tools.ai_translator import config_presenter as config_module
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.result_actions import AiResultNavigator, FailedSubsetRetryFactory
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.run_spec import (
    AiPreflightCode,
    capabilities_for,
    preflight_ai_run,
)
from transbridge.ui.tools.ai_translator.task_adapter import AiLegacyRunState, LegacyAiTaskAdapter


@dataclass
class Entry:
    id: str
    key: str = "key"
    translation: str = ""
    stage: int = 0


class _Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"global-ai-{self.value}"


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


class _WorkerControls:
    def __init__(self) -> None:
        self.events: list[str] = []

    def pause(self) -> None:
        self.events.append("pause")

    def resume(self) -> None:
        self.events.append("resume")

    def stop(self) -> None:
        self.events.append("stop")


def _config(**updates: object) -> SimpleNamespace:
    values = {"api_key": "secret", "model": "model", "provider": "openai_compatible", "max_concurrent": 3}
    values.update(updates)
    return SimpleNamespace(**values)


def test_preflight_explains_missing_credentials_dependency_and_scope() -> None:
    result = preflight_ai_run(
        "translate",
        _config(api_key="", model=""),
        [],
        esp_path=None,
        dependency_available=lambda _name: False,
    )

    assert result.ready is False
    assert {issue.code for issue in result.issues} == {
        AiPreflightCode.MISSING_API_KEY,
        AiPreflightCode.MISSING_MODEL,
        AiPreflightCode.MISSING_DEPENDENCY,
        AiPreflightCode.EMPTY_SCOPE,
        AiPreflightCode.MISSING_SOURCE,
    }
    assert all(issue.fix_intent.value for issue in result.issues)


def test_preflight_disabled_embedding_does_not_probe_vector_dependencies() -> None:
    probed: list[str] = []

    def available(name: str) -> bool:
        probed.append(name)
        return True

    result = preflight_ai_run(
        "translate",
        _config(
            retrieval_enabled=True,
            enable_semantic_match=True,
            embedding=SimpleNamespace(mode="disabled"),
        ),
        [object()],
        esp_path="plugin.esp",
        dependency_available=available,
    )

    assert result.ready is True
    assert probed == ["tiktoken"]


def test_preflight_local_embedding_reports_missing_vector_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from transbridge.infra import embedding_model_store as model_store_module

    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "modules.json").write_text("{}", encoding="utf-8")
    store = SimpleNamespace(installed_path=lambda _model_id: model_path)
    monkeypatch.setattr(model_store_module, "EmbeddingModelStore", lambda: store)
    result = preflight_ai_run(
        "translate",
        _config(
            retrieval_enabled=True,
            enable_semantic_match=True,
            embedding=SimpleNamespace(mode="local", local_model_id="managed-model"),
        ),
        [object()],
        esp_path="plugin.esp",
        dependency_available=lambda name: name == "tiktoken",
    )

    assert {issue.code for issue in result.issues} == {AiPreflightCode.MISSING_EMBEDDING_DEPENDENCY}
    assert "sentence-transformers" in result.reason
    assert "FAISS" in result.reason


def test_preflight_local_embedding_requires_an_installed_model() -> None:
    result = preflight_ai_run(
        "translate",
        _config(
            retrieval_enabled=True,
            enable_semantic_match=True,
            embedding=SimpleNamespace(mode="local", local_model_path=""),
        ),
        [object()],
        esp_path="plugin.esp",
        dependency_available=lambda _name: True,
    )

    assert [issue.code for issue in result.issues] == [AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION]
    assert "没有可用的本地向量模型" in result.reason


def test_preflight_api_embedding_reports_missing_model_and_endpoint() -> None:
    result = preflight_ai_run(
        "translate",
        _config(
            base_url="",
            retrieval_enabled=True,
            enable_semantic_match=True,
            embedding=SimpleNamespace(mode="api", provider="openai", api_key="embedding-key", model="", base_url=""),
        ),
        [object()],
        esp_path="plugin.esp",
        dependency_available=lambda _name: True,
    )

    assert [issue.code for issue in result.issues].count(AiPreflightCode.MISSING_EMBEDDING_CONFIGURATION) == 2
    assert "Embedding 模型名" in result.reason
    assert "Embedding Base URL" in result.reason


def test_run_request_has_global_identity_and_copy_on_read_config() -> None:
    saved = _config(model="before")
    controller = RunController(owner_id="window")
    request = controller.begin(
        "translate",
        saved,
        [Entry("one")],
        overwrite=True,
        esp_path="plugin.esp",
        project_id="project",
        variant_id="variant",
    )

    saved.model = "after"
    first = request.config
    first.model = "mutated-copy"

    assert request.run_id.startswith("ai-")
    assert request.generation == 1
    assert request.config.model == "before"
    assert request.spec.owner.project_id == "project"
    assert request.spec.overwrite is True
    assert request.spec.config_digest


def test_workload_capabilities_do_not_invent_recovery_or_retry() -> None:
    mixed = capabilities_for("mixed")
    translate = capabilities_for("translate")

    assert mixed.task_controls.supports_cancel is True
    assert mixed.task_controls.supports_pause is False
    assert translate.task_controls.supports_pause is True
    assert translate.task_controls.supports_resume is True
    assert translate.task_controls.supports_checkpoint is False
    assert translate.recover is False
    assert translate.retry_failed is False
    assert translate.open_global_log is False
    assert translate.open_global_result is False


def test_legacy_activity_requires_cancel_confirmation_and_rejects_late_updates() -> None:
    controller = RunController(owner_id="window")
    request = controller.begin("mixed", _config(), [Entry("one")])
    activity = LegacyAiTaskAdapter(request.spec)

    assert activity.progress(1, 2, "running") is True
    assert activity.finish(cancelled=True) is False
    assert activity.request_cancel() is True
    assert activity.activity.state is AiLegacyRunState.CANCELLING
    assert activity.finish(cancelled=True) is True
    assert activity.activity.state is AiLegacyRunState.CANCELLED
    assert activity.progress(2, 2, "late") is False


def test_legacy_adapter_projects_real_s03_activity_without_invented_evidence() -> None:
    controller = RunController(owner_id="window")
    request = controller.begin("translate", _config(), [Entry("one")], esp_path="plugin.esp")
    adapter = LegacyAiTaskAdapter(request.spec)
    adapter.progress(3, 10, "第三批")

    projected = adapter.task_activity

    assert projected.run_id == request.run_id
    assert dict(projected.progress) == {"current": 3, "total": 10, "message": "第三批"}
    assert projected.available_actions.pause is True
    assert projected.available_actions.cancel is True
    assert projected.available_actions.recover is False
    assert projected.available_actions.retry is False
    assert projected.available_actions.open_log is False
    assert projected.available_actions.open_result is False


def test_task_runtime_bridge_projects_immediately_and_forwards_real_controls() -> None:
    runtime = TaskRuntime(id_generator=_Ids(), clock=_Clock())
    controller = RunController(owner_id="window", task_runtime=runtime)
    request = controller.begin(
        "translate",
        _config(),
        [Entry("one")],
        esp_path="plugin.esp",
    )

    assert request.runtime_ref is not None
    assert request.run_id == "global-ai-1"
    assert runtime.get(request.runtime_ref, request.spec.owner).state is JobState.RUNNING

    adapter = controller.create_activity(request)
    worker = _WorkerControls()
    controller.attach(request.run_id, worker=worker, activity=adapter)
    assert adapter.task_activity.available_actions.pause is True
    assert adapter.task_activity.available_actions.cancel is True

    runtime.pause(request.runtime_ref, request.spec.owner)
    runtime.resume(request.runtime_ref, request.spec.owner)
    runtime.cancel(request.runtime_ref, request.spec.owner)
    assert worker.events[-3:] == ["pause", "resume", "stop"]
    assert adapter.finish(cancelled=True) is True
    assert runtime.get(request.runtime_ref, request.spec.owner).state is JobState.CANCELLED


def test_failed_subset_retry_repreflights_and_allocates_new_run_id() -> None:
    entries = [Entry("failed", translation="old"), Entry("ok", translation="old")]
    controller = RunController(owner_id="window")
    previous = controller.begin("polish", _config(), entries).spec
    controller.finish(previous.run_id)

    prepared = FailedSubsetRetryFactory().prepare(
        previous=previous,
        failed_entry_keys=("failed",),
        current_entries=entries,
        current_config=_config(model="new-model"),
        esp_path="plugin.esp",
        controller=controller,
    )

    assert prepared.preflight.ready is True
    assert prepared.request is not None
    assert prepared.request.run_id != previous.run_id
    assert [entry.id for entry in prepared.request.entries] == ["failed"]


def test_result_navigation_is_owner_scoped_and_uses_opaque_report_reference() -> None:
    controller = RunController(owner_id="owner")
    request = controller.begin("polish", _config(), [Entry("one", translation="old")])
    navigator = AiResultNavigator()
    report = Path(__file__)
    artifact = navigator.register_report(request.spec, str(report))

    assert artifact is not None
    assert "reports" not in artifact.artifact_id
    assert navigator.report_path(artifact, request.spec.owner) == str(report)
    foreign = request.spec.owner.__class__(owner_id="foreign", entrypoint="ui.ai-translator")
    assert navigator.report_path(artifact, foreign) is None
    assert navigator.entry_navigation(request.spec, "one", request.spec.owner) is not None
    assert navigator.entry_navigation(request.spec, "missing", request.spec.owner) is None


def test_run_controller_survives_100_owner_lifecycles_without_accepting_late_callbacks() -> None:
    observed: list[int] = []
    for index in range(100):
        controller = RunController(owner_id=f"window-{index}")
        request = controller.begin("polish", _config(), [Entry(str(index), translation="old")])
        guarded = controller.guard(request.run_id, observed.append)
        controller.close()
        guarded(index)

    assert observed == []


def test_single_ai_window_uses_visible_four_page_task_configuration(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: LLMConfig())
    ctx = SimpleNamespace(
        slots={},
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    workbench = SimpleNamespace(filtered_entries=lambda: (), locate_entry=lambda _entry_id: None)

    window = AITranslatorWindow(ctx, workbench)

    window.show()
    app.processEvents()
    assert [window._view.controls.tabs.tabText(index) for index in range(window._view.controls.tabs.count())] == [
        "基础配置",
        "术语库",
        "质量处理",
        "运行参数",
    ]
    assert window._view.controls.tabs.isVisible()
    assert window._view.controls.start_btn.isEnabled() is False
    assert window._view.controls.preflight_label.text()
    assert not window._view.controls.advanced_btn.isVisible()
    window.close()
    app.processEvents()


def test_closing_missing_local_model_guide_persists_disabled_mode(monkeypatch) -> None:
    from transbridge.ui.tools.ai_translator import embedding_model_dialog as dialog_module

    observed_modes: list[str] = []

    class _Guide:
        decision = "disable"

        def __init__(self, parent) -> None:
            self._parent = parent

        def exec(self) -> int:
            observed_modes.append(str(self._parent._view.controls.embed_provider_combo.currentData()))
            return 0

    app = QApplication.instance() or QApplication([])
    config = LLMConfig()
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: config)
    monkeypatch.setattr(dialog_module, "LocalEmbeddingGuideDialog", _Guide)
    ctx = SimpleNamespace(
        slots={},
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    workbench = SimpleNamespace(filtered_entries=lambda: (), locate_entry=lambda _entry_id: None)
    window = AITranslatorWindow(ctx, workbench)
    local_index = window._view.controls.embed_provider_combo.findData("local")
    window._view.controls.embed_provider_combo.setCurrentIndex(local_index)

    window._embedding_models.on_mode_activated()

    assert window._view.controls.embed_provider_combo.currentData() == "disabled"
    assert window._config_presenter.build().embedding.mode == "disabled"
    assert observed_modes == ["disabled"]
    window.close()
    app.processEvents()


def test_default_ai_entry_skips_target_dialog_and_binds_current_content(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: LLMConfig())
    slot = SimpleNamespace(label="Plugin", collection=[], esp_path="Plugin.esp")
    ctx = SimpleNamespace(
        slots={"active": slot},
        active_slot=slot,
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    workbench = SimpleNamespace(
        filtered_entries=lambda: (),
        selected_row_entry_ids=lambda: ("selected-entry",),
        locate_entry=lambda _entry_id: None,
    )

    window = AITranslatorWindow.open_for_translation(ctx, workbench)

    assert isinstance(window, AITranslatorWindow)
    assert window._ctx is ctx
    assert window._scope_presenter.state.preset == "selection"
    assert window._scope_presenter.state.selected_entry_ids == frozenset({"selected-entry"})
    assert window._view.controls.preset_selection.text() == "当前选择 1"
    window.close()
    app.processEvents()


def test_single_ai_window_has_no_visible_legacy_batch_entry(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: LLMConfig())
    ctx = SimpleNamespace(
        slots={},
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    workbench = SimpleNamespace(filtered_entries=lambda: (), locate_entry=lambda _entry_id: None)
    window = AITranslatorWindow(ctx, workbench)
    window.show()
    app.processEvents()
    assert not window._view.controls.batch_btn.isVisible()
    assert not window._view.controls.advanced_btn.isVisible()
    app.processEvents()
    window.close()


def test_ai_naming_scheme_creation_routes_to_the_project_terminology_workbench(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: LLMConfig())
    ctx = SimpleNamespace(
        slots={},
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    workbench = SimpleNamespace(filtered_entries=lambda: (), locate_entry=lambda _entry_id: None)
    opened = []
    window = AITranslatorWindow(
        ctx,
        workbench,
        terminology_workbench_requested=lambda: opened.append(True),
    )

    assert window._view.controls.save_term_source_as_scheme_btn.isEnabled()
    window._view.controls.save_term_source_as_scheme_btn.click()

    assert opened == [True]
    window.close()
    app.processEvents()


def test_translation_handoff_starts_worker_then_reactivates_progress_deferred() -> None:
    source = Path("src/transbridge/ui/tools/ai_translator/run_controller.py").read_text(encoding="utf-8")
    function = source[source.index("def start_translation_run(") : source.index("def start_mixed_run(")]

    assert function.index("progress_created(progress)") < function.index("worker.start()")
    assert function.index("worker.start()") < function.index("show_and_activate(progress, deferred=True)")

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.converter.translation_entry import STAGE_HIDDEN, STAGE_LOCKED, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import config_presenter as config_module
from transbridge.ui.tools.ai_translator._mixed_worker import _MixedWorker
from transbridge.ui.tools.ai_translator._translation_progress_window import (
    _TranslationProgressWindow,
)
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
from transbridge.ui.tools.ai_translator.result_presenter import ResultPresenter
from transbridge.ui.tools.ai_translator.result_view import show_polish_report
from transbridge.ui.tools.ai_translator.run_controller import (
    RunAlreadyActiveError,
    RunController,
    show_polish_progress,
    start_mixed_run,
)
from transbridge.ui.tools.ai_translator.scope_presenter import ScopePresenter, Step2ScopeAdapter
from transbridge.ui.tools.ai_translator.term_source_inspector import TermSourceInspector


@dataclass
class Entry:
    id: str
    key: str = "key"
    original: str = "source"
    translation: str = ""
    stage: int = 0
    context: str = ""
    form_id_with_plugin: str | None = None
    string_id: int | None = None
    dsd_type: str | None = None
    dsd_index: int | None = None
    editor_id: str | None = None


def _run_config(max_concurrent: object = 2) -> SimpleNamespace:
    return SimpleNamespace(max_concurrent=max_concurrent)


class WorkbenchPort:
    def __init__(self, entries=(), selected_ids=()) -> None:
        self.entries = tuple(entries)
        self.selected_ids = tuple(selected_ids)
        self.located: list[str] = []

    def filtered_entries(self):
        return self.entries

    def selected_entry_ids(self):
        return self.selected_ids

    def locate_entry(self, entry_id: str) -> None:
        self.located.append(entry_id)


def test_scope_presenter_uses_public_workbench_snapshot_and_filters_locked_entries() -> None:
    visible = [Entry("visible"), Entry("locked", stage=STAGE_LOCKED)]
    port = WorkbenchPort(visible)
    presenter = ScopePresenter(
        collection_provider=lambda: [Entry("collection")],
        label_projection_provider=lambda: {},
        category_of=lambda _entry: "其他",
        workbench=port,
    )

    presenter.select_preset("table_view")

    assert [entry.id for entry in presenter.candidates()] == ["visible"]
    presenter.locate_entry("visible")
    assert port.located == ["visible"]


def test_scope_presenter_selection_preset_uses_exact_selected_ids() -> None:
    selected = Entry("selected")
    entries = [Entry("other"), selected, Entry("locked", stage=STAGE_LOCKED)]
    presenter = ScopePresenter(
        collection_provider=lambda: entries,
        label_projection_provider=lambda: {},
        category_of=lambda _entry: "其他",
        workbench=WorkbenchPort(selected_ids=("selected", "locked")),
    )

    presenter.select_preset("selection")

    assert presenter.state.selected_entry_ids == frozenset({"selected", "locked"})
    assert presenter.candidates() == [selected]


def test_scope_presenter_combines_dimensions_without_copying_entries() -> None:
    keep = Entry("keep", stage=1, context="NPC_:FULL")
    entries = [keep, Entry("label-miss", stage=1), Entry("hidden", stage=STAGE_HIDDEN)]
    presenter = ScopePresenter(
        collection_provider=lambda: entries,
        label_projection_provider=lambda: {"keep": {"review"}},
        category_of=lambda entry: "人名" if entry.context == "NPC_:FULL" else "其他",
        workbench=WorkbenchPort(),
    )
    presenter.toggle_stage(1)
    presenter.toggle_label("review")
    presenter.toggle_category("人名")

    assert presenter.candidates() == [keep]
    assert presenter.candidates()[0] is keep


def test_scope_estimate_exposes_request_tokens_oversized_and_shared_concurrency() -> None:
    entries = [
        TranslationEntry("one", "one", "short", "", 0, "NPC_:FULL"),
        TranslationEntry("long", "long", "x" * 100, "", 0, "NPC_:FULL"),
    ]
    presenter = ScopePresenter(
        collection_provider=lambda: entries,
        label_projection_provider=lambda: {},
        category_of=lambda _entry: "人名",
        workbench=WorkbenchPort(),
    )
    presenter.reset_default(polish=False)

    estimate = presenter.estimate(
        mode="translate",
        rules=None,
        overwrite=False,
        max_tokens=20,
        model="unknown-compatible-model",
        max_concurrent=7,
    )

    assert "2 条 / 1 个请求" in estimate.text
    assert "内容 Token 平均" in estimate.text
    assert "共享并发 7" in estimate.text
    assert "超限 1 条" in estimate.text


def test_step2_adapter_prefers_public_filtered_entries() -> None:
    step2 = SimpleNamespace(
        filtered_entries=lambda: (Entry("one"),),
        selected_row_entry_ids=lambda: ("one",),
        locate_entry=lambda _entry_id: None,
    )
    adapter = Step2ScopeAdapter(step2)

    assert [entry.id for entry in adapter.filtered_entries()] == ["one"]
    assert adapter.selected_entry_ids() == ("one",)
    adapter.locate_entry("one")


def test_run_controller_rejects_reentry_and_releases_terminal_resources() -> None:
    class Worker:
        cancelled = 0

        def cancel(self) -> None:
            self.cancelled += 1

    class Progress:
        closed = 0

        def close(self) -> None:
            self.closed += 1

    controller = RunController(owner_id="window")
    first = controller.begin("translate", _run_config(), [Entry("one")])
    observed: list[str] = []
    worker = Worker()
    progress = Progress()
    controller.attach(first.run_id, worker=worker, progress=progress)

    with pytest.raises(RunAlreadyActiveError):
        controller.begin("polish", _run_config(), [Entry("two")])

    controller.terminal_guard(first.run_id, observed.append)("done")
    second = controller.begin("polish", _run_config(), [Entry("two")])
    controller.guard(first.run_id, observed.append)("late")
    controller.close()
    controller.guard(second.run_id, observed.append)("closed")

    assert observed == ["done"]
    assert progress.closed == 1


def test_run_controller_owns_one_frozen_request_budget_per_run() -> None:
    config = _run_config(max_concurrent=3)
    controller = RunController(owner_id="window")

    first = controller.begin("translate", config, [Entry("one")])
    first_budget = first.request_budget
    first_copy = first.config
    second_copy = first.config
    config.max_concurrent = 7

    assert first_budget.max_in_flight == 3
    assert first.request_budget is first_budget
    assert first_copy is not second_copy
    first_copy.max_concurrent = 99
    assert first.request_budget is first_budget
    assert first.request_budget.max_in_flight == 3
    assert second_copy.max_concurrent == 3

    controller.finish(first.run_id)
    second = controller.begin("translate", config, [Entry("two")])

    assert second.request_budget is not first_budget
    assert second.request_budget.max_in_flight == 7


@pytest.mark.parametrize("max_concurrent", [0, -1, None, "3", 1.5, True])
def test_run_controller_rejects_invalid_max_concurrent(max_concurrent: object) -> None:
    controller = RunController(owner_id="window")

    with pytest.raises(ValueError, match=r"config\.max_concurrent must be a positive integer"):
        controller.begin("translate", _run_config(max_concurrent), [Entry("one")])

    assert controller.active_request is None


def test_run_controller_rejects_missing_max_concurrent() -> None:
    controller = RunController(owner_id="window")

    with pytest.raises(ValueError, match=r"config\.max_concurrent must be a positive integer"):
        controller.begin("translate", object(), [Entry("one")])

    assert controller.active_request is None


def test_run_controller_close_cancels_worker_and_closes_progress() -> None:
    worker = SimpleNamespace(cancelled=0)
    worker.cancel = lambda: setattr(worker, "cancelled", worker.cancelled + 1)
    progress = SimpleNamespace(closed=0)
    progress.close = lambda: setattr(progress, "closed", progress.closed + 1)
    controller = RunController()
    request = controller.begin("mixed", _run_config(), [])
    controller.attach(request.run_id, worker=worker, progress=progress)

    controller.close()

    assert worker.cancelled == 1
    assert progress.closed == 1
    assert controller.is_running is False


def test_run_controller_releases_destroyed_qt_resources_before_close() -> None:
    app = QApplication.instance() or QApplication([])
    worker = QThread()
    progress = QWidget()
    controller = RunController()
    request = controller.begin("mixed", _run_config(), [])
    controller.attach(request.run_id, worker=worker, progress=progress)

    worker.deleteLater()
    progress.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

    assert sip.isdeleted(worker)
    assert sip.isdeleted(progress)
    assert controller.is_running is False
    controller.close()


def test_mixed_cancelled_signal_releases_active_run_and_allows_restart(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])

    class Worker(QObject):
        progress = pyqtSignal(object)
        log = pyqtSignal(str)
        finished = pyqtSignal(object)
        error = pyqtSignal(str)
        cancelled = pyqtSignal()

        progress_stages = (("translate", "翻译"),)
        execution_order = "serial"
        stream_log_dir = ""
        stream_log_error = ""

        def __init__(self, **_kwargs) -> None:
            super().__init__()
            self.cancel_calls = 0

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            self.cancel_calls += 1

        def isRunning(self) -> bool:  # noqa: N802 - Qt compatibility
            return False

    from transbridge.ui.tools.ai_translator import _mixed_worker as worker_module, run_controller as controller_module

    monkeypatch.setattr(worker_module, "_MixedWorker", Worker)
    monkeypatch.setattr(controller_module, "show_and_activate", lambda *_args, **_kwargs: None)
    config = SimpleNamespace(
        api_key="secret",
        model="model",
        provider="openai_compatible",
        max_concurrent=2,
        mixed_execution_order="serial",
    )
    controller = RunController(owner_id="window")
    request = controller.begin("mixed", config, [Entry("one")])
    finished = []
    cancelled = []
    progress = start_mixed_run(
        controller,
        request,
        SimpleNamespace(),
        config,
        [Entry("one")],
        [],
        finished=finished.append,
        error=lambda _message: None,
        cancelled=lambda: cancelled.append(True),
    )

    progress._request_stop()
    progress._worker.cancelled.emit()
    app.processEvents()

    assert controller.active_request is None
    assert cancelled == [True]
    assert finished == []
    restarted = controller.begin("mixed", config, [Entry("two")])
    assert restarted.run_id != request.run_id
    controller.finish(restarted.run_id)


def test_polish_finished_after_cancel_rolls_back_without_publishing_results(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])

    class Worker(QObject):
        detailed_progress = pyqtSignal(object)
        progress = pyqtSignal(int, int, str)
        log = pyqtSignal(str)
        finished_all = pyqtSignal(object)
        finished = pyqtSignal()
        error = pyqtSignal(str)

        stream_log_dir = ""
        stream_log_error = ""

        def __init__(self) -> None:
            super().__init__()
            self.cancel_calls = 0

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            self.cancel_calls += 1

        def isRunning(self) -> bool:  # noqa: N802 - Qt compatibility
            return False

    from transbridge.ui.tools.ai_translator import workflow_progress_runtime

    monkeypatch.setattr(workflow_progress_runtime, "show_and_activate", lambda *_args, **_kwargs: None)
    controller = RunController(owner_id="window")
    request = controller.begin("polish", _run_config(), [Entry("one")])
    worker = Worker()
    published = []
    rolled_back = []
    progress = show_polish_progress(
        controller,
        request,
        QWidget(),
        worker,
        [Entry("one")],
        on_results=published.append,
        on_aborted=lambda: rolled_back.append(True),
        preview=False,
    )

    progress._request_stop()
    worker.finished_all.emit({"one": object()})
    app.processEvents()

    assert worker.cancel_calls == 1
    assert rolled_back == [True]
    assert published == []
    assert controller.active_request is None
    progress.close()
    worker.deleteLater()


def test_mixed_translate_uses_its_cancel_event(monkeypatch, tmp_path) -> None:
    observed = {}
    constructor_options = {}
    progress_updates = []
    logs = []

    class Translator:
        def __init__(self, _config, **kwargs) -> None:
            constructor_options.update(kwargs)

        def translate(self, **kwargs):
            observed.update(kwargs)
            kwargs["stage_progress_callback"]("terms", 2, 5, "已完成术语抽取 2/5 批，本批新增候选 3 个")
            kwargs["progress_callback"](1, 2, "已完成第一批", 1, 0, 3)
            kwargs["log_callback"](1, "批次响应完成")
            kwargs["stream_callback"](1, "raw batch response")
            return SimpleNamespace(success_count=0, failed_count=0)

    import transbridge.ai_translator.translator as translator_module

    monkeypatch.setattr(translator_module, "AutoTranslator", Translator)
    monkeypatch.setattr(translator_module, "TranslatorConfig", lambda **kwargs: kwargs)
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.workflow_log_store.ParatranzConfig.get_data_dir",
        lambda: str(tmp_path),
    )
    ctx = SimpleNamespace(collection=[Entry("one")], esp_path="plugin.esp")
    worker = _MixedWorker(object(), [Entry("one")], [], ctx=ctx)
    worker.progress.connect(progress_updates.append)
    worker.log.connect(logs.append)

    worker._do_translate()
    worker._log_store.close()

    assert observed["stop_event"] is worker._cancelled
    assert observed["pause_event"] is worker._pause_event
    assert callable(constructor_options["llm_client_wrapper"])
    assert callable(constructor_options["term_llm_client_wrapper"])
    assert worker.progress_stages[:2] == (("terms", "术语抽取"), ("translate", "翻译"))
    assert [update.stage for update in progress_updates] == ["terms", "translate"]
    assert progress_updates[0].current == 2
    assert progress_updates[0].total == 5
    assert progress_updates[0].success == 2
    assert progress_updates[0].failed == 0
    assert progress_updates[0].pending == 3
    assert progress_updates[0].new_terms == 3
    assert progress_updates[1].current == 1
    assert progress_updates[1].new_terms == 3
    assert logs == ["[翻译批次 1] 批次响应完成"]
    assert (Path(worker.stream_log_dir) / "stage_terms.log").read_text(encoding="utf-8") == (
        "2/5 已完成术语抽取 2/5 批，本批新增候选 3 个\n"
    )
    assert (Path(worker.stream_log_dir) / "batch_001.log").read_text(encoding="utf-8") == "raw batch response"


def test_mixed_term_failure_closes_pending_stage_stats(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.workflow_log_store.ParatranzConfig.get_data_dir",
        lambda: str(tmp_path),
    )
    worker = _MixedWorker(object(), [Entry("one")], [], ctx=SimpleNamespace(esp_path="plugin.esp"))
    updates = []
    worker.progress.connect(updates.append)

    worker._on_translate_stage_progress(
        "terms",
        5,
        5,
        "第 2/5 批术语抽取失败，已停止后续批次：等待 Provider 超时；翻译将继续",
    )
    worker._log_store.close()

    assert updates[-1].current == 5
    assert updates[-1].success == 0
    assert updates[-1].failed == 5
    assert updates[-1].pending == 0


def test_translation_progress_window_consumes_term_stage_signal() -> None:
    class Worker(QObject):
        progress = pyqtSignal(int, int, str, int, int, int)
        stage_progress = pyqtSignal(str, int, int, str)
        log = pyqtSignal(int, str)
        result = pyqtSignal(object)
        error = pyqtSignal(str)
        finished = pyqtSignal()

        stream_log_dir = ""
        is_paused = False

        def isRunning(self) -> bool:  # noqa: N802 - Qt compatibility
            return False

    app = QApplication.instance() or QApplication([])
    worker = Worker()
    window = _TranslationProgressWindow(worker, SimpleNamespace())

    worker.stage_progress.emit("terms", 3, 8, "正在处理第 3 批")
    app.processEvents()

    assert (window._total_progress_bar.maximum(), window._total_progress_bar.value()) == (8, 3)
    assert window._total_progress_lbl.text() == "3 / 8"
    assert window._progress_msg.full_text == "术语抽取：正在处理第 3 批"

    worker.progress.emit(1, 4, "正在翻译第 1 批", 1, 0, 2)
    app.processEvents()

    assert (window._total_progress_bar.maximum(), window._total_progress_bar.value()) == (4, 1)
    assert window._total_progress_lbl.text() == "1 / 4"
    assert window._progress_msg.full_text == "正在翻译第 1 批"
    assert window._lbl_terms.full_text == "新增术语: 2"

    window.close()


def test_result_presenter_separates_pending_from_failed_polish_results() -> None:
    polish = SimpleNamespace(
        success_count=1,
        failed_count=1,
        pending_count=1,
        details=(
            {"key": "accepted", "success": True, "verdict": "pass", "error": ""},
            {"key": "pending", "success": False, "verdict": "pending", "error": "needs review"},
            {"key": "rejected", "success": False, "verdict": "reject", "error": "bad output"},
        ),
    )

    summary = ResultPresenter.mixed_summary({"polish": polish})

    assert "润色: 成功 1, 失败 1，待审 1" in summary
    assert "润色失败条目 (1):" in summary
    assert "rejected" in summary
    assert "pending" not in summary


def test_result_presenter_commits_only_accepted_polish_results() -> None:
    class Collection:
        def __init__(self) -> None:
            self.updated: list[Entry] = []

        def add(self, entry: Entry, *, overwrite: bool) -> None:
            assert overwrite is True
            self.updated.append(entry)

    collection = Collection()
    entries = [Entry("accepted", translation="old"), Entry("rejected", translation="old")]

    summary = ResultPresenter().apply_decisions(
        collection,
        entries,
        {"accepted": "new", "rejected": None},
    )

    assert summary.accepted == 1
    assert summary.rejected == 1
    assert summary.accepted_entry_ids == ("accepted",)
    assert summary.rejected_entry_ids == ("rejected",)
    assert summary.failed_entry_ids == ()
    assert [(entry.id, entry.translation) for entry in collection.updated] == [("accepted", "new")]


def test_result_presenter_preserves_v2_identity_envelope() -> None:
    class Collection:
        def __init__(self) -> None:
            self.updated = []

        def add(self, entry, *, overwrite: bool) -> None:
            self.updated.append((entry, overwrite))

    collection = Collection()
    entry = TranslationEntry(
        id="legacy",
        key="key",
        original="source",
        translation="old",
        stage=1,
        context="NPC_:FULL",
        metadata=(("owner", "test"),),
    )

    ResultPresenter().apply_decisions(collection, [entry], {entry.id: "new"})

    updated, overwrite = collection.updated[0]
    assert overwrite is True
    assert updated.translation == "new"
    assert updated.identity == entry.identity
    assert updated.metadata == entry.metadata


def test_config_presenter_keeps_persistence_out_of_view(monkeypatch) -> None:
    class Config:
        saved = 0

        @classmethod
        def load_from_file(cls):
            return cls()

        def save_to_file(self) -> None:
            type(self).saved += 1

    class View:
        rendered = None

        def render_config(self, config) -> None:
            self.rendered = config

        def update_config(self, config):
            config.model = "model"
            return config

    monkeypatch.setattr(config_module, "LLMConfig", Config)
    view = View()
    presenter = ConfigPresenter(view)

    assert presenter.load() is view.rendered
    assert presenter.build().model == "model"
    assert presenter.save().model == "model"
    assert Config.saved == 1


def test_term_column_conversion_and_facade_dependency_boundary() -> None:
    assert TermSourceInspector.column_index("A") == 0
    assert TermSourceInspector.column_index("AA") == 26

    facade_path = Path("src/transbridge/ui/tools/ai_translator/ai_translator_window.py")
    facade = facade_path.read_text(encoding="utf-8")
    assert "_find_main_window" not in facade
    assert "from transbridge.ui.workbench.step2 import _" not in facade
    assert "._table" not in facade
    assert len(facade.splitlines()) <= 500
    assert "self._view._" not in facade
    tree = ast.parse(facade)
    window_class = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert sum(isinstance(node, ast.FunctionDef) for node in window_class.body) <= 30
    for progress_name in ("_translation_progress_window.py", "task_progress.py"):
        progress_source = facade_path.with_name(progress_name).read_text(encoding="utf-8")
        assert "_find_main_window" not in progress_source
    for view_name in ("config_view.py", "scope_view.py", "postprocess_view.py"):
        view_source = facade_path.with_name(view_name).read_text(encoding="utf-8")
        assert "from typing import Any" not in view_source
        assert "apply_rules" not in view_source
        assert "BatchPlanner" not in view_source


def test_facade_constructs_and_keeps_four_mode_entrypoints(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: LLMConfig())
    collection = TranslationEntryCollection([TranslationEntry("entry", "entry", "Source", "", 0, "NPC_:FULL")])
    slot = CollectionSlot("Plugin", collection, esp_path="Plugin.esp")
    ctx = SimpleNamespace(
        collection=collection,
        esp_path=slot.esp_path,
        current_project=None,
        label_library={},
        entry_labels={},
        slots={"plugin": slot},
        active_slot=slot,
    )
    step2 = WorkbenchPort()

    window = AITranslatorWindow(ctx, step2)
    controls = window._view.controls
    controls.mode_polish.click()
    assert window._view_port.selected_mode == "polish"
    controls.mode_mixed.click()
    assert window._view_port.selected_mode == "mixed"
    controls.mode_custom.click()
    assert window._view_port.selected_mode == "custom"
    assert not controls.custom_profile_group.isHidden()
    controls.mode_translate.click()
    assert window._view_port.selected_mode == "translate"
    assert controls.start_btn.text() == "开始 AI 翻译"

    window.close()
    app.processEvents()


def test_non_modal_translation_reports_are_parent_owned(monkeypatch) -> None:
    from transbridge.ui.tools.ai_translator import _translation_report_dialog as report_module

    app = QApplication.instance() or QApplication([])

    class Signal:
        def connect(self, _callback) -> None:
            pass

    class ReportDialog(QWidget):
        def __init__(self, *args, parent=None, **kwargs) -> None:
            super().__init__(parent)
            self.entry_activated = Signal()

    monkeypatch.setattr(report_module, "_TranslationReportDialog", ReportDialog)

    single_owner = QWidget()
    single_owner._entry_activated = None
    _TranslationProgressWindow._show_report_dialog(
        single_owner,
        SimpleNamespace(),
        None,
    )

    single_report = single_owner._report_dialog
    assert single_report.parent() is single_owner

    single_owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert sip.isdeleted(single_report)


def test_polish_report_activation_is_deferred_until_config_callback_returns(monkeypatch) -> None:
    from transbridge.ui.tools.ai_translator import _translation_report_dialog as report_module
    from transbridge.ui.tools.ai_translator.result_presenter import PolishApplySummary

    class Signal:
        def connect(self, _callback) -> None:
            pass

    class ReportDialog(QWidget):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__()
            self.entry_activated = Signal()

        def set_report_render_result(self, _artifacts) -> None:
            pass

        def set_report_render_error(self, _message) -> None:
            pass

    class Presenter:
        def build_polish_report(self, *args, **kwargs):
            return SimpleNamespace(snapshot=None, report_path=None)

    monkeypatch.setattr(report_module, "_TranslationReportDialog", ReportDialog)
    app = QApplication.instance() or QApplication([])

    dialog = show_polish_report(
        Presenter(),
        {},
        [],
        PolishApplySummary(0, 0, 0),
        config=SimpleNamespace(pp_polish_level="moderate"),
        esp_path=None,
        entry_activated=lambda _entry_id: None,
    )

    assert not dialog.isVisible()
    app.processEvents()
    assert dialog.isVisible()
    dialog.close()

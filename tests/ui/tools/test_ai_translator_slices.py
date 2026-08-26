from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from transbridge.converter.translation_entry import STAGE_HIDDEN, STAGE_LOCKED, TranslationEntry
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.tools.ai_translator import config_presenter as config_module
from transbridge.ui.tools.ai_translator._batch_translation_progress_window import (
    _BatchTranslationProgressWindow,
)
from transbridge.ui.tools.ai_translator._mixed_worker import _MixedWorker
from transbridge.ui.tools.ai_translator._translation_progress_window import (
    _TranslationProgressWindow,
)
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.batch_runtime import TermSourceInspector
from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
from transbridge.ui.tools.ai_translator.result_presenter import ResultPresenter
from transbridge.ui.tools.ai_translator.result_view import show_polish_report
from transbridge.ui.tools.ai_translator.run_controller import (
    RunAlreadyActiveError,
    RunController,
)
from transbridge.ui.tools.ai_translator.scope_presenter import ScopePresenter, Step2ScopeAdapter


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


class WorkbenchPort:
    def __init__(self, entries=()) -> None:
        self.entries = tuple(entries)
        self.located: list[str] = []

    def filtered_entries(self):
        return self.entries

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


def test_step2_adapter_prefers_public_filtered_entries() -> None:
    step2 = WorkbenchPort([Entry("one")])
    adapter = Step2ScopeAdapter(step2)

    assert [entry.id for entry in adapter.filtered_entries()] == ["one"]
    adapter.locate_entry("one")
    assert step2.located == ["one"]


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
    first = controller.begin("translate", object(), [Entry("one")])
    observed: list[str] = []
    worker = Worker()
    progress = Progress()
    controller.attach(first.run_id, worker=worker, progress=progress)

    with pytest.raises(RunAlreadyActiveError):
        controller.begin("polish", object(), [Entry("two")])

    controller.terminal_guard(first.run_id, observed.append)("done")
    second = controller.begin("polish", object(), [Entry("two")])
    controller.guard(first.run_id, observed.append)("late")
    controller.close()
    controller.guard(second.run_id, observed.append)("closed")

    assert observed == ["done"]
    assert progress.closed == 1


def test_run_controller_close_cancels_worker_and_closes_progress() -> None:
    worker = SimpleNamespace(cancelled=0)
    worker.cancel = lambda: setattr(worker, "cancelled", worker.cancelled + 1)
    progress = SimpleNamespace(closed=0)
    progress.close = lambda: setattr(progress, "closed", progress.closed + 1)
    controller = RunController()
    request = controller.begin("mixed", object(), [])
    controller.attach(request.run_id, worker=worker, progress=progress)

    controller.close()

    assert worker.cancelled == 1
    assert progress.closed == 1
    assert controller.is_running is False


def test_mixed_translate_uses_its_cancel_event(monkeypatch) -> None:
    observed = {}

    class Translator:
        def __init__(self, _config, **_kwargs) -> None:
            pass

        def translate(self, **kwargs):
            observed.update(kwargs)
            return SimpleNamespace(success_count=0, failed_count=0)

    import transbridge.ai_translator.translator as translator_module

    monkeypatch.setattr(translator_module, "AutoTranslator", Translator)
    monkeypatch.setattr(translator_module, "TranslatorConfig", lambda **kwargs: kwargs)
    ctx = SimpleNamespace(collection=[Entry("one")], esp_path="plugin.esp")
    worker = _MixedWorker(object(), [Entry("one")], [], ctx=ctx)

    worker._do_translate()

    assert observed["stop_event"] is worker._cancelled


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
    for progress_name in ("_translation_progress_window.py", "_batch_translation_progress_window.py"):
        progress_source = facade_path.with_name(progress_name).read_text(encoding="utf-8")
        assert "_find_main_window" not in progress_source
    for view_name in ("config_view.py", "scope_view.py", "postprocess_view.py"):
        view_source = facade_path.with_name(view_name).read_text(encoding="utf-8")
        assert "from typing import Any" not in view_source
        assert "apply_rules" not in view_source
        assert "BatchPlanner" not in view_source


def test_facade_constructs_and_keeps_three_mode_entrypoints(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: LLMConfig())
    ctx = SimpleNamespace(
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    step2 = WorkbenchPort()

    window = AITranslatorWindow(ctx, step2)
    controls = window._view.controls
    controls.mode_polish.click()
    assert controls.start_btn.text() == "▶ 开始润色"
    controls.mode_mixed.click()
    assert controls.start_btn.text() == "▶ 开始执行"
    controls.mode_translate.click()
    assert controls.start_btn.text() == "▶ 开始翻译"

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

    batch_owner = QWidget()
    batch_owner._entry_activated = None
    _BatchTranslationProgressWindow._show_plugin_report(
        batch_owner,
        {"result": SimpleNamespace(), "report_path": None},
    )

    single_report = single_owner._report_dialog
    batch_report = batch_owner._report_dialog
    assert single_report.parent() is single_owner
    assert batch_report.parent() is batch_owner

    single_owner.deleteLater()
    batch_owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert sip.isdeleted(single_report)
    assert sip.isdeleted(batch_report)


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

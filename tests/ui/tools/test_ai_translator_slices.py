from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.tools.ai_runtime_support import ai_task_runtime
from transbridge.converter.translation_entry import STAGE_HIDDEN, STAGE_LOCKED, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import config_presenter as config_module
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
from transbridge.ui.tools.ai_translator.result_presenter import ResultPresenter
from transbridge.ui.tools.ai_translator.run_controller import (
    RunAlreadyActiveError,
    RunController,
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


def test_scope_estimate_counts_oversized_single_request_and_shared_concurrency() -> None:
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

    assert "2 条 / 2 个请求" in estimate.text
    assert "内容 Token 平均" in estimate.text
    assert "共享并发 7" in estimate.text
    assert "超限" not in estimate.text


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


def test_run_controller_owns_one_frozen_request_budget_per_run() -> None:
    config = _run_config(max_concurrent=3)
    controller = RunController(task_runtime=ai_task_runtime(), owner_id="window")

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
    controller = RunController(task_runtime=ai_task_runtime(), owner_id="window")

    with pytest.raises(ValueError, match=r"config\.max_concurrent must be a positive integer"):
        controller.begin("translate", _run_config(max_concurrent), [Entry("one")])

    assert controller.active_request is None


def test_run_controller_rejects_missing_max_concurrent() -> None:
    controller = RunController(task_runtime=ai_task_runtime(), owner_id="window")

    with pytest.raises(ValueError, match=r"config\.max_concurrent must be a positive integer"):
        controller.begin("translate", object(), [Entry("one")])

    assert controller.active_request is None


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
    for progress_name in ("task_progress.py",):
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


def test_launch_owner_rejects_reentry_and_closes_unpublished_runtime_job():
    from transbridge.application.tasks import JobState

    runtime = ai_task_runtime()
    controller = RunController(task_runtime=runtime)
    request = controller.begin("mixed", _run_config(), [])
    with pytest.raises(RunAlreadyActiveError):
        controller.begin("mixed", _run_config(), [])
    controller.close()
    assert not controller.accepts(request.run_id)
    assert not controller.is_running
    assert runtime.get(request.runtime_ref, request.spec.owner).state is JobState.CANCELLED

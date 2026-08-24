from __future__ import annotations

from datetime import UTC, datetime
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from transbridge.application.projects import DirtyDecision
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.filters_presenter import FiltersPresenter, FilterState
from transbridge.ui.workbench.filters_view import FiltersView
from transbridge.ui.workbench.save_presenter import SavePhase, SaveStatePresenter, SaveTarget
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.workflow_actions_view import WorkflowActionsView
from transbridge.ui.workbench.workflow_presenter import (
    StatisticsSummary,
    WorkbenchContentKind,
    WorkbenchWorkflowPresenter,
)

_APP = QApplication.instance() or QApplication([])


class _Config:
    token = ""


def _entry(index: int, stage: int, translation: str = "译文") -> TranslationEntry:
    return TranslationEntry(str(index), f"key-{index}", f"Original {index}", translation, stage, "NPC_:FULL")


def test_hierarchy_uses_plugin_language_only_for_real_plugin_format() -> None:
    presenter = WorkbenchWorkflowPresenter()
    plugin = presenter.hierarchy(
        project_id="p1",
        project_name="My Project",
        variant_id="v1",
        variant_name="默认",
        sources=(
            {"source_id": "plugin", "format_id": "plugin.sse", "location": "C:/mods/MyMod.esp", "role": "primary"},
        ),
    )
    translation_file = presenter.hierarchy(
        project_id="p1",
        project_name="My Project",
        variant_id="v1",
        variant_name="默认",
        sources=(
            {"source_id": "eet", "format_id": "xml.eet", "location": "C:/translations/MyMod.eet", "role": "primary"},
        ),
    )

    assert plugin.project_label == "本地工程 · My Project"
    assert plugin.variant_label == "翻译版本 · 默认"
    assert plugin.content_kind is WorkbenchContentKind.PLUGIN
    assert plugin.content_label == "插件 · MyMod.esp"
    assert translation_file.content_kind is WorkbenchContentKind.TRANSLATION_FILE
    assert translation_file.content_label == "翻译内容 · MyMod.eet"
    assert "插件" not in translation_file.content_label


def test_summary_click_filter_preserves_other_filter_state_and_entry_identity() -> None:
    entries = [_entry(0, 0, ""), _entry(1, 2), _entry(2, 3)]
    summary = StatisticsSummary.from_entries(entries)
    current = FilterState(categories=frozenset(("人名",)), search_key="key")
    review = summary.filter_state("review", current)
    presenter = FiltersPresenter()
    presenter.update(review)

    result = presenter.apply(entries, {})

    assert summary == StatisticsSummary(total=3, untranslated=1, needs_review=1, completed=1)
    assert review.categories == current.categories
    assert review.search_key == current.search_key
    assert result == [entries[1]]
    assert result[0] is entries[1]


def test_context_actions_expose_reasons_and_view_emits_one_stable_intent() -> None:
    states = WorkbenchWorkflowPresenter.actions(
        has_context=True,
        visible_entries=20,
        needs_review=0,
        write_supported=True,
    )
    review = next(item for item in states if item.intent_id is IntentId.TRANSLATION_REVIEW)
    view = WorkflowActionsView()
    emitted: list[str] = []
    view.intent_requested.connect(emitted.append)
    view.set_actions(states)

    view._buttons[IntentId.TRANSLATION_AI].click()

    assert review.enabled is False
    assert review.reason == "当前没有待检查词条"
    assert emitted == [IntentId.TRANSLATION_AI.value]
    view.close()


def test_label_management_is_separate_from_label_filter_and_advanced_is_progressive() -> None:
    view = FiltersView(on_changed=lambda: None, on_manage_labels=lambda: None)

    assert view.manage_labels_button.parent() is view
    assert view.category_widget.isHidden()
    view.set_content_visible(True)
    assert view.category_widget.isHidden()
    view.advanced_button.setChecked(True)
    view.build_categories([_entry(1, 0, "")])

    assert not view.category_widget.isHidden()
    assert view.manage_labels_button not in (
        view.label_container.itemAt(index).widget() for index in range(view.label_container.count())
    )
    view.close()


def test_save_projection_keeps_target_dirty_on_concurrent_edit_and_ignores_stale_completion() -> None:
    presenter = SaveStatePresenter()
    first = SaveTarget("project-a", "A", "variant-a", "默认", revision=4)
    next_target = SaveTarget("project-b", "B", "variant-b", "审校", revision=1)
    saved_at = datetime(2026, 8, 24, 10, 30, tzinfo=UTC)

    presenter.begin(first)
    presenter.mark_dirty(SaveTarget("project-a", "A", "variant-a", "默认", revision=5))
    state = presenter.succeed(first, saved_at=saved_at)

    assert state.phase is SavePhase.DIRTY
    assert state.saved_at == saved_at
    assert state.target is first
    presenter.show_target(next_target, dirty=False)
    assert presenter.succeed(first).target is next_target


def test_save_failure_has_retry_and_transition_requires_application_dirty_decision() -> None:
    presenter = SaveStatePresenter()
    target = SaveTarget("project-a", "A", "variant-a", "默认")

    presenter.begin(target)
    state = presenter.fail(target, "disk full")

    assert state.phase is SavePhase.FAILED
    assert state.retry_intent is IntentId.PROJECT_SAVE
    assert state.diagnostic == "disk full"
    assert state.allows_transition(None) is False
    assert state.allows_transition(DirtyDecision.CANCEL) is False
    assert state.allows_transition(DirtyDecision.DISCARD) is True


def test_filter_rerender_preserves_visible_row_selection_identity(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    entries = [_entry(index, 1) for index in range(400)]
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.resize(1_000, 500)
    widget.show()
    widget.refresh(TranslationEntryCollection(entries))
    while widget._table.rowCount() < 400:
        _APP.processEvents()
    widget._table.selectRow(300)
    widget._table.verticalScrollBar().setValue(240)
    _APP.processEvents()
    scroll_before = widget._table.verticalScrollBar().value()
    top_before = widget._table.item(widget._table.rowAt(0), 1).data(Qt.ItemDataRole.UserRole).id

    widget.apply_filter_state({"search_key": "key-", "stage": [1]})
    while widget._table.rowCount() < 400:
        _APP.processEvents()

    assert widget.selected_row_entry_ids() == (entries[300].id,)
    restored_top_item = widget._table.item(int(top_before), 1)
    assert widget._table.visualItemRect(restored_top_item).intersects(widget._table.viewport().rect())
    assert abs(widget._table.verticalScrollBar().value() - scroll_before) <= 1
    widget.close()

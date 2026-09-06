from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.tools.ai_translator.task_sources_view import TaskSourcesView


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _context() -> SimpleNamespace:
    first = SimpleNamespace(label="First.esp", collection=[object(), object()])
    second = SimpleNamespace(label="Second.esp", collection=[object()])
    return SimpleNamespace(slots={"first": first, "second": second}, active_slot=second)


def test_task_sources_default_to_active_and_use_only_scope_counts(qapp: QApplication) -> None:
    ctx = _context()
    panel = TaskSourcesView(ctx)
    events = []
    panel.selection_changed.connect(lambda: events.append(panel.selected_slots()))

    assert panel.selected_slots() == [ctx.active_slot]
    assert "待估算" in panel.summary.text()
    assert not panel.pending_button.isEnabled()
    panel.set_counts({"first": 1, "second": 0})
    assert events == []
    assert "共 2 条 · 本次 1 条" in panel.list.item(0).text()
    assert "本次 0 条" in panel.summary.text()
    panel.pending_button.click()
    assert panel.selected_slots() == [ctx.slots["first"]]
    assert "本次 1 条" in panel.summary.text()
    panel.select_all_button.click()
    assert panel.selected_slots() == list(ctx.slots.values())
    panel.clear_button.click()
    assert panel.selected_slots() == []
    assert len(events) == 3


def test_task_sources_preserve_user_order_and_checks_when_counts_change(qapp: QApplication) -> None:
    ctx = _context()
    panel = TaskSourcesView(ctx)
    panel.select_all_button.click()
    first = panel.list.takeItem(0)
    panel.list.addItem(first)
    panel.set_counts({"first": 0, "second": 1})
    assert panel.selected_slots() == [ctx.slots["second"], ctx.slots["first"]]
    assert panel.list.item(1).checkState() == Qt.CheckState.Checked
    assert "已选 2 个插件 · 本次 1 条" == panel.summary.text()


def test_task_sources_fallback_and_incomplete_estimates(qapp: QApplication) -> None:
    ctx = _context()
    ctx.active_slot = None
    panel = TaskSourcesView(ctx)
    assert panel.selected_slots() == [ctx.slots["first"]]
    panel.set_counts({"first": 1})
    assert not panel.pending_button.isEnabled()
    panel.select_all_button.click()
    assert "待估算" in panel.summary.text()
    empty = TaskSourcesView(None)
    empty.set_counts({})
    assert empty.selected_slots() == []
    assert not empty.pending_button.isEnabled()

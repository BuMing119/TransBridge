"""Shared storage folders never establish ownership of AI tasks."""

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import Qt

from tests.ui.tools import test_ai_task_history_ui as fixtures
from tests.ui.tools.test_ai_task_history_ui import _record, _wait
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.ui.tools.ai_translator import task_registry
from transbridge.ui.tools.ai_translator.task_history_dialog import TaskHistoryDialog
from transbridge.ui.tools.ai_translator.task_registry import AiTaskRegistry

qapp = fixtures.qapp


def test_live_windows_use_project_identity_when_storage_directory_is_shared(qapp, tmp_path, monkeypatch):
    context = SimpleNamespace(active_version_identity=("project-a", "variant"))
    registry = AiTaskRegistry(context, directory_provider=lambda: tmp_path)
    first = SimpleNamespace(
        session=SimpleNamespace(project_dir=tmp_path, version_identity=("project-a", "variant")),
        is_running=lambda: True,
    )
    second = SimpleNamespace(
        session=SimpleNamespace(project_dir=tmp_path, version_identity=("project-b", "variant")),
        is_running=lambda: True,
    )
    registry.windows = {"a": first, "b": second}
    shown = Mock()
    monkeypatch.setattr(task_registry, "show_and_activate", shown)
    assert registry.current_windows() == (first,)
    assert registry.foreground()
    shown.assert_called_once_with(first)
    context.active_version_identity = ("project-b", "variant")
    assert registry.current_windows() == (second,)


def test_history_filters_other_projects_and_marks_identityless_reports_read_only(qapp, tmp_path):
    store = TaskHistoryStore(tmp_path / "ai-task-history")
    store.save({**_record("a"), "project_id": "project-a", "variant_id": "variant"})
    store.save({**_record("b"), "project_id": "project-b", "variant_id": "variant"})
    store.save(_record("unknown"))
    context = SimpleNamespace(active_version_identity=("project-a", "variant"))
    registry = AiTaskRegistry(context, directory_provider=lambda: tmp_path)
    registry.windows["foreign-live"] = SimpleNamespace(
        session=SimpleNamespace(project_dir=tmp_path, version_identity=("project-b", "variant"))
    )
    dialog = TaskHistoryDialog(registry)
    try:
        _wait(qapp, lambda: not dialog.busy)
        rows = {
            dialog.tasks.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole): dialog.tasks.topLevelItem(index)
            for index in range(dialog.tasks.topLevelItemCount())
        }
        assert set(rows) == {"a", "unknown"}
        assert rows["unknown"].text(5) == "未知归属（仅供查看）"
    finally:
        dialog.close()

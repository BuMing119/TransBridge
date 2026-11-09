"""Shared storage folders never establish ownership of AI tasks."""

from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import Qt

from tests.ui.tools import test_ai_task_history_ui as fixtures
from tests.ui.tools.test_ai_task_history_ui import _record, _wait
from transbridge.persistence.task_store import TaskStore
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


def test_history_lists_all_projects_without_active_project_and_marks_identityless_reports_read_only(qapp, tmp_path):
    task_store = TaskStore(tmp_path / "task-store-v1")
    reports = task_store.history()
    reports.save({**_record("a"), "project_id": "project-a", "variant_id": "variant"})
    reports.save({**_record("b"), "project_id": "project-b", "variant_id": "variant"})
    reports.save(_record("unknown"))
    commands = SimpleNamespace(
        project_exists=lambda project_id: project_id == "project-a",
        result_receipt=lambda *_: None,
    )
    context = SimpleNamespace(active_version_identity=None, task_store=task_store, project_commands=commands)
    registry = AiTaskRegistry(context)
    registry.windows["foreign-live"] = SimpleNamespace(
        request=SimpleNamespace(run_id="foreign-live"),
        run=SimpleNamespace(
            state="running",
            status="运行中",
            records=SimpleNamespace(record=None, created_at="2026-10-03T08:01:00+00:00"),
        ),
        session=SimpleNamespace(
            project_dir=task_store.root,
            version_identity=("project-a", "variant"),
            tasks=(),
            completed=False,
            project_saved=False,
        ),
    )
    dialog = TaskHistoryDialog(registry)
    try:
        _wait(qapp, lambda: not dialog.busy)
        rows = {
            dialog.tasks.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole): dialog.tasks.topLevelItem(index)
            for index in range(dialog.tasks.topLevelItemCount())
        }
        assert set(rows) == {"a", "b", "foreign-live", "unknown"}
        assert rows["b"].text(4) == "原项目已删除"
        assert rows["foreign-live"].text(2) == "运行中"
        assert rows["unknown"].text(5) == "未知归属（仅供查看）"
        dialog.tasks.setCurrentItem(rows["unknown"])
        assert dialog.open_button.isEnabled()
        assert not dialog.continue_button.isEnabled() and not dialog.restart_button.isEnabled()
    finally:
        dialog.close()

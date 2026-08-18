from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.ui import context as context_module


class _Config:
    token = ""


_APP: QApplication | None = None


def _application() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _projection(revision: int, persisted: int) -> ProjectionSnapshot:
    return ProjectionSnapshot(
        "project:project-a",
        revision,
        persisted,
        {
            "variant_id": "variant-a",
            "label_library": {"review": {"name": "Review", "color": "#fff"}},
            "entries": [
                {
                    "entry_key": {"namespace": "source", "local_key": "entry-a"},
                    "labels": ["review"],
                }
            ],
        },
    )


def test_app_context_is_defensive_projection_and_releases_subscription(monkeypatch) -> None:
    _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    store = ProjectionStore(_projection(2, 1))
    context = context_module.AppContext(project_projection=store)

    labels = context.entry_labels
    labels["entry-a"].clear()
    library = context.label_library
    library["review"]["name"] = "mutated"

    assert context.dirty
    assert context.active_variant_id == "variant-a"
    assert context.entry_labels == {"entry-a": {"review"}}
    assert context.label_library["review"]["name"] == "Review"
    with pytest.raises(RuntimeError):
        context.entry_labels = {}
    with pytest.raises(RuntimeError):
        context.active_project = object()
    with pytest.raises(RuntimeError):
        context.variant_store = object()

    store.rebuild(_projection(2, 2))
    assert not context.dirty
    context.close_projection()
    assert store.listener_count == 0


def test_projection_notification_from_worker_is_marshaled_to_qt_thread(monkeypatch) -> None:
    app = _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    store = ProjectionStore(_projection(2, 1))
    context = context_module.AppContext(project_projection=store)
    updated = _projection(3, 3)

    worker = threading.Thread(target=lambda: store.rebuild(updated))
    worker.start()
    worker.join()

    deadline = time.monotonic() + 1
    while context.dirty and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)

    assert not context.dirty
    context.close_projection()


def test_projection_revision_without_label_change_does_not_emit_label_signal(monkeypatch) -> None:
    _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    store = ProjectionStore(_projection(2, 1))
    context = context_module.AppContext(project_projection=store)
    notifications: list[None] = []
    context.label_data_changed.connect(lambda: notifications.append(None))

    store.rebuild(_projection(3, 1))

    assert notifications == []

    store.rebuild(
        ProjectionSnapshot(
            "project:project-a",
            4,
            1,
            {
                "variant_id": "variant-a",
                "label_library": {},
                "entries": [
                    {
                        "entry_key": {"namespace": "source", "local_key": "entry-a"},
                        "labels": [],
                    }
                ],
            },
        )
    )

    assert notifications == [None]
    context.close_projection()


def test_each_dirty_projection_revision_restarts_autosave_signal(monkeypatch) -> None:
    _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    store = ProjectionStore(_projection(2, 2))
    context = context_module.AppContext(project_projection=store)
    notifications: list[bool] = []
    context.dirty_changed.connect(lambda: notifications.append(context.dirty))

    store.rebuild(_projection(3, 2))
    store.rebuild(_projection(4, 2))
    store.mark_persisted(4)

    assert notifications == [True, True, False]
    context.close_projection()


def test_project_bar_reads_v2_project_and_variant_catalog(monkeypatch) -> None:
    from transbridge.ui.workbench._project_bar import ProjectBar

    _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    store = ProjectionStore(
        ProjectionSnapshot(
            "project:project-a",
            2,
            2,
            {
                "project_id": "project-a",
                "project_name": "Project A",
                "active_variant_id": "variant-a",
                "variant_id": "variant-a",
                "variants": [
                    {"id": "variant-a", "name": "默认", "active": True},
                    {"id": "variant-b", "name": "审校版", "active": False},
                ],
                "sources": [],
                "entries": [],
                "label_library": {},
            },
        )
    )
    context = context_module.AppContext(project_projection=store)
    bar = ProjectBar(context)
    requested: list[str] = []
    bar.variant_switch_requested.connect(requested.append)

    bar.refresh()
    bar._variant_combo.setCurrentIndex(1)

    assert bar._project_label.text() == "Project A"
    assert bar._variant_combo.count() == 2
    assert bar._variant_combo.itemText(0) == "默认"
    assert bar._variant_combo.itemData(1) == "variant-b"
    assert requested == ["variant-b"]
    context.close_projection()

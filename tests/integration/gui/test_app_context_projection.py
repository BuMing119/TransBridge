from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import OperationResult
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui import context as context_module
from transbridge.ui.projection_types import CollectionSlot


class _Config:
    token = ""
    base_url = "https://paratranz.cn"
    user_id = 7


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


def test_project_binding_projection_is_defensive_and_updates_compatibility_id(monkeypatch) -> None:
    _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    binding = {
        "project_id": 42,
        "project_name": "Cloud",
        "endpoint": "https://paratranz.cn",
        "account_user_id": 7,
        "bound_at": None,
        "validated_at": None,
    }
    store = ProjectionStore(
        ProjectionSnapshot(
            "project:project-a",
            5,
            5,
            {
                "project_id": "project-a",
                "project_revision": 4,
                "variant_id": "variant-a",
                "entries": [],
                "label_library": {},
                "paratranz_binding": binding,
            },
        )
    )
    context = context_module.AppContext(project_projection=store)
    observed = context.paratranz_binding
    observed["project_name"] = "mutated"

    assert context.paratranz_binding["project_name"] == "Cloud"
    assert context.project_revision == 4
    assert context.paratranz_project_id == 42

    store.rebuild(
        ProjectionSnapshot(
            "project:project-a",
            6,
            6,
            {
                "project_id": "project-a",
                "project_revision": 5,
                "variant_id": "variant-a",
                "entries": [],
                "label_library": {},
                "paratranz_binding": None,
            },
        )
    )
    assert context.paratranz_binding is None
    assert context.paratranz_project_id is None
    context.close_projection()


def test_authoritative_projection_divergence_detects_unsaved_ui_copy(monkeypatch) -> None:
    _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    namespace = SourceNamespace("source")
    store = ProjectionStore(
        ProjectionSnapshot(
            "project:project-a",
            5,
            4,
            {
                "project_id": "project-a",
                "project_revision": 2,
                "variant_id": "variant-a",
                "variant_revision": 3,
                "entries": [
                    {
                        "entry_key": {"namespace": namespace.value, "local_key": "entry-a"},
                        "translation": "权威译文",
                        "stage": 1,
                    }
                ],
                "label_library": {},
            },
        )
    )
    context = context_module.AppContext(project_projection=store)
    entry = TranslationEntry(
        "entry-a",
        "entry-a",
        "source",
        "权威译文",
        1,
        None,
        entry_key=EntryKey(namespace, "entry-a"),
    )
    context.add_slot("source", CollectionSlot("source", TranslationEntryCollection([entry])))

    assert context.variant_revision == 3
    assert not context.authoritative_projection_diverged()

    entry.translation = "只改了界面"

    assert context.authoritative_projection_diverged()
    entry.translation = "权威译文"
    context.remove_slot("source")
    assert context.authoritative_projection_diverged()
    context.close_projection()


def test_projected_label_command_forwards_exact_entry_keys_and_expected_revisions(monkeypatch) -> None:
    _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    namespace = SourceNamespace("source")
    entry_key = EntryKey(namespace, "entry-a")
    store = ProjectionStore(
        ProjectionSnapshot(
            "project:project-a",
            5,
            5,
            {
                "project_id": "project-a",
                "project_revision": 2,
                "variant_id": "variant-a",
                "variant_revision": 3,
                "entries": [{"entry_key": entry_key.to_dict(), "labels": []}],
                "label_library": {},
            },
        )
    )

    class Commands:
        def __init__(self) -> None:
            self.calls = []

        def replace_labels(self, labels, library, runtime_context, **expected):
            self.calls.append((labels, library, runtime_context, expected))
            return OperationResult.completed({"revision": 4})

    commands = Commands()
    runtime_context = object()
    context = context_module.AppContext(
        project_projection=store,
        project_commands=commands,
        runtime_context=runtime_context,
    )
    collection = TranslationEntryCollection([
        TranslationEntry(
            "entry-a",
            "entry-a",
            "source",
            "",
            0,
            None,
            entry_key=entry_key,
        )
    ])
    context.add_slot("active", CollectionSlot("active", collection))
    variant_ref = VariantRef(VariantId("variant-a"), ProjectId("project-a"))

    result = context.replace_projected_labels(
        {"entry-a": {"review"}},
        {"review": {"name": "Review", "color": "#fff"}},
        expected_project_revision=2,
        expected_variant_revision=3,
        expected_variant_ref=variant_ref,
    )

    assert result.is_success
    labels, _library, observed_runtime, expected = commands.calls[0]
    assert labels == {entry_key: {"review"}}
    assert observed_runtime is runtime_context
    assert expected == {
        "expected_project_revision": 2,
        "expected_variant_revision": 3,
        "expected_variant_ref": variant_ref,
    }
    context.close_projection()

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication
import pytest

from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.ui import context as context_module


class _Config:
    token = ""


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
    QCoreApplication.instance() or QCoreApplication([])
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

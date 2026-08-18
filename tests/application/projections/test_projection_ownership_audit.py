from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_production_gui_injects_projection_and_collection_signal_does_not_guess_dirty() -> None:
    app_source = (ROOT / "src/transbridge/ui/app.py").read_text(encoding="utf-8")
    context_source = (ROOT / "src/transbridge/ui/context.py").read_text(encoding="utf-8")

    assert 'resolve("project_projection")' in app_source
    assert 'resolve("gui_project_commands")' in app_source
    assert "collection_changed.connect(lambda _: self.mark_dirty())" not in context_source


def test_production_composition_uses_controlled_v2_subdirectory_and_no_legacy_project_handle() -> None:
    composition = (ROOT / "src/transbridge/bootstrap/composition.py").read_text(encoding="utf-8")

    assert 'Path(get_data_dir()) / "projects-v2"' in composition
    assert "ProjectHandle" not in composition

"""All dynamic-term entry points resolve the task's existing cache identity."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.term_database import DynamicTermDatabase
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.llm import LLMConfig
from transbridge.ui.dynamic_terminology_source import dynamic_terminology_source
from transbridge.ui.tools.ai_translator import ai_translator_window, config_dialogs, task_launch
from transbridge.ui.tools.ai_translator.run_spec import AiPreflightResult
from transbridge.ui.tools.ai_translator.source_terms import source_terminology_path
from transbridge.ui.tools.terminology_profiles import source_catalog


def _context(reference=None):
    slot = SimpleNamespace(
        esp_path=reference,
        label="Saved",
        source_snapshot=SimpleNamespace(source=SimpleNamespace(display_name="Saved.esp")),
    )
    return SimpleNamespace(
        active_slot=slot,
        active_key="saved-source",
        slots={"saved-source": slot},
        project_sources=({"source_id": "saved-source", "format_id": "plugin.sse", "location": reference},),
    )


@pytest.fixture
def cache(tmp_path, monkeypatch):
    def directory(stem):
        path = tmp_path / stem
        path.mkdir(exist_ok=True)
        return str(path)

    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", directory)
    database = DynamicTermDatabase(source_terminology_path(None, "saved-source"))
    database.add("Sword", "长剑", "manual")
    database.save()
    return database


def test_editor_without_reference_reads_the_same_cache_as_runtime(cache, monkeypatch):
    from transbridge.ui.tools.ai_translator import _term_editor_dialog

    opened = []

    class Dialog:
        def __init__(self, database, **_kwargs):
            self.database = database

        def exec(self):
            opened.append((self.database._path, [entry.term for entry in self.database.as_list()]))

    monkeypatch.setattr(_term_editor_dialog, "_TermEditorDialog", Dialog)
    monkeypatch.setattr(config_dialogs.QMessageBox, "warning", lambda *_: pytest.fail("A reference is optional"))
    config_dialogs.open_term_editor(None, None, source_key="saved-source")

    assert opened == [(cache._path, ["Sword"])]


def test_ai_window_passes_the_stable_active_source_to_editor(monkeypatch):
    context = _context()
    opened = []
    monkeypatch.setattr(
        ai_translator_window,
        "open_term_editor",
        lambda _parent, reference, **kwargs: opened.append((reference, kwargs["source_key"])),
    )
    ai_translator_window.AITranslatorWindow.on_view_terms(SimpleNamespace(_ctx=context))

    assert opened == [(None, "saved-source")]


def test_catalog_without_reference_uses_saved_name_and_the_task_cache(monkeypatch):
    context = _context()
    monkeypatch.setattr(source_catalog, "bound_paratranz_project", lambda _: None)
    monkeypatch.setattr(
        source_catalog.LLMConfig,
        "load_from_file",
        lambda: SimpleNamespace(local_json_path="", local_excel_path="", local_csv_path=""),
    )
    choices = source_catalog.configured_source_selections(context)

    assert len(choices) == 1
    assert choices[0].label == "动态词库 · Saved"
    assert choices[0].request.esp_path == source_terminology_path(None, "saved-source")
    assert "Saved.esp" in choices[0].detail


def test_startup_inspection_finds_dynamic_terms_without_reference(cache, monkeypatch):
    monkeypatch.setattr(task_launch, "preflight_ai_run", lambda *_args, **_kwargs: AiPreflightResult())
    monkeypatch.setattr(task_launch, "prepare_run_request", lambda *_args, **_kwargs: "prepared")
    task = SourceTask("saved-source", "Saved", None, (), (object(),), ())
    result = task_launch.inspect_launch("translate", SimpleNamespace(), (task,), None, "owner", {})

    assert result == (None, (), False, "prepared")


def test_saved_display_name_does_not_choose_a_different_cache_from_runtime():
    context = _context("D:/mods/Legacy.esp")
    resolved = dynamic_terminology_source(context)

    assert resolved.display_name == "Saved.esp"
    assert resolved.cache_path == source_terminology_path(context.active_slot.esp_path, context.active_key)
    assert Path(resolved.cache_path).name == "Legacy.esp"

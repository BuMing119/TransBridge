from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from tests.application.projects.test_source_update_plugin import _plugin
from tests.persistence.v2.test_saved_project_sources import CountingPreparer, _services
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.translation.result_merge import ResultEntryChange, ResultEntryState


def _saved(root, *, qualified=True, extra=False):
    path = root / "known.esp"
    _plugin(path, [(0x800, "First"), (0x800 if qualified else 0x801, "Second")])
    other = root / "unknown.esp"
    _plugin(other, [(0x900, "Unknown")])
    services = _services(root / "store")
    context = RequestContext("known-originals", run_id="source-texts")
    try:
        created = services.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Known originals",
                source=ProjectSourceRequest(
                    str(path), FormatId.PLUGIN_SSE, options=(("source_namespace", "source:known"),)
                ),
                additional_sources=(
                    ProjectSourceRequest(
                        str(other), FormatId.PLUGIN_SSE, options=(("source_namespace", "source:unknown"),)
                    ),
                )
                if extra
                else (),
            ),
            context,
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        project_path = Path(services.projects.path_for(active.project_ref))
        for state in active.variant.snapshot().entries:
            assert services.gui_project_commands.update_entry(
                state.entry_key, context, translation="Saved", stage=5
            ).is_success
        assert services.gui_project_commands.save(context).is_success
        return path, project_path, context, project_path.read_bytes()
    finally:
        services.close()


@contextmanager
def _reopen(root, path, context):
    preparer = CountingPreparer(forbidden=True)
    services = _services(root / "store", preparer)
    try:
        opened = services.current_project_opener.open_path(path, context)
        assert opened.is_success, opened.diagnostics
        yield services
        assert preparer.calls == 0
    finally:
        services.close()


@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("input_state", ["deleted", "replaced"])
def test_saved_rows_support_upload_and_result_text_without_source_input(tmp_path, monkeypatch, qualified, input_state):
    source, path, context, database_bytes = _saved(tmp_path, qualified=qualified)
    if input_state == "deleted":
        source.unlink()
    else:
        _plugin(source, [(0x900, "Replacement content")])
    open_file = Path.open

    def deny_original(path, *args, **kwargs):
        assert path != source, "saved translation consumers must not read a historical source file"
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_original)
    with _reopen(tmp_path, path, context) as services:
        active = services.project_lifecycle.active
        variant = active.variant.snapshot()
        assert all((state.entry_key.original is not None) == qualified for state in variant.entries)
        catalog = services.gui_project_commands.file_sources_catalog(context)
        assert catalog.is_success and len(catalog.value) == 1
        captured = services.gui_project_commands.categorized_upload_snapshot(
            context, endpoint="https://paratranz.cn", remote_project_id=42, source_ids=(catalog.value[0].source_id,)
        )
        assert captured.is_success, captured.diagnostics
        entries = captured.value.sources[0].entries
        assert {entry.original for entry in entries} == {"First", "Second"}
        assert all(entry.translation == "Saved" and entry.context == "WEAP:FULL" for entry in entries)
        texts = services.gui_project_commands._categorized_uploads.result_source_texts(
            active.project, variant, {entry.entry_key for entry in variant.entries}, context
        )
        assert set(texts) == {entry.identity for entry in entries}
        for entry in entries:
            original, source_context, locator = texts[entry.identity]
            assert (original, source_context) == (entry.original, entry.context)
            assert locator is not None
        assert path.read_bytes() == database_bytes


def test_selected_source_remains_available_after_other_source_input_is_deleted(tmp_path):
    source, path, context, _ = _saved(tmp_path, extra=True)
    source.unlink()
    source.with_name("unknown.esp").unlink()
    with _reopen(tmp_path, path, context) as services:
        catalog = services.gui_project_commands.file_sources_catalog(context).value
        known = next(item for item in catalog if item.namespace.value == "source:known")
        captured = services.gui_project_commands.categorized_upload_snapshot(
            context, endpoint="https://paratranz.cn", remote_project_id=42, source_ids=(known.source_id,)
        )
        assert captured.is_success and len(captured.value.sources) == 1, captured.diagnostics
        assert {entry.original for entry in captured.value.sources[0].entries} == {"First", "Second"}


@pytest.mark.parametrize("changed_metadata", [None, "context", "locator"])
def test_saved_original_result_merge_preserves_context_and_locator_conflict_protection(tmp_path, changed_metadata):
    source, path, context, database_bytes = _saved(tmp_path)
    source.unlink()
    with _reopen(tmp_path, path, context) as services:
        active = services.project_lifecycle.active
        variant = active.variant.snapshot()
        entry = variant.entries[0]
        texts = services.gui_project_commands._categorized_uploads.result_source_texts(
            active.project, variant, {entry.entry_key}, context
        )
        original, source_context, locator = texts[entry.entry_key]
        assert source_context and locator
        base = ResultEntryState(
            entry.entry_key,
            original,
            entry.translation,
            entry.stage.value,
            context="previous source context" if changed_metadata == "context" else source_context,
            source_locator="previous source locator" if changed_metadata == "locator" else locator,
        )
        result = services.gui_project_commands.merge_entry_states(
            (ResultEntryChange(base, replace(base, translation="New translation", stage=1)),),
            context,
            expected_variant_ref=active.formal_variant_ref,
        )
        assert result.is_success, result.diagnostics
        if changed_metadata is None:
            assert len(result.value.applied) == 1 and not result.value.pending
        else:
            assert not result.value.applied
            assert result.value.pending[0].reason == "source_changed_or_missing"
        assert path.read_bytes() == database_bytes

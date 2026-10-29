from dataclasses import replace
import json
from pathlib import Path

import pytest

from tests.application.projects.test_source_update_plugin import _plugin
from tests.persistence.v2.test_saved_project_sources import CountingPreparer, _services
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.entry_source_text import namespace_source_id
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
        return path, project_path, context
    finally:
        services.close()


@pytest.mark.parametrize("kind", ["no_artifact", "unavailable_artifact", "unregistered"])
@pytest.mark.parametrize("reference", ["", "present"])
def test_original_qualified_saved_rows_support_upload_and_result_text_without_source_input(
    tmp_path, monkeypatch, kind, reference
):
    source, project_path, context = _saved(tmp_path)
    document = json.loads(project_path.read_bytes())
    if kind == "unregistered":
        document["data"]["sources"] = []
    else:
        registered = document["data"]["sources"][0]
        registered["location"] = str(source) if reference else ""
        registered["legacy"]["path"] = registered["location"]
        if kind == "no_artifact":
            registered.pop("source_artifact_id")
        else:
            registered["source_artifact_id"] = "f" * 64
    project_path.write_text(json.dumps(document), encoding="utf-8")
    open_file = Path.open

    def deny_reference(path, *args, **kwargs):
        assert path != source, "text consumers must not read an optional original file"
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_reference)
    preparer = CountingPreparer(forbidden=True)
    services = _services(tmp_path / "store", preparer)
    try:
        opened = services.current_project_opener.open_path(project_path, context)
        assert opened.is_success, opened.diagnostics
        active = services.project_lifecycle.active
        variant = active.variant.snapshot()
        assert all(state.entry_key.original is not None for state in variant.entries)
        catalog = services.gui_project_commands.file_sources_catalog(context)
        assert catalog.is_success and len(catalog.value) == 1
        selected_id = catalog.value[0].source_id
        if kind == "unregistered":
            assert selected_id == namespace_source_id(variant.entries[0].entry_key.namespace)
        captured = services.gui_project_commands.categorized_upload_snapshot(
            context, endpoint="https://paratranz.cn", remote_project_id=42, source_ids=(selected_id,)
        )
        assert captured.is_success, captured.diagnostics
        assert {entry.original for entry in captured.value.sources[0].entries} == {"First", "Second"}
        assert all(
            entry.translation == "Saved" and entry.context is None for entry in captured.value.sources[0].entries
        )
        texts = services.gui_project_commands._categorized_uploads.result_source_texts(
            active.project, variant, {entry.entry_key for entry in variant.entries}, context
        )
        assert texts == {state.entry_key: (state.entry_key.original, None, None) for state in variant.entries}
        assert preparer.calls == 0
    finally:
        services.close()


def test_bare_saved_keys_still_require_original_input(tmp_path):
    _source, path, context = _saved(tmp_path, qualified=False)
    document = json.loads(path.read_bytes())
    document["data"]["sources"][0].pop("source_artifact_id")
    path.write_text(json.dumps(document), encoding="utf-8")
    preparer = CountingPreparer(forbidden=True)
    services = _services(tmp_path / "store", preparer)
    try:
        assert services.current_project_opener.open_path(path, context).is_success
        captured = services.gui_project_commands.categorized_upload_snapshot(
            context, endpoint="https://paratranz.cn", remote_project_id=42
        )
        assert not captured.is_success
        assert captured.diagnostics[0].code == "CATEGORIZED_SOURCE_BASELINE_AMBIGUOUS"
        active = services.project_lifecycle.active
        variant = active.variant.snapshot()
        assert not services.gui_project_commands._categorized_uploads.result_source_texts(
            active.project, variant, {state.entry_key for state in variant.entries}, context
        )
        assert preparer.calls == 0
    finally:
        services.close()


def test_selected_known_source_is_not_blocked_by_an_unselected_unknown_source(tmp_path):
    _source, path, context = _saved(tmp_path, extra=True)
    document = json.loads(path.read_bytes())
    for registered in document["data"]["sources"]:
        registered.pop("source_artifact_id")
    path.write_text(json.dumps(document), encoding="utf-8")
    services = _services(tmp_path / "store", CountingPreparer(forbidden=True))
    try:
        assert services.current_project_opener.open_path(path, context).is_success
        catalog = services.gui_project_commands.file_sources_catalog(context).value
        known = next(item for item in catalog if item.namespace.value == "source:known")
        captured = services.gui_project_commands.categorized_upload_snapshot(
            context, endpoint="https://paratranz.cn", remote_project_id=42, source_ids=(known.source_id,)
        )
        assert captured.is_success and len(captured.value.sources) == 1, captured.diagnostics
        assert {entry.original for entry in captured.value.sources[0].entries} == {"First", "Second"}
    finally:
        services.close()


@pytest.mark.parametrize("required_metadata", [None, "context", "locator"])
def test_known_original_result_merge_keeps_existing_context_and_locator_protection(tmp_path, required_metadata):
    _source, path, context = _saved(tmp_path)
    document = json.loads(path.read_bytes())
    document["data"]["sources"] = []
    path.write_text(json.dumps(document), encoding="utf-8")
    services = _services(tmp_path / "store", CountingPreparer(forbidden=True))
    try:
        assert services.current_project_opener.open_path(path, context).is_success
        active = services.project_lifecycle.active
        entry = active.variant.snapshot().entries[0]
        base = ResultEntryState(
            entry.entry_key,
            entry.entry_key.original,
            entry.translation,
            entry.stage.value,
            context="previous source context" if required_metadata == "context" else None,
            source_locator="previous source locator" if required_metadata == "locator" else None,
        )
        result = services.gui_project_commands.merge_entry_states(
            (ResultEntryChange(base, replace(base, translation="New translation", stage=1)),),
            context,
            expected_variant_ref=active.formal_variant_ref,
        )
        assert result.is_success, result.diagnostics
        if required_metadata is None:
            assert len(result.value.applied) == 1 and not result.value.pending
        else:
            assert not result.value.applied
            assert result.value.pending[0].reason == "source_changed_or_missing"
    finally:
        services.close()

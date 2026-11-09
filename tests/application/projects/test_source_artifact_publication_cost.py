"""Exercise source preparation and publication without persisting parser artifacts."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.persistence.v2.test_workflow_closure_audit import service_factory as service_factory_fixture
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.source_commands import source_request_with_initial_entry_states
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.project_store.repository import ProjectStore

service_factory = service_factory_fixture
_CONTEXT = RequestContext("source-publication", run_id="source-publication")


def _count_parses(monkeypatch):
    calls = []
    parse = TranslationIoProjectSourcePreparer.prepare_source

    def tracked(preparer, request, *args, **kwargs):
        calls.append(Path(request.location))
        return parse(preparer, request, *args, **kwargs)

    monkeypatch.setattr(TranslationIoProjectSourcePreparer, "prepare_source", tracked)
    return calls


def _create(services, name, source=None, **options):
    result = services.gui_project_commands.create_project(
        ProjectProvisioningRequest(name, source=source, **options), _CONTEXT
    )
    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    return ProjectStore(services.projects.path_for(active.project_ref)), active.formal_variant_ref.identity


def _assert_no_artifacts(services, store, plugins):
    payload = store.path.read_bytes()
    for plugin in plugins:
        assert plugin.read_bytes() not in payload
        assert str(plugin).encode() not in payload
    assert not (Path(services.root) / "source_artifacts").exists()


def test_provisioning_extracts_each_source_once_and_keeps_source_order(service_factory, tmp_path, monkeypatch):
    sources = [
        write_plugin(tmp_path / f"Source{index}.esp", [(0x800 + index, f"NPC{index}", f"Original {index}")])
        for index in range(3)
    ]
    calls = _count_parses(monkeypatch)
    services = service_factory()
    store, variant = _create(
        services, "Sources", additional_sources=tuple(ProjectSourceRequest(str(source)) for source in sources)
    )

    assert calls == sources
    assert [source.name for source in store.sources()] == [source.name for source in sources]
    assert [row.snapshot.original for row in store.read_page(variant)] == ["Original 0", "Original 1", "Original 2"]
    _assert_no_artifacts(services, store, sources)


def test_folded_import_stores_final_translations_without_either_plugin(service_factory, tmp_path, monkeypatch):
    primary = write_plugin(tmp_path / "Primary.esp", [(0x800, "Greeting", "Welcome")])
    translated = write_plugin(tmp_path / "Translated.esp", [(0x800, "Greeting", "欢迎")])
    calls = _count_parses(monkeypatch)
    services = service_factory()
    store, variant = _create(
        services,
        "Folded",
        ProjectSourceRequest(str(primary)),
        migration_sources=(ProjectSourceRequest(str(translated)),),
    )

    assert calls == [primary, translated]
    (entry,) = store.read_page(variant)
    assert (entry.snapshot.original, entry.state.translation) == ("Welcome", "欢迎")
    _assert_no_artifacts(services, store, (primary, translated))


@pytest.mark.parametrize("with_initial", [False, True])
def test_add_source_parses_once_and_saves_final_initial_state(service_factory, tmp_path, monkeypatch, with_initial):
    plugin = write_plugin(tmp_path / "Added.esp", [(0x800, "Greeting", "Welcome")])
    request = ProjectSourceRequest(str(plugin))
    if with_initial:
        parsed = TranslationIoProjectSourcePreparer().prepare_source(
            request, _CONTEXT, role="primary", common_options=()
        )
        key = parsed.baseline.entries[0].entry_key
        request = source_request_with_initial_entry_states(request, {key: ("Imported", 3)})
    calls = _count_parses(monkeypatch)
    services = service_factory()
    store, variant = _create(services, "Empty project")

    added = services.gui_project_commands.add_source(request, _CONTEXT)
    assert added.is_success, added.diagnostics
    saved = services.gui_project_commands.save(_CONTEXT)
    assert saved.is_success, saved.diagnostics

    assert calls == [plugin]
    (entry,) = store.read_page(variant)
    assert (entry.state.translation, entry.state.stage.value) == (("Imported", 3) if with_initial else ("", 0))
    _assert_no_artifacts(services, store, (plugin,))


def test_external_prepared_source_keeps_final_approved_metadata_and_rejects_unknown_material(service_factory, tmp_path):
    plugin = write_plugin(tmp_path / "Prepared.esp", [(0x800, "Greeting", "Welcome")])
    request = ProjectSourceRequest(str(plugin))
    prepared = TranslationIoProjectSourcePreparer().prepare_source(request, _CONTEXT, role="primary", common_options=())
    (entry,) = prepared.hydration.entries
    metadata = {**dict(entry.metadata), "terminology_plugin_id": "Approved.esp"}
    approved = replace(
        prepared, hydration=replace(prepared.hydration, entries=(replace(entry, metadata=tuple(metadata.items())),))
    )
    unsafe = replace(
        approved,
        hydration=replace(
            approved.hydration,
            entries=(replace(entry, metadata=(*metadata.items(), ("prepared_source", str(plugin)))),),
        ),
    )
    services = service_factory()
    store, variant = _create(services, "External")
    commands = services.gui_project_commands
    before = store.path.read_bytes()
    ref = services.project_lifecycle.active.formal_variant_ref

    rejected = commands.add_prepared_source(unsafe, _CONTEXT, expected_variant_ref=ref)
    assert not rejected.is_success and rejected.diagnostics[0].code == "INVALID_INPUT"
    assert not services.project_lifecycle.active.dirty
    assert store.path.read_bytes() == before
    added = commands.add_prepared_source(approved, _CONTEXT, expected_variant_ref=ref)
    assert added.is_success, added.diagnostics
    assert commands.save(_CONTEXT).is_success

    assert dict(store.read_page(variant)[0].snapshot.metadata)["terminology_plugin_id"] == "Approved.esp"
    _assert_no_artifacts(services, store, (plugin,))


@pytest.mark.parametrize("external_prepared", [False, True])
def test_source_publication_retains_expected_revision_guard(service_factory, tmp_path, external_prepared):
    plugin = write_plugin(tmp_path / "Stale.esp", [(0x800, "Greeting", "Welcome")])
    request = ProjectSourceRequest(str(plugin))
    services = service_factory()
    store, _variant = _create(services, "Stale")
    commands = services.gui_project_commands
    before = store.path.read_bytes()
    previous = services.project_lifecycle.active
    if external_prepared:
        prepared = TranslationIoProjectSourcePreparer().prepare_source(
            request, _CONTEXT, role="primary", common_options=()
        )
        result = commands.add_prepared_source(
            prepared, _CONTEXT, expected_project_revision=1, expected_variant_ref=previous.formal_variant_ref
        )
    else:
        result = commands.add_source(request, _CONTEXT, expected_project_revision=1)

    assert not result.is_success
    assert result.diagnostics[0].code == "ACTIVE_PROJECT_REVISION_CHANGED"
    assert services.project_lifecycle.active is previous
    assert store.path.read_bytes() == before

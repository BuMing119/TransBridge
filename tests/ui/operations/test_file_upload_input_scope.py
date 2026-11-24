from threading import Event
from types import SimpleNamespace

import pytest

from tests.ui.operations.test_categorized_upload_bridge import (
    _progress,
    harness as upload_harness,
    project_fixture as upload_project,
)
from transbridge.paratranz.project_upload import Selection
from transbridge.ui.operations.categorized_upload_bridge import CategorizedUploadSession
from transbridge.ui.source_input_state import SourceInputScope

harness = upload_harness
project_fixture = upload_project


def test_capture_only_reads_selected_source_ids_and_does_not_expand_to_other_slots(harness):
    initial = harness.session._capture()
    context = harness.context
    context.project_sources = harness.services.project_lifecycle.active.project.envelope.data["sources"]
    context.slots = {
        source.source_id: SimpleNamespace(source_id=source.source_id, collection=source.entries)
        for source in initial.sources
    }
    selected = initial.sources[0].source_id
    session = CategorizedUploadSession(SourceInputScope(context, (selected,)))
    snapshot = session._capture()
    assert session.source_ids == (selected,)
    assert tuple(source.source_id for source in snapshot.sources) == (selected,)
    preview = session.load_preview(_progress, Event())
    assert tuple(source.source_id for source in preview) == (selected,)


def test_legacy_known_original_slot_passes_namespace_read_model_id(harness):
    initial = harness.session._capture()
    source = initial.sources[0]
    namespace = source.entries[0].identity.namespace.value
    context = harness.context
    context.project_sources = ()
    context.slots = {namespace: SimpleNamespace(source_id=None, collection=source.entries)}
    session = CategorizedUploadSession(context)
    assert session.source_ids == (f"namespace:{namespace}",)


@pytest.mark.parametrize("categorized", [False, True])
def test_unified_session_catalog_preview_upload_and_retry_use_the_selected_scope(harness, monkeypatch, categorized):
    initial = harness.session._capture()
    current = initial.sources[1]
    context = harness.context
    context.active_slot = SimpleNamespace(source_id=current.source_id)
    calls = []
    capture = context.project_commands.categorized_upload_snapshot

    def observe(request, **options):
        calls.append((options["source_ids"], options["include_dialogue_order"]))
        return capture(request, **options)

    monkeypatch.setattr(context.project_commands, "categorized_upload_snapshot", observe)
    session = CategorizedUploadSession(context, select_sources=True, gateway_factory=harness.remote.gateway)
    catalog = session.load_source_catalog(_progress, Event())
    assert tuple(source.source_id for source in catalog if source.selected) == (current.source_id,)
    assert {source.source_id for source in catalog} == {source.source_id for source in initial.sources}
    assert calls == []

    ids = (current.source_id,)
    preview = session.load_preview(_progress, Event(), source_ids=ids, categorized=categorized)
    assert calls == [(ids, categorized)]
    assert tuple(source.source_id for source in preview) == ids
    paths = {file.path for file in preview[0].files}
    if categorized:
        assert paths and all(path.startswith(f"{current.label}/") for path in paths)
    else:
        assert paths == {f"{current.label}.json"}
    assert harness.remote.reads == []

    selection = Selection(ids, tuple(file.file_key for file in preview[0].files))
    cancel = Event()
    cancel.set()
    assert session.upload(selection, _progress, cancel, categorized=categorized).retry_count == len(paths)
    assert calls == [(ids, categorized)]
    assert harness.remote.reads == []

    session.load_preview(_progress, Event(), source_ids=ids, categorized=not categorized)
    receipt = session.retry_upload(_progress, Event())
    assert calls == [(ids, categorized), (ids, not categorized), (ids, categorized)]
    assert receipt.success_count == len(paths)
    assert {file.path for file in harness.remote.files.values()} == paths

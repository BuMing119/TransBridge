from threading import Event
from types import SimpleNamespace

from tests.ui.operations.test_categorized_upload_bridge import (
    _progress,
    harness as upload_harness,
    project_fixture as upload_project,
)
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

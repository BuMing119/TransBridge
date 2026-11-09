from itertools import count
from pathlib import Path

import pytest

from tests.dialogue_order_support import dialogue_bytes
from transbridge.application.categorized_upload import categorize_sources
from transbridge.application.contracts import ErrorCategory, RequestContext
from transbridge.application.dialogue.loading import DialogueIndexLoader
from transbridge.application.io import FormatId
from transbridge.application.io.dialogue_reading_view import build_dialogue_reading_view
from transbridge.application.io.plugin_dialogue_order import METADATA_KEY, dialogue_order_from_snapshot
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.categorized_upload import ProjectCategorizedUploadService
from transbridge.application.tasks import CancellationToken
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store import source_reads
from transbridge.ui.source_hydration import slot_from_hydration


@pytest.fixture
def project(tmp_path):
    plugin = tmp_path / "Fixture.esp"
    plugin.write_bytes(dialogue_bytes())
    ids = count()
    services = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=lambda: f"dialogue-{next(ids)}",
        timestamp_factory=lambda: "2026-10-29T00:00:00+00:00",
    )
    context = RequestContext("dialogue-publication")
    result = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Dialogue", source=ProjectSourceRequest(str(plugin), FormatId.PLUGIN_SSE)), context
    )
    assert result.is_success, result.diagnostics
    service = services.gui_project_commands._categorized_uploads
    yield services, service, context, plugin
    services.close()


def capture(service, context, **kwargs):
    return service.snapshot(context, endpoint="https://paratranz.cn/api", remote_project_id=42, **kwargs)


def texts(snapshot):
    groups = categorize_sources(snapshot.sources)
    return [entry.original for group in groups if group.category_id == "dialogue:00000001" for entry in group.entries]


def test_reopen_without_live_plugin_restores_same_order_and_does_not_save(project):
    services, service, context, plugin = project
    original = capture(service, context)
    assert original.is_success, original.diagnostics
    assert texts(original.value) == ["First topic", "First reply", "Second reply", "Later topic", "Later reply"]
    active = services.project_lifecycle.active
    project_path = services.projects.path_for(active.project_ref)
    before = services.variants.read_snapshot(active.formal_variant_ref)
    plugin.unlink()
    reopened = services.current_project_opener.open_path(project_path, context)
    assert reopened.is_success, reopened.diagnostics
    assert reopened.value["hydrations"]
    slot = slot_from_hydration(reopened.value["hydrations"][0])
    assert slot.reading_source_entries
    reading = build_dialogue_reading_view(
        slot.reading_source_entries, dialogue_order_from_snapshot(slot.source_snapshot)
    )
    by_key = {entry.identity: entry for entry in slot.collection}
    assert [
        by_key[key].original for key in reading.ordered_keys if by_key[key].context.startswith(("INFO:", "DIAL:"))
    ] == texts(original.value)
    index = DialogueIndexLoader().build(
        slot.collection, snapshot=slot.source_snapshot, reading_entries=slot.reading_source_entries
    )
    quest = next(quest for quest in index.quests if quest.identity[1] == "00000001")
    assert [topic.identity[2] for topic in quest.topics if topic.kind == "DIAL"] == ["00000010", "00000020"]
    fresh = ProjectCategorizedUploadService(
        service._lifecycle, service._baselines, service._source_preparer, stored_sources=service._stored_sources
    )
    restored = capture(fresh, context)
    assert restored.is_success, restored.diagnostics
    assert texts(restored.value) == texts(original.value)
    assert services.variants.read_snapshot(services.project_lifecycle.active.formal_variant_ref) == before


def test_dialogue_order_is_retained_as_extracted_metadata(project, monkeypatch):
    services, service, context, plugin = project
    original_bytes = plugin.read_bytes()
    plugin.unlink()
    active = services.project_lifecycle.active
    database = Path(services.projects.path_for(active.project_ref))
    assert original_bytes not in database.read_bytes()
    monkeypatch.setattr(
        service._source_preparer, "prepare_source", lambda *_args, **_kwargs: pytest.fail("Plugin source was read")
    )
    reopened = services.current_project_opener.prepare_path(database, context)
    assert reopened.is_success, reopened.diagnostics
    (hydration,) = reopened.value.hydrations
    assert hydration.source_snapshot.content is None
    assert METADATA_KEY in dict(hydration.source_snapshot.metadata)
    result = capture(service, context)
    assert result.is_success, result.diagnostics
    assert texts(result.value) == ["First topic", "First reply", "Second reply", "Later topic", "Later reply"]


def test_plain_capture_does_not_read_dialogue_metadata(project, monkeypatch):
    _services, service, context, _plugin = project
    actual = source_reads.dialogue_order_from_snapshot
    calls = []

    def read(snapshot, **kwargs):
        calls.append(snapshot.sha256)
        return actual(snapshot, **kwargs)

    monkeypatch.setattr(source_reads, "dialogue_order_from_snapshot", read)
    plain = capture(service, context, include_dialogue_order=False)
    assert plain.is_success and plain.value.sources[0].dialogue_order is None
    assert calls == []
    ordered = capture(service, context)
    assert ordered.is_success and ordered.value.sources[0].dialogue_order is not None
    assert len(calls) == 1


def test_cancelled_capture_does_not_fallback_to_saved_identity_text(project):
    _services, service, context, _plugin = project
    token = CancellationToken()
    token._cancel("Cancelled before source capture")
    result = capture(service, context, cancellation=token)
    assert not result.is_success
    assert result.diagnostics[0].category is ErrorCategory.CANCELLED

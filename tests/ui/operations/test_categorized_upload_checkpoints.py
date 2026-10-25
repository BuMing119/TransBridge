"""Upload must not implicitly persist IDs or unsaved translation work."""

from tests.ui.operations import test_categorized_upload_bridge as bridge

project_fixture = bridge.project_fixture
harness = bridge.harness


def test_upload_never_commits_associations_or_publishes_project_changes(harness, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("file upload must not commit associations")

    monkeypatch.setattr(harness.services.project_lifecycle, "commit_active_content", forbidden)
    before = bridge._snapshot(harness)
    publications = []
    subscription = harness.services.project_projection.subscribe(publications.append, replay=False)
    try:
        result = bridge._upload(harness)
    finally:
        subscription.close()
    assert result.success_count == 2
    assert bridge._snapshot(harness) == before and publications == []


def test_unsaved_translation_is_not_implicitly_saved(harness):
    local = bridge._snapshot(harness).sources[0].entries[0]
    active = harness.services.project_lifecycle.active
    disk = harness.services.variants.read_snapshot(active.formal_variant_ref)
    assert harness.services.gui_project_commands.update_entry(
        local.identity, harness.request_context, translation="未保存工作译文", stage=5
    ).is_success
    assert active.dirty
    assert bridge._upload(harness).success_count == 2
    assert active.dirty
    assert harness.services.variants.read_snapshot(active.formal_variant_ref) == disk


def test_cancel_after_file_create_does_not_touch_local_metadata(harness, monkeypatch):
    before = bridge._snapshot(harness)
    create = harness.remote.create_file

    def cancel(*args):
        result = create(*args)
        harness.cancel.set()
        return result

    monkeypatch.setattr(harness.remote, "create_file", cancel)
    result = bridge._upload(harness)
    assert result.success_count == result.cancelled_count == 1
    assert bridge._snapshot(harness) == before


def test_background_edit_is_preserved_and_stops_later_files(harness, monkeypatch):
    local = bridge._snapshot(harness).sources[0].entries[0]
    create = harness.remote.create_file

    def edit(*args):
        result = create(*args)
        assert harness.services.gui_project_commands.update_entry(
            local.identity, harness.request_context, translation="后台新编辑", stage=3
        ).is_success
        return result

    monkeypatch.setattr(harness.remote, "create_file", edit)
    result = bridge._upload(harness)
    assert result.success_count == result.pending_count == 1
    current = bridge._snapshot(harness).sources[0].entries[0]
    assert (current.translation, current.stage) == ("后台新编辑", 3)

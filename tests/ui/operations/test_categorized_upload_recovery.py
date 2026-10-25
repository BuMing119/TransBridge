"""Current paths recover replacements without persistent cloud identities."""

from dataclasses import replace

from tests.ui.operations import test_categorized_upload_bridge as bridge

project_fixture = bridge.project_fixture
harness = bridge.harness


def test_deleted_files_are_recreated_on_next_upload(harness):
    before = bridge._snapshot(harness)
    assert bridge._upload(harness).success_count == 2
    old_ids = set(harness.remote.files)
    harness.remote.files.clear()
    assert bridge._upload(harness).success_count == 2
    assert not old_ids.intersection(harness.remote.files)
    assert bridge._snapshot(harness) == before


def test_same_path_replacement_uses_current_id_without_review(harness):
    assert bridge._upload(harness).success_count == 2
    old_id, old = next(iter(harness.remote.files.items()))
    del harness.remote.files[old_id]
    harness.remote.files[99] = replace(old, file_id=99, entries=tuple(replace(row, file_id=99) for row in old.entries))
    harness.remote.writes.clear()
    assert bridge._upload(harness).success_count == 2
    assert any(write[1] == 99 for write in harness.remote.writes)
    assert all(write[1] != old_id for write in harness.remote.writes)


def test_lost_response_is_not_replayed_and_next_click_uses_inventory(harness):
    harness.remote.timeout_after_create = True
    result = bridge._upload(harness)
    assert result.success_count == result.failed_count == 1
    assert len(harness.remote.files) == 2
    assert bridge._upload(harness).success_count == 2
    assert sum(write[0] == "file" for write in harness.remote.writes) == 2


def test_moved_file_is_not_followed_by_old_id(harness):
    assert bridge._upload(harness).success_count == 2
    old_id, old = next(iter(harness.remote.files.items()))
    moved = replace(old, path="Other/人名.json")
    harness.remote.files[old_id] = moved
    harness.remote.writes.clear()
    assert bridge._upload(harness).success_count == 2
    assert harness.remote.files[old_id] == moved
    assert all(write[1] != old_id for write in harness.remote.writes)

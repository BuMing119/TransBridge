import pytest

from tests.persistence.v2.test_saved_project_sources import CountingPreparer, _services, saved as saved_fixture

saved = saved_fixture


@pytest.mark.parametrize("consumer", ["upload", "result_merge"])
def test_cold_consumers_use_registered_source_after_original_is_removed(saved, consumer):
    root, source, _path, context, _expected = saved
    source.unlink()
    preparer = CountingPreparer(forbidden=True)
    services = _services(root, preparer)
    try:
        prepared = services.current_project_opener.prepare_active(context)
        assert prepared.is_success, prepared.diagnostics
        opened = services.current_project_opener.activate(prepared.value, context)
        assert opened.is_success, opened.diagnostics
        active = services.project_lifecycle.active
        snapshot = active.variant.snapshot()
        if consumer == "upload":
            result = services.gui_project_commands.categorized_upload_snapshot(
                context, endpoint="https://paratranz.cn", remote_project_id=42
            )
            assert result.is_success, result.diagnostics
            assert len(result.value.sources) == 1
            assert len(result.value.sources[0].entries) == len(snapshot.entries)
        else:
            service = services.gui_project_commands._categorized_uploads
            keys = {entry.entry_key for entry in snapshot.entries}
            texts = service.result_source_texts(active.project, snapshot, keys, context)
            assert set(texts) == keys
            assert all(value[0] for value in texts.values())
        assert preparer.calls == 0
    finally:
        services.close()

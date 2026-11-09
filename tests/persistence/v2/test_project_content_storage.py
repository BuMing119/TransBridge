"""SQLite project reads and translation-only saves use extracted content."""

from tests.persistence.v2.test_saved_project_sources import CountingPreparer, _services, saved as saved_fixture
from transbridge.persistence.project_store.repository import ProjectStore

saved = saved_fixture


def test_translation_and_clean_saves_never_read_original_source(saved):
    root, source, path, context, expected = saved
    source.unlink()
    preparer = CountingPreparer(forbidden=True)
    services = _services(root, preparer)
    try:
        commands = services.gui_project_commands
        assert services.current_project_opener.open_path(path, context).is_success
        active = services.project_lifecycle.active
        assert commands.update_entry(expected.entries[0].entry_key, context, translation="Edited offline").is_success
        assert commands.save(context).is_success
        store = ProjectStore(path)
        saved_summary = store.summary()
        assert commands.save(context).is_success
        assert store.summary() == saved_summary
        assert (
            services.variants.read_snapshot(active.formal_variant_ref).envelope.data["entries"][0]["translation"]
            == "Edited offline"
        )
        assert preparer.calls == 0
        assert not (root / "source_artifacts").exists()
    finally:
        services.close()

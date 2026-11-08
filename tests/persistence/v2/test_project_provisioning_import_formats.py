from uuid import uuid4

import pytest

from tests.source_format_fixtures import write_translation_source
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services


def _services(root):
    return build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-11-07T12:00:00+08:00"
    )


@pytest.mark.parametrize("kind", ["json.transbridge", "json.dsd", "sst.ssu8", "sst.ssu9", "binary.eet"])
def test_import_formats_create_save_and_reopen_without_original_file(tmp_path, kind):
    path = write_translation_source(tmp_path, kind)
    context = RequestContext("gui", run_id="import-format-creation")
    root = tmp_path / "data"
    services = _services(root)
    try:
        prepared = services.gui_project_commands.prepare_create(
            ProjectProvisioningRequest("已有译文", migration_sources=(ProjectSourceRequest(str(path)),)), context
        )
        assert prepared.is_success and prepared.value is not None, prepared.diagnostics
        assert prepared.value["entry_count"] == 1
        committed = services.gui_project_commands.commit_create(prepared.value["token"], context)
        assert committed.is_success, committed.diagnostics
        hydration = services.gui_project_commands.consume_create_hydration(prepared.value["project_id"], context)
        assert hydration.is_success
        source = hydration.value.sources[0]
        assert source.format_id.value == kind
        assert [(entry.original, entry.translation) for entry in source.entries] == [("Hello", "你好")]
        snapshot = services.project_lifecycle.active.variant.snapshot()
        assert services.gui_project_commands.save(context).is_success
    finally:
        services.close()
    path.unlink()
    services = _services(root)
    try:
        prepared = services.current_project_opener.prepare_active(context)
        assert prepared.is_success and prepared.value is not None, prepared.diagnostics
        assert services.current_project_opener.activate(prepared.value, context).is_success
        assert services.project_lifecycle.active.variant.snapshot().entries == snapshot.entries
        assert services.project_lifecycle.active.variant.snapshot().source_fingerprints == snapshot.source_fingerprints
    finally:
        services.close()


def test_malformed_binary_eet_never_creates_a_project(tmp_path):
    path = tmp_path / "translated.eet"
    path.write_bytes(b"EET_" + b"\0" * 12)
    services = _services(tmp_path / "data")
    try:
        result = services.gui_project_commands.prepare_create(
            ProjectProvisioningRequest("二进制译文", migration_sources=(ProjectSourceRequest(str(path)),)),
            RequestContext("gui"),
        )
        assert not result.is_success
        assert result.diagnostics[0].code == "EET_PARSE_FAILED"
        assert services.project_lifecycle.active is None
    finally:
        services.close()

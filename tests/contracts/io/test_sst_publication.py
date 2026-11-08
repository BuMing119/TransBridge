"""Native SST identities survive saved projects and guarded publication."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.contracts.io.test_sst_adapter import _parse
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.publish import ImmediateCommitGuard, OsPublishFilesystem
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.parser.xt.sst_codec import SstDocument


def _services(root):
    return build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-11-08T12:00:00+08:00"
    )


def _run(checked, filesystem=None):
    run_id = checked.draft.context.run_id
    return HydratedWriteWorkload(checked, filesystem=filesystem)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id=run_id),
            cancellation=None,
            publish_commit_guard=lambda: ImmediateCommitGuard(run_id),
        )
    )


@pytest.mark.parametrize("format_id", [FormatId.SST_SSU8, FormatId.SST_SSU9])
def test_saved_sst_reopens_and_publishes_without_external_source(tmp_path, format_id):
    source, document, _ = _parse(tmp_path, format_id)
    root = tmp_path / "project-data"
    context = RequestContext("sst-publication", run_id="native-sst-reopened")
    services = _services(root)
    try:
        prepared = services.gui_project_commands.prepare_create(
            ProjectProvisioningRequest("SST source", migration_sources=(ProjectSourceRequest(str(source)),)), context
        )
        assert prepared.is_success, prepared.diagnostics
        committed = services.gui_project_commands.commit_create(prepared.value["token"], context)
        assert committed.is_success, committed.diagnostics
        assert services.gui_project_commands.save(context).is_success
    finally:
        services.close()
    source.unlink()

    reopened = _services(root)
    try:
        prepared = reopened.current_project_opener.prepare_active(context)
        assert prepared.is_success, prepared.diagnostics
        assert prepared.value.recovery is None
        hydration = prepared.value.hydrations[0]
        assert hydration.source_snapshot.content == document.to_bytes()
        assert dict(hydration.source_snapshot.metadata)["sst_codec_version"] == 2
        assert [entry.entry_key.local_key for entry in hydration.entries] == ["sst-v2:0", "sst-v2:1"]
        edited = replace(hydration.entries[0], translation="Reopened translation 😀", stage=1)
        target = tmp_path / "translated.sst"
        checked = HydratedWritePreflightService().preflight(
            HydratedWriteDraft(hydration.source_snapshot, format_id, (edited,), str(target), 1, context)
        )
        assert checked.ready, checked.checks
        result = _run(checked)
        assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
        actual = SstDocument.from_bytes(target.read_bytes())
        expected = replace(
            document,
            records=(replace(document.records[0], translation="Reopened translation 😀"), document.records[1]),
        )
        assert actual == expected
        assert not source.exists()
        assert not tuple(tmp_path.glob(".*.stage"))
        assert not tuple(tmp_path.glob(".*.tmp"))
    finally:
        reopened.close()


@pytest.mark.parametrize("legacy_part", ["snapshot", "entry"])
def test_old_saved_identity_is_blocked_at_preflight_without_writing(tmp_path, legacy_part):
    source, _, parsed = _parse(tmp_path)
    source.unlink()
    snapshot = parsed.source_snapshot
    entries = tuple(entry.snapshot() for entry in parsed.entries)
    if legacy_part == "snapshot":
        snapshot = replace(
            snapshot, metadata=tuple(pair for pair in snapshot.metadata if pair[0] != "sst_codec_version")
        )
    else:
        entries = tuple(replace(entry, metadata=()) for entry in entries)
    target = tmp_path / "translated.sst"
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(snapshot, parsed.format_id, entries, str(target), 0, RequestContext("sst-legacy"))
    )
    assert not checked.ready
    assert any(check.code == "SST_REIMPORT_REQUIRED" and not check.passed for check in checked.checks)
    assert not target.exists()


@pytest.mark.parametrize("corruption", ["header", "unselected-record"])
def test_full_fidelity_rejects_corruption_outside_selected_translation(tmp_path, corruption):
    source, _, parsed = _parse(tmp_path)
    source.unlink()
    target = tmp_path / "translated.sst"
    context = RequestContext("sst-fidelity", run_id="sst-fidelity-run")
    entry = replace(parsed.entries[0], translation="Changed", stage=1)
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(parsed.source_snapshot, parsed.format_id, (entry,), str(target), 1, context)
    )
    assert checked.ready, checked.checks

    class CorruptingFilesystem(OsPublishFilesystem):
        def fsync_file(self, path):
            document = SstDocument.from_bytes(Path(path).read_bytes())
            if corruption == "header":
                document = replace(document, flag=document.flag ^ 1)
            else:
                document = replace(document, records=(document.records[0], replace(document.records[1], edid_hash=0)))
            Path(path).write_bytes(document.to_bytes())
            super().fsync_file(path)

    result = _run(checked, CorruptingFilesystem())
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "SST_FIDELITY_MISMATCH"
    assert not target.exists()
    assert not tuple(tmp_path.glob(".*.stage"))

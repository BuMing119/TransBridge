"""EET publication verifies the complete file before a guarded replacement."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tests.contracts.io.test_eet_adapter import _parse, _write_request
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.eet_adapter import EetAdapter
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.publish import (
    FormatAdapterRenderer,
    FormatRoundTripValidator,
    ImmediateCommitGuard,
    OsPublishFilesystem,
    PublishCoordinator,
    PublishTarget,
)
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.parser.eet_binary_codec import EetDocument
from transbridge.parser.eet_number_cache import EetNumberCache


def _publish(request, *, filesystem=None, validator=None):
    adapter = EetAdapter()
    filesystem = filesystem or OsPublishFilesystem()
    return PublishCoordinator(filesystem).publish(
        request,
        PublishTarget(request.target.uri),
        renderer=FormatAdapterRenderer(adapter),
        validator=validator or FormatRoundTripValidator(adapter, filesystem),
        commit_guard=ImmediateCommitGuard(request.context.run_id),
    )


def test_snapshot_publication_preserves_unselected_records_and_backs_up_target(tmp_path):
    source, document, parsed = _parse(tmp_path)
    source.unlink()
    target = tmp_path / "output.eet"
    target.write_bytes(b"previous target")
    entry = replace(parsed.entries[1], translation="Changed 25", stage=1)
    request = _write_request(tmp_path, parsed, (entry,), options=(("source_authority", "hydration-v2"),))
    result = _publish(request)
    assert result.outcome is OperationOutcome.COMPLETED, (result.code, result.message)
    assert result.validation.fidelity_valid
    assert EetDocument.from_bytes(target.read_bytes()) == document.with_translation(1, "Changed 25")
    assert Path(result.backup_path).read_bytes() == b"previous target"
    assert not tuple(tmp_path.glob(".*.stage"))
    assert not tuple(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    "corruption", ["header", "unselected-record", "original-cache", "translation-cache", "phrases"]
)
def test_full_fidelity_detects_changes_beyond_selected_translation(tmp_path, corruption):
    _, _, parsed = _parse(tmp_path)
    target = tmp_path / "output.eet"
    target.write_bytes(b"existing")
    entry = replace(parsed.entries[0], translation="Updated 9", stage=1)
    request = _write_request(tmp_path, parsed, (entry,))

    class CorruptingFilesystem(OsPublishFilesystem):
        def fsync_file(self, path):
            document = EetDocument.from_bytes(Path(path).read_bytes())
            if corruption == "header":
                document = replace(document, game="different game")
            elif corruption == "phrases":
                document = replace(document, phrases=())
            else:
                changes = {"icon": 22}
                if corruption == "original-cache":
                    changes = {"original_variables": b""}
                elif corruption == "translation-cache":
                    changes = {"translation_variables": EetNumberCache("Forged %VAR00%", ("0",)).to_payload()}
                document = replace(document, records=(document.records[0], replace(document.records[1], **changes)))
            Path(path).write_bytes(document.to_bytes())
            super().fsync_file(path)

    result = _publish(request, filesystem=CorruptingFilesystem())
    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "EET_FIDELITY_MISMATCH"
    assert target.read_bytes() == b"existing"
    assert not tuple(tmp_path.glob(".*.stage"))


def test_cancellation_during_fidelity_keeps_target_and_removes_stage(tmp_path):
    _, _, parsed = _parse(tmp_path)
    target = tmp_path / "output.eet"
    target.write_bytes(b"existing")
    token = SimpleNamespace(is_cancelled=False)
    filesystem = OsPublishFilesystem()

    class CancelValidator:
        def validate(self, request, staging_path):
            token.is_cancelled = True
            return FormatRoundTripValidator(EetAdapter(), filesystem).validate(request, staging_path)

    result = _publish(_write_request(tmp_path, parsed, cancellation=token), validator=CancelValidator())
    assert result.outcome is OperationOutcome.CANCELLED
    assert target.read_bytes() == b"existing"
    assert not tuple(tmp_path.glob(".*.stage"))


def test_malformed_stage_fails_full_validation_without_publishing(tmp_path):
    _, _, parsed = _parse(tmp_path)

    class TruncatingFilesystem(OsPublishFilesystem):
        def fsync_file(self, path):
            Path(path).write_bytes(Path(path).read_bytes()[:-1])
            super().fsync_file(path)

    result = _publish(_write_request(tmp_path, parsed), filesystem=TruncatingFilesystem())
    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "EET_FIDELITY_FAILED"
    assert not (tmp_path / "output.eet").exists()
    assert not tuple(tmp_path.glob(".*.stage"))


def test_saved_binary_eet_reopens_and_publishes_from_captured_source(tmp_path):
    source, document, _ = _parse(tmp_path)
    root = tmp_path / "project-data"
    context = RequestContext("eet-publication", run_id="eet-reopened")

    def services():
        return build_persistence_v2_services(
            root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-11-08T12:00:00+08:00"
        )

    initial = services()
    try:
        prepared = initial.gui_project_commands.prepare_create(
            ProjectProvisioningRequest("Binary EET", migration_sources=(ProjectSourceRequest(str(source)),)), context
        )
        assert prepared.is_success, prepared.diagnostics
        committed = initial.gui_project_commands.commit_create(prepared.value["token"], context)
        assert committed.is_success, committed.diagnostics
        assert initial.gui_project_commands.save(context).is_success
    finally:
        initial.close()
    source.unlink()

    reopened = services()
    try:
        loaded = reopened.current_project_opener.prepare_active(context)
        assert loaded.is_success, loaded.diagnostics
        assert loaded.value.recovery is None
        hydration = loaded.value.hydrations[0]
        assert hydration.source_snapshot.content == document.to_bytes()
        assert [entry.entry_key.local_key for entry in hydration.entries] == ["eet-v1:0", "eet-v1:1"]
        target = tmp_path / "reopened.eet"
        entry = replace(hydration.entries[1], translation="Saved project 75", stage=1)
        checked = HydratedWritePreflightService().preflight(
            HydratedWriteDraft(hydration.source_snapshot, hydration.format_id, (entry,), str(target), 1, context)
        )
        assert checked.ready, checked.checks
        result = HydratedWriteWorkload(checked)(
            SimpleNamespace(
                ref=SimpleNamespace(run_id=context.run_id),
                cancellation=None,
                publish_commit_guard=lambda: ImmediateCommitGuard(context.run_id),
            )
        )
        assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
        assert EetDocument.from_bytes(target.read_bytes()) == document.with_translation(1, "Saved project 75")
        assert not source.exists()
    finally:
        reopened.close()


@pytest.mark.parametrize("legacy_part", ["snapshot", "entry"])
def test_legacy_eet_identity_is_blocked_in_hydrated_preflight(tmp_path, legacy_part):
    source, _, parsed = _parse(tmp_path)
    source.unlink()
    snapshot = parsed.source_snapshot
    entries = tuple(entry.snapshot() for entry in parsed.entries)
    if legacy_part == "snapshot":
        snapshot = replace(snapshot, metadata=())
    else:
        entries = tuple(replace(entry, metadata=()) for entry in entries)
    target = tmp_path / "legacy.eet"
    checked = HydratedWritePreflightService().preflight(
        HydratedWriteDraft(snapshot, parsed.format_id, entries, str(target), 0, RequestContext("eet-legacy"))
    )
    assert not checked.ready
    assert any(check.code == "EET_REIMPORT_REQUIRED" and not check.passed for check in checked.checks)
    assert not target.exists()

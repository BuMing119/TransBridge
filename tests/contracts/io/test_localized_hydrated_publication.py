from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId, ParseRequest, SourceDescriptor, TranslationIoUseCase
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflightService,
    HydratedWriteWorkload,
)
from transbridge.application.io.publish import BackupPolicy, ConflictPolicy, OsPublishFilesystem
from transbridge.application.tasks import CallbackThreadBackend, OwnerRef, TaskRuntime
from transbridge.ui.operations import OperationKind, OperationTaskAdapter, OperationTaskRequest
from transbridge.ui.source_hydration import slot_from_hydration


def _field(kind, value):
    return struct.pack("<4sH", kind.encode(), len(value)) + value


def _record(kind, identifier, value, flags=0):
    return struct.pack("<4sIIIIHH", kind.encode(), len(value), flags, identifier, 0, 44, 0) + value


@pytest.fixture
def localized(tmp_path):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    strings = source_dir / "Strings"
    strings.mkdir()
    source = source_dir / "localized.esp"
    weapon = _record("WEAP", 0x800, _field("EDID", b"Weapon\0") + _field("FULL", struct.pack("<I", 1)))
    source.write_bytes(
        _record("TES4", 0, _field("HEDR", struct.pack("<fII", 1.7, 1, 0x801)), 0x80)
        + struct.pack("<4sI4sIHHHH", b"GRUP", len(weapon) + 24, b"WEAP", 0, 0, 0, 0, 0)
        + weapon
    )
    for identifier, suffix in enumerate(("strings", "dlstrings", "ilstrings"), 1):
        text = f"Original {identifier}".encode() + b"\0"
        payload = text if suffix == "strings" else struct.pack("<I", len(text)) + text
        (strings / f"localized_English.{suffix}").write_bytes(
            struct.pack("<IIII", 1, len(payload), identifier, 0) + payload
        )
    parsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(source)), RequestContext("test"), FormatId.PLUGIN_SSE)
    )
    assert parsed.outcome is OperationOutcome.COMPLETED
    assert parsed.entries[0].string_id == 1
    output = tmp_path / "output"
    output.mkdir()
    return SimpleNamespace(source=source, parsed=parsed, output=output, target=output / "translated.esp")


def _preflight(case, translation="Translated", overwrite=False):
    # Both production hydration and create_write freeze/reconstruct these values.
    hydration = SimpleNamespace(
        location=str(case.source),
        format_id=FormatId.PLUGIN_SSE,
        source_snapshot=case.parsed.source_snapshot,
        entries=tuple(entry.snapshot() for entry in case.parsed.entries),
    )
    slot = slot_from_hydration(hydration)
    assert next(iter(slot.collection)).string_id == 1
    entries = tuple(
        replace(entry, translation=translation, stage=1 if translation else 0).snapshot() for entry in slot.collection
    )
    return HydratedWritePreflightService().preflight(
        HydratedWriteDraft(
            slot.source_snapshot,
            slot.format_id,
            entries,
            str(case.target),
            1,
            RequestContext("gui"),
            ConflictPolicy.EXPLICIT_OVERWRITE if overwrite else ConflictPolicy.FAIL,
            BackupPolicy.REQUIRED_IF_EXISTS,
        )
    )


def _run(checked, filesystem=None):
    ids = iter(str(index) for index in range(20))
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: next(ids)),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=CallbackThreadBackend(lambda _run, callback: callback()),
    )
    adapter = OperationTaskAdapter(runtime)
    owner = OwnerRef("gui", "test")
    ref = adapter.submit(
        OperationTaskRequest(
            OperationKind.WRITE,
            checked.request_digest,
            "variant",
            "write",
            HydratedWriteWorkload(checked, filesystem=filesystem),
            True,
            (checked.draft.target_path,),
        ),
        owner,
    )
    return adapter.result(ref, owner)


@pytest.mark.parametrize("translation", ["Translated", ""])
def test_localized_production_hydration_publishes_every_companion_with_final_name(localized, translation):
    checked = _preflight(localized, translation)
    assert checked.ready
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    expected = {
        localized.target,
        *(
            localized.output / "Strings" / f"translated_English.{suffix}"
            for suffix in ("strings", "dlstrings", "ilstrings")
        ),
    }
    assert {Path(path) for path in result.artifact_refs} == expected
    assert {path for path in localized.output.rglob("*") if path.is_file()} == expected
    reparsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(localized.target)), RequestContext("verify"), FormatId.PLUGIN_SSE)
    )
    assert reparsed.entries[0].original == (translation or "Original 1")


@pytest.mark.parametrize("removed", [False, True])
def test_plugin_and_strings_write_uses_confirmed_bytes_after_source_changes(localized, removed):
    checked = _preflight(localized)
    for path in localized.source.parent.rglob("*"):
        if path.is_file():
            path.unlink() if removed else path.write_bytes(b"unconfirmed changed source")
    result = _run(checked)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    reparsed = TranslationIoUseCase().parse(
        ParseRequest(SourceDescriptor(str(localized.target)), RequestContext("verify"), FormatId.PLUGIN_SSE)
    )
    assert reparsed.entries[0].original == "Translated"


def _old_outputs(case):
    (case.output / "Strings").mkdir()
    originals = {case.target: b"old plugin"}
    originals.update({
        case.output / "Strings" / f"translated_English.{suffix}": f"old {suffix}".encode()
        for suffix in ("strings", "dlstrings", "ilstrings")
    })
    for path, content in originals.items():
        path.write_bytes(content)
    return originals


def test_companion_alone_requires_overwrite_confirmation(localized):
    originals = _old_outputs(localized)
    localized.target.unlink()
    checked = _preflight(localized)
    assert not checked.ready
    assert not next(item for item in checked.checks if item.code == "OVERWRITE_CONFIRMED").passed
    assert (localized.output / "Strings" / "translated_English.strings").read_bytes() == originals[
        localized.output / "Strings" / "translated_English.strings"
    ]


def test_changed_companion_after_preflight_blocks_every_write(localized):
    originals = _old_outputs(localized)
    checked = _preflight(localized, overwrite=True)
    changed = localized.output / "Strings" / "translated_English.strings"
    originals[changed] = b"new external content"
    changed.write_bytes(originals[changed])
    result = _run(checked)
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "TARGET_FINGERPRINT_CONFLICT"
    assert all(path.read_bytes() == content for path, content in originals.items())


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("after_replace", [False, True])
def test_failure_on_last_plugin_replace_rolls_back_the_entire_bundle(localized, existing, after_replace):
    originals = _old_outputs(localized) if existing else {}
    checked = _preflight(localized, overwrite=existing)

    class FailingFilesystem(OsPublishFilesystem):
        failed = False

        def atomic_replace(self, source, destination):
            if Path(destination) == localized.target and not self.failed:
                self.failed = True
                if after_replace:
                    super().atomic_replace(source, destination)
                raise OSError("injected plugin commit failure")
            super().atomic_replace(source, destination)

    result = _run(checked, FailingFilesystem())
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "BUNDLE_COMMIT_FAILED"
    assert {path for path in localized.output.rglob("*") if path.is_file()} == set(originals)
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert not tuple(localized.output.glob(".transbridge-publish-*"))


def test_successful_overwrite_keeps_verified_backups_for_all_members(localized):
    originals = _old_outputs(localized)
    result = _run(_preflight(localized, overwrite=True))
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    backups = result.value["backup_paths"]
    assert len(backups) == len(originals)
    assert {Path(path).read_bytes() for path in backups} == set(originals.values())


def test_staging_failure_leaves_existing_outputs_unchanged(localized):
    originals = _old_outputs(localized)

    class FailingFilesystem(OsPublishFilesystem):
        def fsync_file(self, path):
            raise OSError("injected staging flush failure")

    result = _run(_preflight(localized, overwrite=True), FailingFilesystem())
    assert result.outcome is OperationOutcome.FAILED
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert {path for path in localized.output.rglob("*") if path.is_file()} == set(originals)


def test_rollback_failure_reports_and_retains_recovery_copies(localized):
    _old_outputs(localized)

    class FailingFilesystem(OsPublishFilesystem):
        rolling_back = False

        def atomic_replace(self, source, destination):
            if Path(destination) == localized.target:
                self.rolling_back = True
                raise OSError("plugin commit failed")
            if self.rolling_back:
                raise OSError("rollback failed")
            super().atomic_replace(source, destination)

    result = _run(_preflight(localized, overwrite=True), FailingFilesystem())
    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].code == "BUNDLE_ROLLBACK_FAILED"
    recovery = Path(dict(result.diagnostics[0].details)["recovery_directory"])
    assert recovery.is_dir()
    assert any(path.read_bytes() == b"old plugin" for path in recovery.glob("rollback-*"))


def test_cancellation_after_staging_publishes_nothing(localized):
    from transbridge.application.io import SsePluginAdapter, WriteRequest
    from transbridge.application.io.publish.guards import ImmediateCommitGuard
    from transbridge.application.io.publish.plugin_bundle import PluginBundlePublisher
    from transbridge.application.tasks.controls import CancellationToken

    token = CancellationToken()

    class CancellingFilesystem(OsPublishFilesystem):
        def fsync_file(self, path):
            super().fsync_file(path)
            token._cancel("test cancellation after staging")

    checked = _preflight(localized)
    request = WriteRequest(
        SourceDescriptor(str(localized.target)),
        FormatId.PLUGIN_SSE,
        tuple(replace(entry, translation="Translated", stage=1) for entry in localized.parsed.entries),
        1,
        RequestContext("gui", run_id="cancel-run"),
        source_snapshot=localized.parsed.source_snapshot,
        options=(("source_authority", "hydration-v2"),),
        cancellation=token,
    )
    result = PluginBundlePublisher(CancellingFilesystem(), SsePluginAdapter()).publish(
        request,
        checked.artifact_fingerprints,
        conflict_policy=ConflictPolicy.FAIL,
        backup_policy=BackupPolicy.REQUIRED_IF_EXISTS,
        commit_guard=ImmediateCommitGuard("cancel-run"),
    )
    assert result.outcome is OperationOutcome.CANCELLED
    assert not tuple(localized.output.iterdir())

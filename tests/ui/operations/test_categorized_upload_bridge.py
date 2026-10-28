"""Real project capture and file publication without local upload commits."""

from dataclasses import replace
import json
import logging
from threading import Event
from types import SimpleNamespace

import pytest

from tests.application.projects import test_categorized_upload as project_tests
from transbridge.application.categorized_upload import RemoteEntry, RemoteFile
from transbridge.application.file_upload import FileWriteResult
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.projects.remote_binding import ParaTranzTargetStatus
from transbridge.application.projects.variant_commands import EntryStatePatch
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.operations.categorized_upload_bridge import CategorizedUploadSession
from transbridge.ui.operations.categorized_upload_dialog import Selection

project_fixture = project_tests.project


class _Remote:
    def __init__(self):
        self.files, self.writes, self.reads = {}, [], []
        self.closed, self.next_file, self.next_entry = 0, 11, 101
        self.timeout_after_create = False
        self.allowed = True

    def gateway(self, _config, *, cancel, status=None):
        self.cancellation = cancel
        return self

    def inventory(self, project_id):
        assert project_id == 42
        if not self.allowed:
            raise PermissionError("当前账号没有访问权限")
        self.reads.append(("inventory", project_id))
        return {file.path: file.file_id for file in self.files.values()}

    def create(self, project_id, path, records):
        entries = tuple(
            TranslationEntry(
                row["key"], row["key"], row["original"], row["translation"], row["stage"], row.get("context")
            )
            for row in records
        )
        result = self.create_file(project_id, path, entries)
        return FileWriteResult(file_id=result.file_id)

    def create_file(self, project_id, path, entries):
        assert project_id == 42 and entries
        file_id = self.next_file
        self.next_file += 1
        rows = tuple(
            RemoteEntry(
                self.next_entry + index,
                file_id,
                entry.key,
                entry.original,
                entry.translation,
                entry.context or "",
                entry.stage,
            )
            for index, entry in enumerate(entries)
        )
        self.next_entry += len(rows)
        result = self.files[file_id] = RemoteFile(file_id, path, rows)
        self.writes.append(("file", file_id, path))
        if self.timeout_after_create:
            self.timeout_after_create = False
            raise TimeoutError("response lost after file import")
        return result

    def update(self, project_id, file_id, path, records):
        file = self.files[file_id]
        current = {entry.key: entry for entry in file.entries}
        changed = False
        for row in records:
            previous = current.get(row["key"])
            if previous is None:
                current[row["key"]] = RemoteEntry(self.next_entry, file_id, row["key"], row["original"], "", "", 0)
                self.next_entry += 1
                changed = True
            else:
                updated = replace(previous, original=row["original"], context=row.get("context", ""))
                changed |= updated != previous
                current[row["key"]] = updated
        self.files[file_id] = replace(file, entries=tuple(current.values()))
        self.writes.append(("source", file_id, path))
        return FileWriteResult("applied" if changed else "hashMatched", file_id)

    def translate(self, project_id, file_id, path, records, *, force):
        file = self.files[file_id]
        incoming = {row["key"]: row for row in records}
        entries, changed = [], False
        for entry in file.entries:
            row = incoming.get(entry.key)
            if row and (force or entry.stage < 3) and (force or row["translation"]):
                updated = replace(entry, translation=row["translation"], stage=row["stage"])
                changed |= updated != entry
                entry = updated
            entries.append(entry)
        self.files[file_id] = replace(file, entries=tuple(entries))
        self.writes.append(("translations", file_id, path))
        return FileWriteResult("applied" if changed else "unchanged", file_id)

    def close(self):
        self.closed += 1


@pytest.fixture
def harness(project_fixture):
    services, _service, request_context, first, second = project_fixture
    active = services.project_lifecycle.active
    target = SimpleNamespace(
        project_id=42,
        project_name="Remote",
        endpoint="https://paratranz.cn/api",
        account_user_id=7,
        status=ParaTranzTargetStatus.AVAILABLE,
        reason="",
    )
    context = SimpleNamespace(
        project_commands=services.gui_project_commands,
        uses_authoritative_projection=True,
        runtime_context=request_context,
        active_project_id=active.project_ref.identity.value,
        active_variant_id=active.formal_variant_ref.identity.value,
        config=SimpleNamespace(config_revision=0),
        resolve_paratranz_target=lambda: target,
    )
    remote = _Remote()
    session = CategorizedUploadSession(context, gateway_factory=remote.gateway)
    return SimpleNamespace(
        services=services,
        request_context=request_context,
        context=context,
        remote=remote,
        session=session,
        first=first,
        second=second,
        cancel=Event(),
    )


def _progress(*_args):
    pass


def _selection(harness, mode="orig_only"):
    sources = harness.session.load_preview(_progress, harness.cancel)
    return Selection(
        tuple(source.source_id for source in sources),
        tuple(file.file_key for source in sources for file in source.files),
        translation_mode=mode,
    )


def _upload(harness, selection=None):
    return harness.session.upload(selection or _selection(harness), _progress, harness.cancel)


def _snapshot(harness):
    result = harness.services.gui_project_commands.categorized_upload_snapshot(
        harness.request_context, endpoint="https://paratranz.cn", remote_project_id=42
    )
    assert result.is_success, result.diagnostics
    return result.value


def test_local_selection_does_not_read_cloud_and_upload_is_file_scoped(harness):
    selection = _selection(harness)
    assert harness.remote.reads == harness.remote.writes == []
    result = _upload(harness, selection)
    assert result.success_count == 2 and result.failed_count == 0
    assert harness.remote.reads == [("inventory", 42)]
    assert len(harness.remote.files) == 2


@pytest.mark.parametrize("categorized", [False, True])
def test_both_layouts_share_executor(harness, categorized):
    harness.session.categorized = categorized
    assert _upload(harness).success_count == 2
    assert all(("/" in file.path) == categorized for file in harness.remote.files.values())


def test_upload_does_not_change_local_project_or_persist_ids(harness):
    before = _snapshot(harness)
    generation = harness.services.project_lifecycle.generation
    active = harness.services.project_lifecycle.active
    disk = harness.services.variants.read_snapshot(active.formal_variant_ref)
    assert _upload(harness).success_count == 2
    assert _snapshot(harness) == before
    assert harness.services.project_lifecycle.generation == generation
    assert harness.services.variants.read_snapshot(active.formal_variant_ref) == disk


def test_repeated_upload_reuses_current_files_with_server_noops(harness):
    assert _upload(harness).success_count == 2
    ids = set(harness.remote.files)
    assert _upload(harness).success_count == 2
    assert set(harness.remote.files) == ids
    assert {row.status for row in harness.session.last_receipt.files} == {"unchanged"}


def test_overlapping_upload_is_rejected_but_later_upload_allowed(harness, monkeypatch):
    create = harness.remote.create_file

    def overlap(*args):
        with pytest.raises(RuntimeError, match="正在执行"):
            _upload(harness)
        return create(*args)

    monkeypatch.setattr(harness.remote, "create_file", overlap)
    assert _upload(harness).success_count == 2
    assert _upload(harness).success_count == 2


def test_permission_failure_releases_guard(harness):
    harness.remote.allowed = False
    with pytest.raises(PermissionError):
        _upload(harness)
    harness.remote.allowed = True
    assert _upload(harness).success_count == 2


def test_cancel_before_upload_has_no_remote_access(harness):
    selection = _selection(harness)
    harness.cancel.set()
    result = _upload(harness, selection)
    assert result.success_count == result.failed_count == 0
    assert harness.remote.reads == harness.remote.writes == []


def test_current_translation_is_captured_at_click(harness):
    selection = _selection(harness)
    source = _snapshot(harness).sources[0]
    assert harness.services.gui_project_commands.update_entry(
        source.entries[0].identity, harness.request_context, translation="选择后更新", stage=5
    ).is_success
    assert _upload(harness, selection).success_count == 2
    assert any(row.translation == "选择后更新" for file in harness.remote.files.values() for row in file.entries)


def test_single_source_never_expands_to_other_plugin(harness):
    before = _snapshot(harness)
    current, _other = before.sources
    harness.session = CategorizedUploadSession(
        harness.context, source_namespace=current.entries[0].identity.namespace, gateway_factory=harness.remote.gateway
    )
    selection = _selection(harness)
    assert selection.source_ids == (current.source_id,)
    assert _upload(harness, selection).success_count == 1
    assert _snapshot(harness) == before
    assert all(file.path.startswith(current.label + "/") for file in harness.remote.files.values())


def test_single_source_rejects_batch_before_network(harness):
    selection = _selection(harness)
    harness.session.source_namespace = _snapshot(harness).sources[0].entries[0].identity.namespace
    with pytest.raises(ValueError, match="本次上传范围"):
        _upload(harness, selection)
    assert harness.remote.reads == []


@pytest.mark.parametrize("changed", ["project", "variant", "account"])
def test_target_change_prevents_remote_access(harness, changed):
    selection = _selection(harness)
    if changed == "project":
        harness.context.active_project_id = "other"
    elif changed == "variant":
        harness.context.active_variant_id = "other"
    elif changed == "account":
        harness.context.resolve_paratranz_target().account_user_id = 8
    with pytest.raises((ValueError, RuntimeError), match="变化"):
        _upload(harness, selection)
    assert harness.remote.reads == []


def test_unrelated_config_revision_does_not_cancel_frozen_upload(harness):
    harness.context.config.config_revision += 1
    assert _upload(harness).success_count == 2


def test_diagnostics_keep_session_identity(harness, caplog):
    caplog.set_level(logging.INFO, logger="transbridge.application.categorized_upload.diagnostics")
    assert _upload(harness).success_count == 2
    records = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "transbridge.application.categorized_upload.diagnostics"
    ]
    assert {row["session_id"] for row in records} == {harness.session.session_id}
    assert any(row.get("step") == "preview_summary" for row in records)


def test_invalid_old_upload_metadata_does_not_block_file_upload(harness):
    entry = _snapshot(harness).sources[0].entries[0]
    old_ref = ExternalEntryRef("paratranz", "project:42", 999, (("endpoint", "invalid old endpoint"),))
    assert harness.services.gui_project_commands.replace_entry_records(
        {entry.identity: EntryStatePatch(entry.translation, entry.stage, (old_ref,))}, harness.request_context
    ).is_success
    assert _upload(harness).success_count == 2
    states = harness.services.project_lifecycle.active.variant.snapshot().entries
    assert next(state for state in states if state.entry_key == entry.identity).external_refs == (old_ref,)

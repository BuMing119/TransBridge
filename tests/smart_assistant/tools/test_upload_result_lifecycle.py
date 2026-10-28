from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tests.smart_assistant.tools.test_upload_cancellation_receipt import _context
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.sync.entry_upload_results import EntryUploadResultStore
from transbridge.smart_assistant.tools._entry_upload import upload_entries
from transbridge.smart_assistant.tools._entry_upload_recovery import recover_upload_associations
from transbridge.smart_assistant.tools._project_tool_mutations import ProjectToolTarget
from transbridge.smart_assistant.tools.types import ExecutionContext


class Remote:
    def __init__(self, before_return=lambda entry: None):
        self.sent = []
        self.before_return = before_return

    def upsert_entry(self, project_id, entry, **options):
        self.sent.append((project_id, entry, options))
        self.before_return(entry)
        return replace(entry, remote_id=700 + len(self.sent))


def test_upload_uses_fixed_payload_and_merges_refs_into_latest_local_state(
    current_project_factory, tmp_path, monkeypatch
):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    entries = tuple(project.context.collection)
    unrelated = ExternalEntryRef("other", "scope", "new")

    def edit(_entry):
        if len(remote.sent) == 1:
            target = ProjectToolTarget.capture(ExecutionContext(app_context=project.context))
            target.commit_records((
                replace(entries[0], translation="newer first", stage=3, external_refs=(unrelated,)),
                replace(entries[1], translation="newer second", stage=1),
            ))

    remote = Remote(edit)
    result = upload_entries({}, context, project.context.collection, remote, 7, None)

    assert result.success, result
    assert [entry.translation for _, entry, _ in remote.sent] == ["", "", ""]
    assert context._target_variant_revision == project.context.variant_revision
    assert context._target_project_revision == project.context.project_revision
    current = project.context.collection
    assert (current.get("first").translation, current.get("first").stage) == ("newer first", 3)
    assert current.get("second").translation == "newer second"
    assert unrelated in current.get("first").external_refs
    assert ExternalEntryRef("paratranz", "project:7", 701) in current.get("first").external_refs
    saved = EntryUploadResultStore(tmp_path).load(result.data["result_id"])
    assert [row["association"] for row in saved["receipts"]] == ["applied"] * 3
    assert saved["inputs"][0]["payload"]["translation"] == ""


def test_deleted_source_defers_association_and_retains_every_success(current_project_factory, tmp_path, monkeypatch):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)

    def remove(_entry):
        if len(remote.sent) == 1:
            project.install(tuple(project.context.collection)[1:])

    remote = Remote(remove)
    result = upload_entries({}, context, project.context.collection, remote, 7, None)

    assert result.partial and not result.success
    assert len(remote.sent) == 3
    assert result.data["uploaded"] == 3
    assert [row["key"] for row in result.data["deferred_associations"]] == ["first"]
    assert [
        row["remote_id"] for row in EntryUploadResultStore(tmp_path).load(result.data["result_id"])["receipts"]
    ] == [701, 702, 703]


def test_upload_associates_original_source_after_switching_visible_plugin(
    current_project_factory, tmp_path, monkeypatch
):
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.ui.context import CollectionSlot

    _, project = _context(current_project_factory, tmp_path, monkeypatch)
    rows = tuple(project.context.collection)
    source = project.context.active_slot
    source.collection = TranslationEntryCollection((rows[0],))
    context = ExecutionContext(app_context=project.context)
    other = CollectionSlot("Other", TranslationEntryCollection(rows[1:]))

    remote = Remote(lambda _entry: project.context.add_slot("other.esp", other))
    result = upload_entries({}, context, source.collection, remote, 7, None)

    assert result.success, result
    assert project.context.active_slot is other
    assert source.collection.get(rows[0].identity).external_refs == (ExternalEntryRef("paratranz", "project:7", 701),)
    assert all(not entry.external_refs for entry in other.collection)


def test_local_failure_can_recover_without_repeating_remote_effect(current_project_factory, tmp_path, monkeypatch):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    real = project.commands.replace_entry_records
    monkeypatch.setattr(
        project.commands,
        "replace_entry_records",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    remote = Remote()
    result = upload_entries({}, context, project.context.collection, remote, 7, None)
    assert result.data["uploaded"] == 3 and len(result.data["deferred_associations"]) == 3
    blocked = upload_entries(
        {}, ExecutionContext(app_context=project.context), project.context.collection, remote, 7, None
    )
    assert blocked.error_code == "UPLOAD_RECEIPT_REQUIRES_RECOVERY"
    assert len(remote.sent) == 3

    monkeypatch.setattr(project.commands, "replace_entry_records", real)
    recovered = recover_upload_associations(
        ExecutionContext(app_context=project.context), result_id=result.data["result_id"]
    )
    assert recovered.success and recovered.data["associated"] == 3
    assert len(remote.sent) == 3
    assert all(entry.external_refs for entry in project.context.collection)


def test_receipt_disk_failure_returns_complete_memory_receipt(current_project_factory, tmp_path, monkeypatch):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    save = EntryUploadResultStore.save_receipt

    def fail_confirmed(self, record, receipt):
        if receipt["outcome"] == "confirmed":
            raise OSError("write failed after acceptance")
        save(self, record, receipt)

    monkeypatch.setattr(EntryUploadResultStore, "save_receipt", fail_confirmed)
    remote = Remote()
    result = upload_entries({}, context, project.context.collection, remote, 7, None)
    assert not result.success and result.partial
    assert result.data["uploaded"] == 1 and result.data["not_attempted"] == 2
    assert not result.data["durable"]
    receipt = result.data["upload_result"]["receipts"][0]
    assert receipt["outcome"] == "confirmed" and receipt["remote_id"] == 701
    assert (
        json.loads(
            (tmp_path / "entry-upload-results" / result.data["result_id"] / "receipt-00000000.json").read_text()
        )["outcome"]
        == "in_flight"
    )
    monkeypatch.setattr(EntryUploadResultStore, "save_receipt", save)
    recovered = recover_upload_associations(
        ExecutionContext(app_context=project.context), result_id=result.data["result_id"]
    )
    assert recovered.success, recovered
    assert len(remote.sent) == 1
    assert EntryUploadResultStore(tmp_path).load(result.data["result_id"])["receipts"][0]["outcome"] == "confirmed"


def test_undo_receipt_failure_does_not_misreport_successful_associations(
    current_project_factory, tmp_path, monkeypatch
):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)

    def late_failure(_ctx, mutation):
        assert mutation().is_success
        raise OSError("undo receipt unavailable")

    monkeypatch.setattr(
        "transbridge.smart_assistant.tools._entry_upload_associations.capture_variant_command", late_failure
    )
    result = upload_entries({}, context, project.context.collection, Remote(), 7, None)
    assert result.success, result
    assert result.warnings and "undo receipt unavailable" in result.warnings[0]
    assert result.data["deferred_associations"] == []
    assert all(row["association"] == "applied" for row in result.data["upload_result"]["receipts"])
    assert all(entry.external_refs for entry in project.context.collection)


def test_uncertain_request_is_retained_and_blocks_blind_resend(current_project_factory, tmp_path, monkeypatch):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    calls = []

    def unknown(*args, **kwargs):
        calls.append(args)
        raise ExternalServiceError(ExternalServiceCategory.TIMEOUT, "Remote outcome is unknown")

    remote = SimpleNamespace(upsert_entry=unknown)
    result = upload_entries({}, context, project.context.collection, remote, 7, None)
    assert result.data["uploaded"] == 0 and result.data["not_attempted"] == 2
    assert result.data["upload_result"]["receipts"][0]["outcome"] == "unknown"
    again = upload_entries({}, context, project.context.collection, remote, 7, None)
    assert again.error_code == "UPLOAD_RECEIPT_REQUIRES_RECOVERY"
    assert len(calls) == 1


def test_unknown_remote_effect_is_not_scoped_to_local_variant(current_project_factory, tmp_path, monkeypatch):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    calls = []

    def unknown(*args, **kwargs):
        calls.append(args)
        raise ExternalServiceError(ExternalServiceCategory.TIMEOUT, "Remote outcome is unknown")

    remote = SimpleNamespace(upsert_entry=unknown)
    upload_entries({}, context, project.context.collection, remote, 7, None)
    monkeypatch.setattr(type(project.context), "active_version_identity", property(lambda _self: ("project", "other")))
    other = ExecutionContext(app_context=project.context)
    result = upload_entries({}, other, project.context.collection, remote, 7, None)
    assert result.error_code == "UPLOAD_RECEIPT_REQUIRES_RECOVERY"
    assert len(calls) == 1


def test_in_flight_receipt_exists_before_remote_send(current_project_factory, tmp_path, monkeypatch):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)

    def inspect(_entry):
        records = list((tmp_path / "entry-upload-results").glob("*/receipt-*.json"))
        assert any(json.loads(path.read_text())["outcome"] == "in_flight" for path in records)

    result = upload_entries({}, context, project.context.collection, Remote(inspect), 7, None)
    assert result.success


def test_unknown_upload_blocks_new_session_without_exposing_other_receipt(
    current_project_factory, tmp_path, monkeypatch
):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    context.request_context = replace(project.context.runtime_context, session_id="first-session")
    calls = []

    def unknown(*args, **kwargs):
        calls.append(args)
        raise ExternalServiceError(ExternalServiceCategory.TIMEOUT, "Remote outcome is unknown")

    remote = SimpleNamespace(upsert_entry=unknown)
    first = upload_entries({}, context, project.context.collection, remote, 7, None)
    other = ExecutionContext(
        app_context=project.context,
        request_context=replace(project.context.runtime_context, session_id="other-session"),
    )
    result = upload_entries({}, other, project.context.collection, remote, 7, None)
    assert result.error_code == "UPLOAD_RECEIPT_REQUIRES_RECOVERY"
    assert len(calls) == 1
    assert result.data["blocked_uploads"] == [{"key": "first", "outcome": "requires_remote_verification"}]
    assert first.data["result_id"] not in json.dumps(result.data)


def test_recovery_rejects_another_owner_or_session(current_project_factory, tmp_path, monkeypatch):
    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    context.owner_id = "first-owner"
    context.request_context = replace(project.context.runtime_context, session_id="first-session")
    result = upload_entries({}, context, project.context.collection, Remote(), 7, None)
    stranger = ExecutionContext(
        app_context=project.context,
        owner_id="second-owner",
        request_context=replace(project.context.runtime_context, session_id="second-session"),
    )
    with pytest.raises(ValueError, match="所有者"):
        recover_upload_associations(stranger, result_id=result.data["result_id"])


def test_tool_freezes_one_config_for_client_and_receipt(current_project_factory, tmp_path, monkeypatch):
    from transbridge.smart_assistant.tools.tool_paratranz import _tool_upload_entries

    context, project = _context(current_project_factory, tmp_path, monkeypatch)
    original = SimpleNamespace(token="private-test", base_url="https://first.example/api", user_id=7, timeout=30)
    project.context.config = original
    configs = []

    def change_config(_entry):
        original.base_url = "https://second.example/api"
        original.user_id = 8

    remote = Remote(change_config)
    remote.close = lambda: None
    monkeypatch.setattr(
        "transbridge.paratranz.service.ParaTranzService.from_config",
        lambda config: configs.append(config) or remote,
    )
    result = _tool_upload_entries({"project_id": 7}, context)
    assert len(remote.sent) == 3
    assert configs[0] is not original and configs[0].base_url == "https://first.example/api"
    assert result.data["upload_result"]["target"]["endpoint"] == "https://first.example/api"
    assert result.data["upload_result"]["target"]["account_user_id"] == 7
    assert len(result.data["deferred_associations"]) == 3
    assert "private-test" not in json.dumps(result.data["upload_result"])

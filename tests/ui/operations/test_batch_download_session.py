"""Real project state and file reads through the multi-source GUI composition."""

from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_production_facade import _runtime
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.file_upload import upload_files
from transbridge.application.io.paratranz_mapping import paratranz_record_from_entry
from transbridge.application.projects import EntryStatePatch
from transbridge.application.tasks import JobState, OwnerRef, TaskCancelled
from transbridge.paratranz.batch_download_snapshot import (
    BatchDownloadRead,
    BatchDownloadScope,
    BatchDownloadSnapshotReader,
)
from transbridge.ui.operations.batch_download_session import BatchDownloadSession


class Context:
    uses_authoritative_projection = True
    active_variant = "默认"
    project_name = "批量测试"

    def __init__(self, services, request):
        self.services = services
        self.project_commands = services.gui_project_commands
        self.runtime_context = request
        self.config = SimpleNamespace(token="configured", config_revision=1)
        self.target = SimpleNamespace(
            endpoint="https://paratranz.cn/api",
            project_id=42,
            account_user_id=7,
            status=SimpleNamespace(value="available"),
            project_name="远端",
            reason="",
        )
        self.slots = {}

    @property
    def active_project_id(self):
        return self.services.project_lifecycle.active.project_ref.identity.value

    @property
    def active_variant_id(self):
        return self.services.project_lifecycle.active.formal_variant_ref.identity.value

    @property
    def project_sources(self):
        return self.services.project_lifecycle.active.project.envelope.data["sources"]

    @property
    def project_relations(self):
        return self.services.project_lifecycle.active.project.envelope.data.get("source_relations", ())

    def resolve_paratranz_target(self):
        return self.target


class Gateway:
    def __init__(self):
        self.files, self.rows, self.calls = {}, {}, []
        self.fail = set()

    def inventory(self, project_id):
        self.calls.append(("inventory", project_id))
        return dict(self.files)

    def get_file_translation(self, project_id, file_id):
        self.calls.append(("file", file_id))
        if file_id in self.fail:
            raise RuntimeError("测试文件读取失败")
        return self.rows[file_id]

    def close(self):
        pass


@pytest.fixture
def case(project):  # noqa: F811 - imported pytest fixture
    services, source_service, request, *_ = project
    context = Context(services, request)
    snapshot = source_service.snapshot(request, endpoint=context.target.endpoint, remote_project_id=42).value
    registrations = {item["source_id"]: item for item in context.project_sources}
    # Exercise the slot fallback used by canonical source registrations.
    context.slots = {
        source.source_id: SimpleNamespace(
            collection=source.entries, label=source.label, esp_path=str(registrations[source.source_id]["location"])
        )
        for source in snapshot.sources
    }
    gateway, membership = Gateway(), []
    paths = {file.source_id: file.path for file in upload_files(snapshot.sources, categorized=False)}
    for index, source in enumerate(snapshot.sources, 1):
        path = paths[source.source_id]
        gateway.files[path] = index
        gateway.rows[index] = [
            dict(
                paratranz_record_from_entry(entry),
                id=index * 100 + offset,
                fileId=index,
                translation=f"云端{index}",
                stage=3,
            )
            for offset, entry in enumerate(source.entries)
        ]

    def service_factory(_config):
        def projects(*, uid, cancellation=None):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            membership.append(uid)
            return [SimpleNamespace(project_id=42)]

        return SimpleNamespace(list_projects=projects, close=lambda: None)

    runtime = _runtime()
    session = BatchDownloadSession(
        context,
        runtime,
        reader_factory=lambda config: BatchDownloadSnapshotReader(
            config,
            gateway_factory=lambda _config: gateway,
        ),
        service_factory=service_factory,
    )
    return SimpleNamespace(
        services=services,
        context=context,
        session=session,
        runtime=runtime,
        sources=snapshot.sources,
        gateway=gateway,
        membership=membership,
        cancel=Event(),
    )


def compare(case, sources=None, policy="keep_current", decisions=()):
    return case.session.compare(
        tuple(source.source_id for source in case.sources) if sources is None else sources,
        policy,
        decisions,
        lambda *_: None,
        case.cancel,
    )


def states(case):
    return {
        entry.entry_key: (entry.translation, entry.stage.value)
        for entry in case.services.project_lifecycle.active.variant.snapshot().entries
    }


def test_source_catalog_opens_without_hydration_and_preselects_only_current_plugin(case, monkeypatch):
    current, other = case.sources
    case.context.collection = current.entries
    monkeypatch.setattr(
        case.context.project_commands,
        "file_sources_snapshot",
        lambda *_args, **_kwargs: pytest.fail("Listing plugins must not hydrate source files"),
    )

    options = case.session.load_sources(lambda *_: None, case.cancel)

    assert {item.source_id for item in options if item.selected} == {current.source_id}
    assert {item.source_id: item.count for item in options} == {
        current.source_id: len(current.entries),
        other.source_id: len(other.entries),
    }
    assert case.gateway.calls == [] and case.membership == []


def test_source_catalog_without_current_collection_does_not_silently_select_every_plugin(case):
    case.context.collection = None
    options = case.session.load_sources(lambda *_: None, case.cancel)
    assert len(options) == 2
    assert not any(item.selected for item in options)


def test_source_catalog_retries_an_ordinary_concurrent_edit_internally(case, monkeypatch):
    original = case.context.project_commands.file_sources_catalog
    calls = []

    def changed_once(context):
        calls.append(context)
        if len(calls) == 1:
            return OperationResult.failed(
                DomainError(ErrorCategory.CONFLICT, "CATEGORIZED_UPLOAD_STALE", "工程内容刚刚变化")
            )
        return original(context)

    monkeypatch.setattr(case.context.project_commands, "file_sources_catalog", changed_once)
    options = case.session.load_sources(lambda *_: None, case.cancel)
    assert len(options) == 2 and len(calls) == 2
    assert case.gateway.calls == []


def test_batch_file_reads_and_one_real_commit_with_unsaved_state(case, monkeypatch):
    key = case.sources[0].entries[0].identity
    assert case.context.project_commands.update_entry(
        key, case.context.runtime_context, translation="未保存", stage=1
    ).is_success
    before = case.services.project_lifecycle.active.variant.revision
    captures = []
    original = case.context.project_commands.save_snapshot
    monkeypatch.setattr(
        case.context.project_commands, "save_snapshot", lambda *args: (captures.append(args[0]), original(*args))[1]
    )
    preview = compare(case)
    assert preview.updates == 2
    assert compare(case).updates == 2
    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert receipt.updated == 2 and not receipt.requires_review
    assert len(captures) == 0
    assert case.session.record["merges"]
    assert case.services.project_lifecycle.active.variant.revision == before + 1
    assert {translation for translation, _stage in states(case).values()} == {"云端1", "云端2"}
    assert case.gateway.calls == [("inventory", 42), ("file", 1), ("file", 2)]
    assert case.membership == [7]


def test_unselected_local_edit_is_preserved_without_another_cloud_read(case):
    selected, other = case.sources
    compare(case, (selected.source_id,))
    key = other.entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="新编辑", stage=1)

    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert receipt.updated == 1 and not receipt.requires_review
    assert states(case)[key] == ("新编辑", 1)
    assert case.gateway.calls == [("inventory", 42), ("file", 1)]


def test_same_entry_new_edit_is_preserved_while_independent_results_are_published(case):
    compare(case)
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="刚改的", stage=1)

    first = case.session.apply(lambda *_: None, case.cancel)

    assert first.requires_review and first.updated == 1 and first.preview.deferred == 0
    assert states(case)[key] == ("刚改的", 1)
    assert "刚改的" in {translation for translation, _stage in states(case).values()}
    assert case.session.apply(lambda *_: None, case.cancel).updated == 0
    assert states(case)[key] == ("刚改的", 1)
    assert len(case.gateway.calls) == 3


def test_partial_file_read_can_retry_here_without_redownloading_successful_source(case):
    case.gateway.fail.add(2)
    first = compare(case)
    assert first.updates == 1 and any("读取失败" in detail for detail in first.details)
    case.gateway.fail.clear()

    second = compare(case)

    assert second.updates == 2
    assert case.gateway.calls.count(("file", 1)) == 1
    assert case.gateway.calls.count(("file", 2)) == 2


def test_missing_file_is_relocated_on_retry_without_reopening_window(case):
    paths = dict(case.gateway.files)
    case.gateway.files = {path: file_id for path, file_id in paths.items() if file_id == 1}
    assert compare(case).updates == 1
    case.gateway.files = paths

    assert compare(case).updates == 2
    assert case.gateway.calls.count(("file", 1)) == 1


def test_normal_verification_status_upgrade_is_not_a_false_identity_change(case):
    case.context.target.status.value = "unverified"
    session = case.session
    case.context.target.status.value = "available"
    assert compare(case).updates == 2
    assert session.apply(lambda *_: None, case.cancel).updated == 2


@pytest.mark.parametrize("phase", ["evidence", "commit"])
def test_failed_publication_preserves_every_source_and_can_retry(case, monkeypatch, phase):
    compare(case)
    before = states(case)
    with monkeypatch.context() as patch:
        if phase == "evidence":

            def fail(*args, **kwargs):
                raise OSError("测试磁盘失败")

            patch.setattr(case.session.result_store, "save_merge", fail)
        else:
            patch.setattr(
                case.context.project_commands,
                "merge_entry_states",
                lambda *args, **kwargs: OperationResult.failed(
                    DomainError(ErrorCategory.INTERNAL, "DISK_FAILURE", "测试磁盘失败"),
                ),
            )
        with pytest.raises(RuntimeError):
            case.session.apply(lambda *_: None, case.cancel)
    assert states(case) == before
    assert case.session.apply(lambda *_: None, case.cancel).updated == 2


def test_cancel_before_commit_cannot_update_any_plugin(case, monkeypatch):
    compare(case)
    before = states(case)
    original = case.context.project_commands.merge_entry_states

    def cancel_before_commit(*args, **kwargs):
        case.cancel.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(case.context.project_commands, "merge_entry_states", cancel_before_commit)
    with pytest.raises((RuntimeError, TaskCancelled)):
        case.session.apply(lambda *_: None, case.cancel)
    assert states(case) == before


def test_postcommit_target_change_does_not_turn_success_into_failure(case):
    compare(case)

    def progress(current, total, _message):
        if current == total == 1:
            case.context.target.account_user_id = 99

    assert case.session.apply(progress, case.cancel).updated == 2
    assert {translation for translation, _stage in states(case).values()} == {"云端1", "云端2"}


def test_unrelated_references_survive_current_project_reference_cleanup(case):
    from transbridge.application.io.identity import ExternalEntryRef

    key = case.sources[1].entries[0].identity
    unrelated = ExternalEntryRef("other", "project:9", "retained")
    cloud = ExternalEntryRef("paratranz", "project:42", "historical")
    assert case.context.project_commands.replace_entry_records(
        {key: EntryStatePatch("", 0, (unrelated, cloud))},
        case.context.runtime_context,
    ).is_success
    compare(case)
    assert case.session.apply(lambda *_: None, case.cancel).updated == 2
    entry = next(
        entry for entry in case.services.project_lifecycle.active.variant.snapshot().entries if entry.entry_key == key
    )
    assert entry.external_refs == (unrelated,)


def test_late_cancel_after_commit_keeps_completed_task_and_success_receipt(case):
    compare(case)

    def progress(current, total, _message):
        if current == total == 1:
            case.cancel.set()

    receipt = case.session.apply(progress, case.cancel)
    actor = OwnerRef(
        case.context.runtime_context.owner_id,
        "gui.operation-plan",
        case.context.active_project_id,
        case.context.active_variant_id,
    )
    assert receipt.updated == 2
    assert case.runtime.tasks.list(actor)[-1].state is JobState.COMPLETED


def test_source_rename_does_not_refetch_frozen_remote_result(case):
    selected = case.sources[0]
    compare(case, (selected.source_id,))
    active = case.services.project_lifecycle.active
    data = dict(active.project.envelope.data)
    sources = [dict(source) for source in data["sources"]]
    for source in sources:
        if source["source_id"] == selected.source_id:
            source["display_name"] = "新插件名.esp"
    data["sources"] = sources
    updated = replace(
        active.project,
        envelope=replace(active.project.envelope, data=data, revision=active.project.envelope.revision + 1),
    )
    assert case.services.project_lifecycle.commit_project_update(
        updated,
        active.project.envelope.revision,
        case.context.runtime_context,
    ).is_success
    case.gateway.files["新插件名.esp.json"] = 3
    case.gateway.rows[3] = [dict(row, id=300, fileId=3, translation="新路径译文") for row in case.gateway.rows[1]]

    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert not receipt.requires_review and receipt.updated == 1
    assert states(case)[selected.entries[0].identity][0] == "云端1"
    assert ("file", 3) not in case.gateway.calls


def test_cross_cached_source_collision_is_recoverable_in_same_window(case):
    first, second = case.sources
    compare(case, (first.source_id,))
    original = case.gateway.rows[2]
    case.gateway.rows[2] = [dict(row, id=100) for row in original]

    blocked = compare(case)

    assert blocked.updates == 0
    assert any("重叠" in detail for detail in blocked.details)
    case.gateway.rows[2] = original
    assert compare(case).updates == 2


def test_empty_source_is_explained_and_does_not_block_other_sources(case, monkeypatch):
    original = case.context.project_commands.file_sources_snapshot

    def empty_first(*args, **kwargs):
        result = original(*args, **kwargs)
        snapshot = result.value
        return replace(
            result, value=replace(snapshot, sources=(replace(snapshot.sources[0], entries=()), *snapshot.sources[1:]))
        )

    monkeypatch.setattr(case.context.project_commands, "file_sources_snapshot", empty_first)
    preview = compare(case)
    assert preview.updates == 1
    assert any("没有可更新词条" in detail for detail in preview.details)
    assert not any("index out of range" in detail for detail in preview.details)
    before = states(case)

    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert receipt.updated == 1
    current = states(case)
    assert current[case.sources[0].entries[0].identity] == before[case.sources[0].entries[0].identity]
    assert current[case.sources[1].entries[0].identity] == ("云端2", 3)
    assert not case.services.project_lifecycle.active.dirty


def test_target_change_inside_runtime_guard_cannot_mutate_any_source(case, monkeypatch):
    compare(case)
    before = states(case)
    original = case.runtime.tasks.try_commit

    def switch_account(permit, mutation):
        case.context.target.account_user_id = 99
        return original(permit, mutation)

    monkeypatch.setattr(case.runtime.tasks, "try_commit", switch_account)
    with pytest.raises(RuntimeError):
        case.session.apply(lambda *_: None, case.cancel)
    assert states(case) == before


def test_preview_hashes_only_actual_merge_candidates(case, monkeypatch):
    from transbridge.ui.operations import batch_download_results as results

    case.gateway.rows[1] = [{**row, "translation": ""} for row in case.gateway.rows[1]]
    signature = results.source_locator_signature
    calls = []

    def count_signature(*args):
        calls.append(args)
        return signature(*args)

    monkeypatch.setattr(results, "source_locator_signature", count_signature)
    preview = compare(case)
    assert preview.updates == 1
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["metadata", "string_id", "fingerprint"])
def test_preview_invalidates_review_when_source_locator_changes(case, field):
    from transbridge.application.translation.result_merge import ResultConflictPolicy, merge_result_entries
    from transbridge.ui.operations.batch_download_results import preview_entry_states, result_changes

    compare(case, policy="defer")
    source = case.sources[0]
    key = source.entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="手改的", stage=1)
    receipt = case.session.apply(lambda *_: None, case.cancel)
    row = receipt.preview.review_rows[0]
    reviewed = case.session.review_states[row.row_id]
    snapshot = case.session._capture()
    registrations = [dict(item) for item in case.context.project_sources]
    if field == "fingerprint":
        next(item for item in registrations if item["source_id"] == source.source_id)["fingerprint"] = "0" * 64
    else:
        snapshot = replace(
            snapshot,
            sources=tuple(
                replace(
                    item,
                    entries=tuple(
                        replace(
                            entry,
                            **{
                                field: tuple({**dict(entry.metadata), "location": "changed"}.items())
                                if field == "metadata"
                                else "new"
                            },
                        )
                        if entry.identity == key
                        else entry
                        for entry in item.entries
                    ),
                )
                for item in snapshot.sources
            ),
        )
    candidates = result_changes(case.session.plan)
    plan = merge_result_entries(
        {change.base.entry_key: change.base for change in candidates},
        preview_entry_states(snapshot, registrations, {change.base.entry_key for change in candidates}),
        {change.incoming.entry_key: change.incoming for change in candidates},
        policy=ResultConflictPolicy.DEFER,
        policies={key: ResultConflictPolicy.PREFER_INCOMING},
        decision_states={key: reviewed},
    )
    assert any(conflict.entry_key == key and conflict.reason == "decision_stale" for conflict in plan.pending)
    assert states(case)[key] == ("手改的", 1)
    assert case.session.result_store.sources(
        case.session.record,
        scope_factory=BatchDownloadScope,
        read_factory=BatchDownloadRead,
    )


def test_concurrent_new_edit_can_be_explicitly_kept_in_existing_review(case):
    compare(case)
    key = case.sources[0].entries[0].identity
    case.context.project_commands.update_entry(key, case.context.runtime_context, translation="手改的", stage=1)
    receipt = case.session.apply(lambda *_: None, case.cancel)
    assert receipt.requires_review
    row = receipt.preview.review_rows[0]
    assert "修改" in row.reason

    preview = compare(case, decisions=((row.row_id, "keep_local"),))

    assert preview.updates == 0 and preview.deferred == 0
    assert preview.review_rows[0].decision == "keep_local"
    assert case.session.review_states[row.row_id].source_locator is not None
    assert case.session.apply(lambda *_: None, case.cancel).updated == 0
    assert states(case)[key] == ("手改的", 1)


def test_revision_race_rebases_unrelated_edit_and_finishes_without_user_restart(case, monkeypatch):
    selected, other = case.sources
    compare(case, (selected.source_id,))
    original = case.context.project_commands.replace_entry_records
    once = []
    key = other.entries[0].identity

    def race(*args, **kwargs):
        if not once:
            once.append(True)
            case.context.project_commands.update_entry(
                key, case.context.runtime_context, translation="并发编辑", stage=1
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(case.context.project_commands, "replace_entry_records", race)

    assert case.session.apply(lambda *_: None, case.cancel).updated == 1
    assert states(case)[key] == ("并发编辑", 1)
    assert case.gateway.calls.count(("file", 1)) == 1

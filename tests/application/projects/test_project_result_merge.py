from tests.application.projects.test_categorized_upload import project as project_fixture
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.translation.result_merge import ResultConflictPolicy, ResultEntryChange, ResultEntryState
from transbridge.persistence.v2.ids import VariantId, VariantRef
from transbridge.persistence.v2.variant import VariantSnapshot

project = project_fixture


def test_commit_failure_retains_original_diagnostic_and_failure_counts(project, monkeypatch):
    services, _sources, context, *_ = project
    command = services.gui_project_commands
    failed = OperationResult.failed(
        DomainError(ErrorCategory.INTERNAL, "DURABLE_VARIANT_SAVE_FAILED", "磁盘空间不足", retryable=True),
        run_id=context.run_id,
    )
    monkeypatch.setattr(command, "replace_entry_states", lambda *_args, **_kwargs: failed)
    result = command.merge_entry_states(
        changes(project), context, expected_variant_ref=services.project_lifecycle.active.formal_variant_ref
    )
    assert result is failed
    assert result.counts.failed == 1
    assert result.diagnostics[0].code == "DURABLE_VARIANT_SAVE_FAILED"
    assert result.diagnostics[0].message == "磁盘空间不足"
    assert result.diagnostics[0].retryable


def test_save_task_target_accepts_newer_edits_but_never_another_identity(project):
    services, _sources, context, *_ = project
    command = services.gui_project_commands
    candidates = changes(project)
    active = services.project_lifecycle.active
    identity = (active.project_ref.identity.value, active.formal_variant_ref.identity.value)
    assert command.update_entry(candidates[0].base.entry_key, context, translation="later", stage=3).is_success
    assert command.save(context, expected_active_identity=identity).is_success
    assert not services.project_lifecycle.active.dirty
    rejected = command.save(context, expected_active_identity=(identity[0], "another-variant"))
    assert not rejected.is_success


def test_merge_guard_must_publish_and_duplicate_delivery_does_not_repeat_commit(project):
    services, _sources, context, *_ = project
    command = services.gui_project_commands
    candidates = changes(project)
    before = services.project_lifecycle.active.variant.snapshot()
    evidence = []
    denied = command.merge_entry_states(
        candidates,
        context,
        expected_variant_ref=before.ref,
        commit_mutation=lambda _publish: None,
        before_commit=evidence.append,
    )
    assert not denied.is_success
    assert services.project_lifecycle.active.variant.snapshot() is before
    assert not evidence

    def twice(publish):
        publish()
        publish()

    applied = command.merge_entry_states(
        candidates,
        context,
        expected_variant_ref=before.ref,
        commit_mutation=twice,
        before_commit=evidence.append,
    )
    assert applied.is_success and len(evidence) == 1
    assert services.project_lifecycle.active.variant.revision == before.revision + 1


def changes(project):
    services, sources, context, *_ = project
    result = sources.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=42)
    assert result.is_success
    return tuple(
        ResultEntryChange(
            ResultEntryState(entry.identity, entry.original, entry.translation, entry.stage, entry.context or ""),
            ResultEntryState(entry.identity, entry.original, "result", 3, entry.context or ""),
        )
        for source in result.value.sources
        for entry in source.entries
    )


def test_merge_latest_content_preserves_concurrent_edits_and_saves_independent_targets(project):
    services, _sources, context, *_ = project
    candidates = changes(project)
    ref = services.project_lifecycle.active.formal_variant_ref
    assert services.gui_project_commands.update_entry(
        candidates[0].base.entry_key, context, translation="manual", stage=5
    ).is_success
    evidence = []
    result = services.gui_project_commands.merge_entry_states(
        candidates,
        context,
        expected_variant_ref=ref,
        persist=True,
        before_commit=evidence.append,
    )
    assert result.is_success, result.diagnostics
    assert len(result.value.applied) == 1 and len(result.value.kept_current_keys) == 1
    assert evidence == [result.value]
    active = services.project_lifecycle.active
    persisted = VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref)
    assert persisted == active.variant.snapshot() and not active.dirty
    assert {entry.translation for entry in persisted.entries} == {"manual", "result"}


def test_merge_refuses_old_variant_and_evidence_failure_leaves_memory_and_disk_intact(project):
    services, _sources, context, *_ = project
    candidates = changes(project)
    before = services.project_lifecycle.active.variant.snapshot()
    ref = before.ref
    wrong = services.gui_project_commands.merge_entry_states(
        candidates,
        context,
        expected_variant_ref=VariantRef(VariantId("other"), ref.project_id),
    )
    assert not wrong.is_success

    def fail_evidence(_plan):
        raise PermissionError("evidence directory locked")

    failed = services.gui_project_commands.merge_entry_states(
        candidates,
        context,
        expected_variant_ref=ref,
        persist=True,
        before_commit=fail_evidence,
    )
    assert not failed.is_success
    assert services.project_lifecycle.active.variant.snapshot() is before
    assert VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref) == before


def test_merge_retries_current_cas_without_rehashing_installed_sources(project, monkeypatch):
    services, _sources, context, *_ = project
    candidates = changes(project)
    command = services.gui_project_commands
    commit = command.replace_entry_states
    captured = []
    signatures = []
    from transbridge.application.projects import source_hydration_cache

    original_signature = source_hydration_cache.source_input_signature

    def counted_signature(*args, **kwargs):
        signatures.append(args[0].location)
        return original_signature(*args, **kwargs)

    monkeypatch.setattr(source_hydration_cache, "source_input_signature", counted_signature)

    def race(states, *args, **options):
        captured.append(states)
        if len(captured) == 1:
            assert command.update_entry(candidates[0].base.entry_key, context, translation="raced", stage=5).is_success
        return commit(states, *args, **options)

    monkeypatch.setattr(command, "replace_entry_states", race)
    result = command.merge_entry_states(
        candidates,
        context,
        expected_variant_ref=services.project_lifecycle.active.formal_variant_ref,
        policy=ResultConflictPolicy.KEEP_CURRENT,
    )
    assert result.is_success and len(captured) == 2
    assert len(result.value.applied) == 1 and result.value.kept_current_keys == (candidates[0].base.entry_key,)
    # Both the first access and the CAS retry use the installed source artifact;
    # neither hashes or reparses live input while rebasing current translations.
    assert signatures == []
    signatures.clear()
    repeated = command.merge_entry_states(
        candidates,
        context,
        expected_variant_ref=services.project_lifecycle.active.formal_variant_ref,
    )
    assert repeated.is_success and signatures == []


def test_noop_conflict_still_records_evidence_under_guard_without_revision_change(project):
    services, _sources, context, *_ = project
    candidate = changes(project)[0]
    command = services.gui_project_commands
    assert command.update_entry(candidate.base.entry_key, context, translation="manual", stage=5).is_success
    before = services.project_lifecycle.active.variant.snapshot()
    calls = []

    def guard(mutation):
        calls.append("guard")
        mutation()

    result = command.merge_entry_states(
        (candidate,),
        context,
        expected_variant_ref=before.ref,
        before_commit=lambda plan: calls.append(plan),
        commit_mutation=guard,
    )
    assert result.is_success
    assert calls == ["guard", result.value]
    assert services.project_lifecycle.active.variant.snapshot() is before


def test_task_result_directory_is_owned_by_current_project(project):
    from pathlib import Path

    services, _sources, context, *_ = project
    assert (
        services.gui_project_commands.task_result_directory(context)
        == Path(services.projects.path_for(services.project_lifecycle.active.project_ref)).parent
    )


def test_actual_applied_download_clears_only_requested_remote_association(project):
    from transbridge.application.io.identity import ExternalEntryRef
    from transbridge.application.projects import EntryStatePatch

    services, _sources, context, *_ = project
    candidates = changes(project)
    command = services.gui_project_commands
    old = ExternalEntryRef("paratranz", "project:42", 1)
    other = ExternalEntryRef("other", "project:42", 2)
    assert command.replace_entry_records(
        {
            candidate.base.entry_key: EntryStatePatch(candidate.base.translation, candidate.base.stage, (old, other))
            for candidate in candidates
        },
        context,
    ).is_success
    assert command.update_entry(candidates[0].base.entry_key, context, translation="manual", stage=5).is_success
    result = command.merge_entry_states(
        candidates,
        context,
        expected_variant_ref=services.project_lifecycle.active.formal_variant_ref,
        clear_external_scope=("paratranz", "project:42"),
    )
    assert result.is_success
    records = {entry.entry_key: entry for entry in services.project_lifecycle.active.variant.snapshot().entries}
    assert records[candidates[0].base.entry_key].external_refs == (old, other)
    assert records[candidates[1].base.entry_key].external_refs == (other,)

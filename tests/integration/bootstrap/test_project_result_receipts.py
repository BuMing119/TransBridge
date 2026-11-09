from contextlib import closing
from dataclasses import replace
from uuid import uuid4

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.tasks.result_application import ResultApplication
from transbridge.application.tasks.storage_contracts import ApplicationState
from transbridge.application.translation.result_merge import ResultConflictPolicy, ResultEntryChange, ResultEntryState
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store import reads
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.task_store import TaskStore
from transbridge.persistence.v2.deferred_variant_rows import DeferredVariantRows


def _services(root):
    return build_persistence_v2_services(
        root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-11-09T00:00:00+08:00"
    )


def _stage(commands, recovery, context, variant_ref, run_id, row, translation):
    recovery.create({
        "task_id": run_id,
        "project_id": variant_ref.project_id.value,
        "variant_id": variant_ref.identity.value,
        "mode": "download",
        "config_digest": "synthetic",
        "state": "completed",
        "created_at": "2026-11-09T00:00:00+08:00",
        "sources": [],
        "result_kind": "download",
        "results_only": True,
    })
    base = ResultEntryState(row.entry_key, row.original, row.translation, row.stage, row.context)
    candidate = replace(base, translation=translation, stage=1)
    row_data = {
        "entry_key": row.entry_key.to_dict(),
        "source": "Greeting source",
        "action": "translation",
        "before": {
            "original": row.original,
            "translation": row.translation,
            "stage": row.stage,
            "context": row.context,
            "revision": row.revision.value,
        },
        "status": "succeeded",
        "text": candidate.translation,
        "stage": candidate.stage,
        "decision": "pending",
        "applied": False,
        "attempt": 1,
    }
    recovery.save_results(run_id, [row_data])
    result_application = ResultApplication.for_commands(recovery, commands)
    saved = recovery.load_results(run_id)["rows"]
    policy = ResultConflictPolicy.PREFER_INCOMING
    request = result_application.prepare(run_id, variant_ref.project_id, variant_ref.identity, saved, policy)
    operation_id = request.request.operation_id
    merged = commands.merge_entry_states(
        (ResultEntryChange(base, candidate),),
        context,
        expected_variant_ref=variant_ref,
        policy=policy,
        operation_id=operation_id,
    )
    assert merged.is_success, merged.diagnostics
    assert [(item.entry_key, item.translation, item.stage) for item in merged.value.applied] == [
        (candidate.entry_key, candidate.translation, candidate.stage)
    ]
    assert result_application.finish(request, merged.value).state is ApplicationState.STAGED_UNSAVED
    assert commands.result_receipt(variant_ref.project_id.value, operation_id) is None
    return result_application, operation_id


def test_normal_project_save_records_exact_task_application_and_reconciles_without_replaying(tmp_path, monkeypatch):
    root = tmp_path / "data"
    plugin = write_plugin(tmp_path / "Source.esp", [(0x800, "Greeter", "Welcome")])
    context = RequestContext("gui", run_id="result-application")
    task_root = root / "task-store-v1"
    with closing(_services(root)) as services:
        commands = services.gui_project_commands
        created = commands.create_project(
            ProjectProvisioningRequest("Result application", source=ProjectSourceRequest(str(plugin))), context
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        variant_ref = active.formal_variant_ref
        store = ProjectStore(project_path(root, active.project_ref.identity))
        recovery = TaskStore(task_root).recovery()
        (original,) = store.read_page(variant_ref.identity)
        review, overwritten_id = _stage(
            commands, recovery, context, variant_ref, "overwritten-run", original.snapshot, "Overwritten candidate"
        )
        assert store.read_page(variant_ref.identity) == (original,)
        manual = commands.update_entry(original.state.entry_key, context, translation="Manual override", stage=3)
        assert manual.is_success, manual.diagnostics
        saved = commands.save(context)
        assert saved.is_success, saved.diagnostics
        discarded = review.reconcile(overwritten_id)
        assert discarded.state is ApplicationState.DISCARDED
        assert discarded.receipt["entries"] == []
        assert discarded.receipt["dispositions"] == [
            {"entry_key": original.state.entry_key.to_dict(), "state": "discarded-or-superseded"}
        ]
        assert recovery.load_results("overwritten-run")["rows"][0]["text"] == "Overwritten candidate"

        (updated,) = store.read_page(variant_ref.identity)
        assert updated.state.translation == "Manual override"
        review, saved_id = _stage(
            commands, recovery, context, variant_ref, "accepted-run", updated.snapshot, "Accepted candidate"
        )
        assert store.read_page(variant_ref.identity) == (updated,)

        def fail_task_receipt(*_):
            raise OSError("Injected task database acknowledgement failure")

        with monkeypatch.context() as patch:
            patch.setattr(recovery.decisions, "acknowledge", fail_task_receipt)
            saved = commands.save(context)
            assert saved.is_success, saved.diagnostics
            persisted = review.reconcile(saved_id)
            assert persisted.state is ApplicationState.ACKNOWLEDGEMENT_PENDING
        assert store.read_page(variant_ref.identity)[0].state.translation == "Accepted candidate"
        committed = store.path.read_bytes()

    plugin.unlink()
    with closing(_services(root)) as restarted:
        assert restarted.project_lifecycle.active is None
        recovered = TaskStore(task_root).recovery()
        review = ResultApplication.for_commands(recovered, restarted.gui_project_commands)
        confirmed = review.reconcile(saved_id)
        assert confirmed.state is ApplicationState.COMMITTED
        assert confirmed.receipt["entries"] == [
            {"entry_key": original.state.entry_key.to_dict(), "translation": "Accepted candidate", "stage": 1}
        ]
        assert review.reconcile(saved_id) == confirmed
        recovered.delete("accepted-run", discard_unmerged=True)

    assert store.path.read_bytes() == committed
    assert ProjectStore(store.path).read_page(variant_ref.identity)[0].state.translation == "Accepted candidate"
    assert store.receipt(saved_id) is not None


def test_task_merge_on_paged_project_keeps_unselected_ranges_deferred(tmp_path, monkeypatch):
    root = tmp_path / "data"
    plugin = write_plugin(
        tmp_path / "Source.esp",
        [(0x800 + index, f"Item{index:03}", f"Original {index:03}") for index in range(350)],
    )
    other_plugin = write_plugin(tmp_path / "Unloaded.esp", [(0x800, "Other", "Untouched source")])
    context = RequestContext("gui", run_id="paged-result-application")
    with closing(_services(root)) as setup:
        created = setup.gui_project_commands.create_project(
            ProjectProvisioningRequest(
                "Paged result application",
                source=ProjectSourceRequest(str(plugin)),
                additional_sources=(ProjectSourceRequest(str(other_plugin)),),
            ),
            context,
        )
        assert created.is_success, created.diagnostics
        active = setup.project_lifecycle.active
        ref = active.formal_variant_ref
        store = ProjectStore(project_path(root, active.project_ref.identity))
        before = tuple(store.iter_entries(ref.identity))
        target = before[145]
    plugin.unlink()
    other_plugin.unlink()

    with closing(_services(root)) as services:
        prepared = services.current_project_opener.prepare_active(context, paged=True)
        assert prepared.is_success, prepared.diagnostics
        activated = services.current_project_opener.activate(prepared.value, context)
        assert activated.is_success, activated.diagnostics
        assert isinstance(services.project_lifecycle.active.variant.snapshot().entries, DeferredVariantRows)
        commands = services.gui_project_commands
        recovery = TaskStore(root / "task-store-v1").recovery()
        decoded = []
        decode_entry = reads.decode_entry

        def observe(row):
            entry = decode_entry(row)
            decoded.append(entry.state.entry_key)
            return entry

        def forbidden_range(*_args, **_kwargs):
            pytest.fail("Merging one candidate opened a full project range")

        with monkeypatch.context() as patch:
            patch.setattr(reads, "decode_entry", observe)
            patch.setattr(ProjectStore, "iter_entries", forbidden_range)
            review, operation_id = _stage(
                commands, recovery, context, ref, "paged-run", target.snapshot, "Selected translation"
            )
            working = services.project_lifecycle.active.variant.snapshot().entries
            assert isinstance(working, DeferredVariantRows)
            assert len(working.pending()) == 1 and working.pending()[0].entry_key == target.state.entry_key
            assert store.read_entries(ref.identity, (target.state.entry_key,)) == (target,)
            saved = commands.save(context)
            assert saved.is_success, saved.diagnostics
            receipt = review.reconcile(operation_id)
            assert receipt.state is ApplicationState.COMMITTED
            assert [row["entry_key"] for row in receipt.receipt["entries"]] == [target.state.entry_key.to_dict()]
        assert set(decoded) == {target.state.entry_key}
        assert isinstance(services.project_lifecycle.active.variant.snapshot().entries, DeferredVariantRows)

    after = tuple(ProjectStore(store.path).iter_entries(ref.identity))
    assert after[:145] == before[:145] and after[146:] == before[146:]
    assert after[145].state.translation == "Selected translation"

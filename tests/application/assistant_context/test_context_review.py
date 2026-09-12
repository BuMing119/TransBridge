"""Independent regression probes for context recovery and admission boundaries."""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event
from types import SimpleNamespace

import pytest

from tests.application.assistant_context.test_history_queries import _request
from tests.application.assistant_requests.test_request_repository import _proposal
from transbridge.application.assistant_context.admission import clear_wait, save_wait
from transbridge.application.assistant_context.models import CompactionSummary, PreparationWait
from transbridge.application.assistant_context.projection import append_context, project_result
from transbridge.application.assistant_requests.models import RequestItem, UserRequest
from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.persistence.assistant_attachment_cleanup import AssistantAttachmentCleanup
from transbridge.persistence.assistant_context_store import AssistantContextStore
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore
from transbridge.persistence.v2.models import BackupVerificationError
from transbridge.smart_assistant.context_budget import ContextBudget
from transbridge.smart_assistant.context_runtime import ContextRuntime
from transbridge.smart_assistant.request_model_input import RequestModelInput
from transbridge.ui.tools.smart_assistant.request_context_preparation import RequestContextPreparation

pytest_plugins = ["tests.application.assistant_requests.test_request_repository"]


def _admit(service, context, request_id):
    service.scheduler.activate(context.session_id, "review")
    request = next(r for r in service.requests(service.state(context)) if r.request_id == request_id)
    admission = service.scheduler.select_next_turn(context.session_id, "review", (request,)).admission
    return request, admission


def test_real_loaded_configuration_can_reach_background_context_preparation(composed, tmp_path):
    _, service, context = composed
    request_id = _request(service, context)
    request, admission = _admit(service, context, request_id)
    repository = ConfigRepository(tmp_path / "config.ini", credential_store=UnavailableCredentialStore())
    config = LLMConfig.load_from_file(repository=repository, environment={})
    jobs, callbacks, errors = [], [], []

    def submit(work):
        future = Future()
        jobs.append((work, future))
        return future

    binding = SimpleNamespace(
        service=service,
        context=context,
        admission=admission,
        _closed=False,
        _active=True,
        _queue=SimpleNamespace(submit=submit),
        delivered=SimpleNamespace(emit=callbacks.append),
        prepare_model_input=lambda *args, **kwargs: RequestModelInput((), (), ContextBudget(), request=request),
        facade=SimpleNamespace(
            _orchestrator=SimpleNamespace(get_llm_client=lambda: object(), _cached_llm_config=config)
        ),
    )
    preparation = RequestContextPreparation(binding)
    preparation._queue.shutdown(wait=True)
    preparation._queue = SimpleNamespace(submit=submit, shutdown=lambda **kwargs: None)
    preparation.prepare([], 2000, context_window=32768, on_ready=lambda result: None, on_error=errors.append)
    work, future = jobs.pop()
    future.set_result(work())
    callbacks.pop()()
    assert not errors, f"Execution preparation failed before it could reach the background worker: {errors}"
    assert len(jobs) == 1


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_context_attachment_damage_is_a_preparation_wait_not_a_business_failure(tmp_path, damage):
    store = AssistantContextStore(AssistantTranscriptStore(str(tmp_path)))
    request = UserRequest("r", "s", "goal", (RequestItem("i", "answer"),), source_message_ids=("u",))
    epoch = append_context([{"role": "user", "message_id": "u", "content": "original"}], request, {}, config_digest="c")
    staged = store.stage(epoch)
    path = tmp_path.joinpath(*staged.head["artifact"]["path"].split("/"))
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"corrupt")
    with pytest.raises(PreparationWait, match="CONTEXT_RECOVERY_REQUIRED"):
        store.read("s", "r", (), staged.head)


def test_independent_followup_receives_authorized_parent_decision_context(composed):
    _, service, context = composed
    parent_id = _request(service, context, "Keep XML ordering; the earlier decision is DECISION-ALPHA.")
    service.accept_input(context, "Explain why we chose that approach", selection={}, command_id="follow")
    batch = service.prepare_batch(context)
    proposal = _proposal(batch)
    proposal["directives"][0].update(action="FOLLOW_UP", target_id=parent_id, expected_revision=1)
    state = service.apply_routing(context, batch.batch_id, proposal)
    follow_id = state["requests"][-1]["request_id"]
    request, admission = _admit(service, context, follow_id)
    assert request.related_to == parent_id
    runtime = ContextRuntime(service, context, admission, client=object(), config=LLMConfig())
    messages, _ = runtime.prepare(RequestModelInput((), (), ContextBudget(), request=request))
    assert "DECISION-ALPHA" in str(messages)
    after = service.state(context)
    assert after["requests"] == state["requests"]


def test_an_unpublished_stage_cannot_replace_a_committed_head(tmp_path):
    store = AssistantContextStore(AssistantTranscriptStore(str(tmp_path)))
    request = UserRequest("r", "s", "goal", (RequestItem("i", "answer"),), source_message_ids=("u",))
    epoch = append_context([{"role": "user", "message_id": "u", "content": "original"}], request, {}, config_digest="c")
    committed = store.stage(epoch)
    store.stage(replace(epoch, epoch_id="unpublished", reason="configuration_changed"), committed)
    assert store.read("s", "r", (), committed.head).epoch == epoch


def test_amend_discards_partial_summary_from_previous_request_revision(composed):
    _, service, context = composed
    request_id = _request(service, context)
    service.save_history(
        context,
        [
            {"role": "assistant", "message_id": "old", "content": "Earlier discussion"},
            {"role": "assistant", "message_id": "tail", "content": "Latest discussion"},
        ],
        request_id=request_id,
    )
    request, admission = _admit(service, context, request_id)
    config = LLMConfig()
    runtime = ContextRuntime(service, context, admission, client=object(), config=config)
    runtime.prepare(RequestModelInput((), (), ContextBudget(), request=request))
    old_head = service.state(context)["context_heads"][request_id]
    committed = runtime.store.read(context.session_id, request_id, request.scope, old_head)
    partial = replace(
        committed.epoch,
        epoch_id="partial-old-revision",
        summaries=(CompactionSummary("old-pending", "OLD-UNPUBLISHED-SEMANTICS", ("old",)),),
        items=tuple(i for i in committed.epoch.items if i.item_id != "old"),
        reason="compaction",
    )
    staged = runtime.store.stage(partial, committed)
    save_wait(
        service,
        context,
        admission,
        PreparationWait("COMPACTION_CALL_LIMIT", "continue explicitly"),
        config_digest=runtime.config_digest,
        pending=staged,
    )
    service.scheduler.release(admission)
    service.accept_input(context, "Amend the goal to focus on exact original facts", selection={}, command_id="amend")
    batch = service.prepare_batch(context)
    proposal = _proposal(batch)
    proposal["directives"][0].update(action="AMEND", target_id=request_id, expected_revision=1)
    service.apply_routing(context, batch.batch_id, proposal)
    request, admission = _admit(service, context, request_id)
    assert request.revision == 2
    clear_wait(service, context, request_id)
    resumed = ContextRuntime(service, context, admission, client=object(), config=config)
    messages, _ = resumed.prepare(RequestModelInput((), (), ContextBudget(), request=request))
    assert "OLD-UNPUBLISHED-SEMANTICS" not in str(messages)


@pytest.mark.parametrize("control", ["read_request_history", "read_request_result"])
def test_maximum_valid_history_page_reaches_model_without_another_reference(control):
    page = {
        "source_id": "original",
        "offset": 0,
        "end": 8000,
        "total": 8000,
        "digest": "source-digest",
        "material_only": True,
        "text": "x" * 8000,
    }
    record = {
        "message_id": "retrieval-receipt",
        "role": "tool",
        "name": control,
        "tool_call_id": "query",
        "content": json.dumps(page),
    }
    projected = project_result(record)
    assert json.loads(projected["content"]) == page


def test_new_user_input_persists_while_summary_is_waiting_for_network(composed):
    _, service, context = composed
    entered, release = Event(), Event()

    def blocked_summary():
        entered.set()
        release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as input_queue:
        binding = SimpleNamespace(service=service, _queue=input_queue, delivered=SimpleNamespace(emit=lambda _: None))
        preparation = RequestContextPreparation(binding)
        try:
            preparation._submit(blocked_summary, lambda _: None)
            assert entered.wait(1)
            accepted = input_queue.submit(
                service.accept_input, context, "cancel pending work", selection={}, command_id="during-summary"
            )
            assert accepted.result(timeout=0.5)["message_id"] == "during-summary"
        finally:
            release.set()
            if hasattr(preparation, "close"):
                preparation.close()


def test_cancel_returns_promptly_while_provider_shutdown_is_blocked(composed):
    _, service, _ = composed
    release = Event()
    preparation = RequestContextPreparation(SimpleNamespace(service=service))
    preparation._summary_client = SimpleNamespace(cancel=lambda: release.wait(5))
    with ThreadPoolExecutor(max_workers=1) as gui_call:
        try:
            cancelled = gui_call.submit(preparation.cancel)
            cancelled.result(timeout=0.5)
        finally:
            release.set()
            if hasattr(preparation, "close"):
                preparation.close()


def test_cleanup_retains_full_summary_chain_from_retained_head_and_reclaims_only_orphan(tmp_path):
    artifacts = AssistantTranscriptStore(str(tmp_path))
    store = AssistantContextStore(artifacts)
    request = UserRequest("r", "s", "goal", (RequestItem("i", "answer"),), source_message_ids=("u", "v"))
    epoch = append_context(
        [
            {"role": "user", "message_id": "u", "content": "first"},
            {"role": "user", "message_id": "v", "content": "second"},
        ],
        request,
        {},
        config_digest="c",
    )
    first_epoch = replace(
        epoch,
        summaries=(CompactionSummary("S1", "First summary", ("u",)),),
        items=tuple(i for i in epoch.items if i.item_id != "u"),
    )
    first = store.stage(first_epoch)
    second_epoch = replace(
        first_epoch,
        epoch_id="second",
        summaries=first_epoch.summaries + (CompactionSummary("S2", "Second summary", ("v",)),),
        items=tuple(i for i in first_epoch.items if i.item_id != "v"),
    )
    second = store.stage(second_epoch, first)
    orphan = store.stage(replace(second_epoch, epoch_id="orphan"), second)
    report = AssistantAttachmentCleanup(tmp_path).collect(retained_artifacts=(("s", second.references[-1]),))
    assert {r.path for r in second.references} <= set(report.referenced)
    assert {r.path for r in second.summary_refs} <= set(report.referenced)
    assert orphan.head["artifact"]["path"] in report.candidates
    assert not report.removed
    assert store.read("s", "r", (), second.head).epoch.summaries == second_epoch.summaries


def test_unknown_context_schema_is_rejected_by_reader_and_cleanup(tmp_path):
    artifacts = AssistantTranscriptStore(str(tmp_path))
    store = AssistantContextStore(artifacts)
    future = artifacts.write_artifact(
        "s",
        json.dumps({
            "kind": "assistant_context",
            "schema_version": 999,
            "session_id": "s",
            "request_id": "r",
        }).encode(),
    )
    with pytest.raises(PreparationWait):
        store.read("s", "r", (), {"schema_version": 999, "artifact": future.to_dict()})
    with pytest.raises(BackupVerificationError, match="unknown"):
        AssistantAttachmentCleanup(tmp_path).collect()

"""Background comprehensive review of newly discovered project reference differences."""

from __future__ import annotations

from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import Provenance
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.application.translation.entry_alias import ai_entry_id, ai_entry_key
from transbridge.application.translation.postprocess import PostProcessCandidate
from transbridge.application.translation.proofread_stage import ProofreadStage
from transbridge.infra.limited_llm_client import LimitedLLMClient
from transbridge.infra.llm_client import create_llm_client

from .source_terms import source_run_id
from .workflow_log_store import WorkflowLogStore
from .workflow_logging_client import WorkflowLoggingLLMClient


def source_review_evidence(outcome):
    """Index once per source, preserving the text to which an assessment applies."""
    snapshot = getattr(outcome.translation, "post_process_result", None)
    evidence = {item.entry_key: (item.text, dict(item.report_details)) for item in getattr(snapshot, "candidates", ())}
    for key, result in getattr(outcome.translation, "entry_outcomes", {}).items():
        if getattr(result, "review_details", ()):
            evidence[key] = (result.text, dict(result.review_details))
    for entry in outcome.task.polish_entries:
        result = outcome.polish.get(ai_entry_id(entry))
        if result is None:
            continue
        details = {
            field: getattr(result, field)
            for field in (
                "review_status",
                "review_reason",
                "detected_issues",
                "review_reference_terms",
                "review_contract_version",
                "review_before_text",
            )
            if hasattr(result, field)
        }
        evidence[entry.identity] = (result.polished_translation, details)
    return evidence


def review_source_references(executor, task, references, evidence):
    """Review on the existing worker and commit only validated candidate changes."""
    from .task_translation_resume import proofread_recovery_checkpoint

    run_id = source_run_id(executor.attempt_id or executor.request.run_id, task.key) + "-reference-review"
    candidates = []
    for selected in task.entries:
        if ai_entry_key(selected) not in references:
            continue
        entry = task.collection.get(selected.identity)
        if entry is None:
            raise ValueError("综合校对期间词条已不存在")
        details = dict(evidence.get(entry.identity, ("", {}))[1])
        details["review_before_text"] = entry.translation
        candidates.append(
            PostProcessCandidate(
                run_id=run_id,
                entry_key=entry.identity,
                before_revision=entry.revision,
                original=entry.original,
                before_text=entry.translation,
                text=entry.translation,
                stage=entry.stage,
                context=entry.context or "",
                accepted=True,
                report_details=tuple(details.items()),
            )
        )
    by_key = {
        entry.identity: references[ai_entry_key(entry)] for entry in task.entries if ai_entry_key(entry) in references
    }
    store = WorkflowLogStore(task.esp_path, workflow="reference_review", max_concurrent=executor.config.max_concurrent)
    try:
        client = WorkflowLoggingLLMClient(
            LimitedLLMClient(
                create_llm_client(executor.config),
                executor.request.request_budget,
                cancel_event=executor.stop,
                pause_event=executor.pause,
                lane_key=(task.key, "reference_review"),
            ),
            store,
            channel_prefix="proofread_call",
        )
        stage = ProofreadStage(
            client,
            term_resolver=lambda candidate: by_key[candidate.entry_key],
            target_locale=executor.config.target_lang,
            game_profile=executor.config.game_profile,
            polish_level=executor.config.pp_polish_level,
            model=executor.config.model,
            max_tokens_per_batch=executor.config.max_tokens_per_batch,
            max_output_tokens=executor.config.max_output_tokens,
            checkpoint=proofread_recovery_checkpoint(executor, task, "reference-review"),
        )
        outcome = stage.run(
            tuple(candidates),
            max_workers=executor.config.max_concurrent,
            progress_callback=lambda current, total, message: executor.progress(
                task.key, "term_closure", current, total, message
            ),
            event_callback=lambda message: executor.log(task.key, message),
        )
        while not executor.stop.is_set() and not executor.pause.wait(0.05):
            pass
        if executor.stop.is_set():
            return {}
        accepted = tuple(item for item in outcome.candidates if item.accepted)
        executor.progress(task.key, "term_closure_finalize", 0, len(accepted), "正在核对并写入补充校对结果")
        initial_stages = {item.entry_key: item.stage for item in candidates}
        changes = tuple(
            item for item in accepted if item.text != item.before_text or item.stage != initial_stages[item.entry_key]
        )
        if changes:
            context = RequestContext(
                "ai-project-reference-review",
                run_id=run_id,
                permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
            )
            committed = task.collection.apply(
                ChangeSet(
                    run_id=run_id,
                    patches=tuple(
                        EntryPatch.create(item.entry_key, translation=item.text, stage=item.stage) for item in changes
                    ),
                    expected_revisions=tuple((item.entry_key, item.before_revision) for item in changes),
                    provenance=Provenance(run_id, context.owner_id, "ai-project-reference-review"),
                ),
                context,
            )
            if committed.status is not MutationStatus.APPLIED:
                raise RuntimeError("综合校对未能写入任务草稿：" + "; ".join(d.message for d in committed.diagnostics))
        result = {}
        for item in accepted:
            entry = task.collection.get(item.entry_key)
            if entry is None or entry.translation != item.text:
                raise RuntimeError("综合校对结果与当前任务草稿不一致")
            details = dict(item.report_details)
            result[ai_entry_key(entry)] = TranslationEntryOutcome(
                item.entry_key,
                "succeeded",
                item.text,
                entry.stage,
                str(details.get("review_reason", "")),
                item.report_details,
            )
        # Technical review failures leave the already translated candidate intact.
        # The caller marks omitted results for review without failing translation.
        for diagnostic in outcome.diagnostics:
            executor.log(task.key, f"综合校对：{diagnostic.message}")
        executor.progress(task.key, "term_closure_finalize", len(accepted), len(accepted), "补充校对结果已写入任务草稿")
        return result
    finally:
        store.close()

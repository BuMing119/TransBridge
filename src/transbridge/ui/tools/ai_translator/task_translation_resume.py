"""Validate durable translation candidates before reusing them in a fresh draft."""

from __future__ import annotations

from transbridge.ai_translator.prepared_names import PreparedName
from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.protected_syntax import protected_syntax_matches


def prepare_translation_recovery(executor, tasks):
    """Read on the worker; candidates never mutate formal or task collections here."""
    record = getattr(executor.request, "recovery_record", None)
    if not record:
        return {}
    from transbridge.application.translation.task_candidate_recovery import load_recovery_candidates

    sources = {source["key"]: source for source in record["sources"]}
    recovered = {}
    for task in tasks:
        if executor.stop.is_set():
            break
        while not executor.pause.wait(0.05):
            if executor.stop.is_set():
                return recovered
        source = sources.get(task.key)
        if source is None or not task.translate_entries:
            continue
        executor.progress(task.key, "recover", 0, len(task.translate_entries), "正在校验已保存的翻译候选")
        before = {EntryKey.from_dict(item["entry_key"]): item for item in source.get("inputs", ())}
        candidates = load_recovery_candidates(record, task.key, executor.config)
        values, changed = {}, 0
        for entry in task.translate_entries:
            if executor.stop.is_set():
                break
            candidate = candidates.get(entry.identity)
            if candidate is None:
                continue
            original = before.get(entry.identity)
            if original is None or (
                entry.original,
                entry.translation,
                entry.stage,
                entry.context or "",
            ) != (
                original["original"],
                original["translation"],
                original["stage"],
                original.get("context", ""),
            ):
                changed += 1
                continue
            if not candidate.text.strip() or not protected_syntax_matches(entry.original, candidate.text):
                changed += 1
                continue
            values[entry.identity] = PreparedName(entry.original, entry.context or "", candidate.text)
        recovered[task.key] = values
        executor.log(task.key, f"已恢复 {len(values)} 条翻译候选；将在新任务草稿中重新提交并完成后续检查")
        if changed:
            executor.log(task.key, f"{changed} 条候选的输入或结构已变化，保留旧记录并按当前词条重新处理")
        executor.progress(task.key, "recover", len(task.translate_entries), len(task.translate_entries), "候选校验完成")
    return recovered


def postprocess_recovery_options(executor, task):
    """A stable task/source identity; settings and input hashes isolate changed work."""
    if getattr(executor.request, "recovery_directory", None) is None:
        return {}
    from transbridge.application.translation.task_config_snapshot import execution_signature as signature
    from transbridge.application.translation.workload_models import canonical_hash

    from .source_terms import source_run_id

    binding = getattr(executor.request, "terminology_binding", None)
    ref = getattr(binding, "snapshot_ref", None)
    identity = getattr(executor.request, "recovery_task_id", None) or executor.request.run_id
    return {
        "postprocess_identity": source_run_id(identity, task.key),
        "postprocess_settings": canonical_hash({
            "config": signature(executor.config),
            "terminology": getattr(ref, "snapshot_identity", None),
        }),
        "postprocess_root": executor.request.recovery_directory / "ai-translation-resume",
        "postprocess_proofread_checkpoint": proofread_recovery_checkpoint(executor, task, "translation-proofread"),
    }


def proofread_recovery_checkpoint(executor, task, stage):
    """Keep successful review batches even when the enclosing phase is interrupted."""
    directory = getattr(executor.request, "recovery_directory", None)
    if directory is None:
        return None
    from transbridge.application.translation.proofread_checkpoint import ProofreadCheckpoint, proofread_settings

    owner = executor.request.spec.owner
    return ProofreadCheckpoint(
        directory / "ai-proofread-resume",
        project=str(owner.project_id),
        variant=str(owner.variant_id),
        source=f"{task.key}:{stage}",
        settings=proofread_settings(executor.config),
        task_id=getattr(executor.request, "recovery_task_id", None) or executor.request.run_id,
    )

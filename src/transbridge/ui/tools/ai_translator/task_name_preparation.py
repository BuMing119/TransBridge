"""Prepare all participating project names before any source workflow starts."""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
import logging
from pathlib import Path

from transbridge.ai_translator.generated_term_writer import GeneratedTermWriter
from transbridge.ai_translator.name_preparation_authority import SourceNameAuthority
from transbridge.ai_translator.name_preparation_plan import collect_project_names, resolve_project_names
from transbridge.ai_translator.prepared_names import PreparedName
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.ai_translator.term_validation import valid_term_pair
from transbridge.application.terminology.identity import normalize_original
from transbridge.application.translation.protected_syntax import protected_syntax_matches
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .project_name_progress import ProjectNameProgressReporter
from .source_terms import source_terminology_path
from .task_name_reuse import commit_reused_names

logger = logging.getLogger(__name__)


class _PreparationCancelled(Exception):
    """Internal scan cancellation; it does not represent a failed name."""


def prepare_project_names(executor, tasks):
    """Commit known names first; prepare only unresolved names through the model."""
    executor.prepared_name_term_counts = {}
    if not hasattr(executor, "reused_drafts"):
        executor.reused_drafts = {}
    if executor.shared_term_pool is None:
        return {}
    tasks = tuple(tasks)
    progress = ProjectNameProgressReporter((task.key for task in tasks), getattr(executor, "project_progress", None))
    executor.name_progress = progress
    progress.begin(
        "scan",
        {task.key: len(task.translate_entries) if hasattr(task.translate_entries, "__len__") else 0 for task in tasks},
        "统计项目名称",
    )
    if not _wait(executor):
        progress.cancel()
        return {}
    by_source = {task.key: task for task in tasks}
    try:
        groups, managers, authorities = _plan_names(executor, tasks)
    except _PreparationCancelled:
        progress.cancel()
        return {}
    if executor.stop.is_set():
        progress.cancel()
        return {}
    if not groups:
        progress.finish({}, "没有待准备的项目名称")
        return {}

    source_counts = Counter(occ.source_key for group in groups for occ in group.occurrences)
    occurrence_count = sum(source_counts.values())
    for task in tasks:
        count = source_counts[task.key]
        if count:
            message = f"项目名称准备：{occurrence_count} 次出现，{len(groups)} 个独立名称；本来源 {count} 条"
            executor.log(task.key, message)
            executor.progress(task.key, "project_names", 0, count, message)

    results, source_groups = {}, defaultdict(list)
    recovered = getattr(executor, "recovered_translations", {})
    recovered_groups = set()
    for index, group in enumerate(groups):
        saved = next(
            (
                value
                for occurrence in group.occurrences
                if (value := recovered.get(occurrence.source_key, {}).get(occurrence.entry.identity)) is not None
            ),
            None,
        )
        if saved is not None:
            # The candidate may have survived before shared-name publication.
            # Rejoin the ordinary validation/publication path without a model call.
            results[index] = (saved.text, _invalid_text(group, saved.text))
            recovered_groups.add(index)
            continue
        existing = group.authority.existing_translations
        known = group.authority.exact_translation
        if known is not None or len(existing) == 1:
            # Reuse effective authority (including aliases) or an eligible
            # local name. Neither promotes local translations into the pool.
            text = known if known is not None else existing[0]
            results[index] = (text, _invalid_text(group, text))
        else:
            source_groups[group.representative.source_key].append((index, group))
    committed_reuse = commit_reused_names(
        executor,
        tasks,
        groups,
        {index: result for index, result in results.items() if index not in recovered_groups},
        managers,
        wait=_wait,
    )
    progress.begin(
        "translate",
        {source: len(selected) for source, selected in source_groups.items()},
        "翻译项目名称首批",
        details={
            task.key: (
                "等待翻译代表名称"
                if task.key in source_groups
                else "已有名称已写入任务草稿"
                if task.key in executor.reused_drafts
                else "共享名称由其他来源准备"
                if source_counts[task.key]
                else "本来源没有待翻译名称"
            )
            for task in tasks
        },
        name_count=len(groups),
        occurrence_count=occurrence_count,
        reused_count=len(committed_reuse),
    )
    capacity = min(len(source_groups), max(2, executor.config.max_concurrent))
    if capacity:
        work = list(source_groups.items())
        next_index = 0
        with ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="ai-project-names") as pool:
            pending = set()
            while pending or (next_index < len(work) and not executor.stop.is_set()):
                while len(pending) < capacity and next_index < len(work) and not executor.stop.is_set():
                    source, selected = work[next_index]
                    pending.add(pool.submit(_prepare_source, executor, by_source[source], selected))
                    next_index += 1
                if pending:
                    ready, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in ready:
                        completed = future.result()
                        results.update(completed)
                        if executor.stop.is_set():
                            progress.cancel()
                        settled = Counter(
                            groups[index].representative.source_key
                            for index, (_, reason) in completed.items()
                            if reason != "任务已取消"
                        )
                        for source, count in settled.items():
                            progress.update("translate", source, current=count, detail="代表名称翻译已返回")
        for _, selected in work[next_index:]:
            results.update({index: ("", "任务已取消") for index, _ in selected})

    # Validate identity/structure before reuse. Terminology differences are
    # advisory findings for subsequent comprehensive proofreading.
    groups = _validate_results(executor, groups, results, authorities, committed_reuse=committed_reuse)
    cache = defaultdict(dict)
    for index, group in enumerate(groups):
        text, reason = results[index]
        for occurrence in group.occurrences:
            entry = occurrence.entry
            cache[occurrence.source_key][entry.identity] = PreparedName(
                entry.original, entry.context or "", text if not reason else "", reason
            )
            if reason:
                label = "名称预翻译已取消" if reason == "任务已取消" else "名称预翻译失败"
                executor.log(occurrence.source_key, f"{label} [{entry.key}] {entry.original}：{reason}")

    if _wait(executor):
        _publish(executor, groups, results, managers)
    else:
        progress.cancel()
    with _access(executor):
        if executor.consistency:
            executor.consistency.require_current(executor.request)
    for source, prepared in cache.items():
        failed = sum(bool(item.reason) and item.reason != "任务已取消" for item in prepared.values())
        cancelled = sum(item.reason == "任务已取消" for item in prepared.values())
        accepted = executor.prepared_name_term_counts.get(source, 0)
        state = "已取消" if executor.stop.is_set() else "完成"
        message = f"项目名称准备{state}：{len(prepared)} 条，失败 {failed} 条，新增共享术语 {accepted} 条"
        if cancelled:
            message += f"，取消 {cancelled} 条"
        executor.log(source, message)
        executor.progress(source, "project_names", len(prepared) - cancelled, len(prepared), message)
    if executor.stop.is_set():
        progress.cancel()
    else:
        failed = sum(bool(reason) for _, reason in results.values())
        progress.finish(
            Counter(group.representative.source_key for group in groups),
            f"项目名称准备完成：{len(groups)} 个独立名称，失败 {failed} 个",
        )
    return dict(cache)


def _plan_names(executor, tasks):
    project_progress = getattr(executor, "name_progress", None)

    def checkpoint():
        _checkpoint(executor)

    def progress(task, current, total):
        message = f"统计名称：已扫描 {current}/{total} 条"
        if current == 0:
            executor.log(task.key, "开始统计重复名称")
        executor.progress(task.key, "project_names", current, total, message)
        if project_progress is not None:
            project_progress.update("scan", task.key, current=current, detail="统计名称")

    candidates = collect_project_names(
        tasks, overwrite=executor.request.spec.overwrite, checkpoint=checkpoint, progress=progress
    )
    selected = defaultdict(set)
    for candidate in candidates:
        for occurrence in candidate.occurrences:
            selected[occurrence.source_key].add(occurrence.entry.identity)
    managers, authorities = {}, {}
    if not candidates:
        return (), managers, authorities
    total = sum(len(candidate.occurrences) for candidate in candidates)
    message = f"名称统计完成：{total} 次出现，按原文和类型去重后 {len(candidates)} 个名称"
    if project_progress is not None:
        project_progress.begin(
            "load",
            {source: 1 for source in selected},
            "加载项目名称术语",
            name_count=len(candidates),
            occurrence_count=total,
        )
    # The cheap collection pass completes before opening term databases. Loading
    # and cross-scope checks are visible separately from model request counters.
    with _access(executor):
        for task in tasks:
            if task.key in selected:
                executor.log(task.key, message)
                executor.progress(task.key, "project_names", 0, len(selected[task.key]), "等待加载名称术语")
        for task in tasks:
            if task.key not in selected:
                continue
            checkpoint()
            executor.log(task.key, "加载名称首批术语")
            executor.progress(task.key, "project_names", 0, len(selected[task.key]), "加载名称首批术语")
            if project_progress is not None:
                project_progress.update("load", task.key, detail="加载名称首批术语")
            manager = _manager(executor, task)
            managers[task.key] = manager
            authorities[task.key] = SourceNameAuthority(
                manager, task.collection, excluded=selected[task.key], checkpoint=checkpoint
            )
            if project_progress is not None:
                project_progress.update("load", task.key, current=1, detail="名称术语已加载")

        checked = Counter()
        if project_progress is not None:
            project_progress.begin(
                "resolve", {source: len(entries) for source, entries in selected.items()}, "检查名称复用约束"
            )

        def authority_for(occurrence, repeated):
            source = occurrence.source_key
            authority = authorities[source].for_entry(occurrence.entry, check_required_terms=False)
            if repeated or authority.exact_translation is not None or authority.existing_translations:
                authority = authorities[source].for_entry(occurrence.entry)
            checked[source] += 1
            if checked[source] == 1 or checked[source] % 256 == 0 or checked[source] == len(selected[source]):
                executor.progress(source, "project_names", checked[source], len(selected[source]), "检查名称复用约束")
                if project_progress is not None:
                    project_progress.update("resolve", source, current=checked[source], detail="检查名称复用约束")
            return authority

        groups = resolve_project_names(candidates, authority_for=authority_for, checkpoint=checkpoint)
    return groups, managers, authorities


def _validate_results(executor, groups, results, authorities, *, committed_reuse=frozenset()):
    progress = getattr(executor, "name_progress", None)
    checked = Counter()
    totals = Counter(
        group.representative.source_key for index, group in enumerate(groups) if index not in committed_reuse
    )
    if progress is not None:
        if executor.stop.is_set():
            progress.cancel()
        else:
            progress.begin("validate", totals, "校验名称翻译结果")
    validated = list(groups)
    for index, group in enumerate(groups):
        if index in committed_reuse:
            continue  # Already validated and committed; cancellation cannot erase this evidence.
        text, reason = results[index]
        if not _wait(executor):
            if not reason:
                results[index] = ("", "任务已取消")
            if progress is not None:
                progress.cancel()
            continue
        occurrence = group.representative
        if not reason and index % 256 == 0:
            executor.progress(occurrence.source_key, "project_names", index, len(groups), "校验名称翻译结果")
        if not reason:
            results[index] = (text, _invalid_text(validated[index], text))
        checked[occurrence.source_key] += 1
        count = checked[occurrence.source_key]
        if progress is not None and (count == 1 or count % 256 == 0 or count == totals[occurrence.source_key]):
            progress.update("validate", occurrence.source_key, current=checked[occurrence.source_key])
    return tuple(validated)


def _manager(executor, task):
    config = deepcopy(executor.config)
    config.enable_semantic_match = False
    manager = TermDatabaseManager(
        config,
        source_terminology_path(task.esp_path, task.key),
        executor.client,
        executor.project_id,
        shared_term_pool=executor.shared_term_pool,
        generated_term_plugin_id=Path(task.esp_path).name if task.esp_path else None,
        reference_store=getattr(executor, "reference_store", None),
        reference_source=task.key,
        **executor.request.terminology_binding.term_database_kwargs(),
    )
    manager.load_all()
    if executor.consistency:
        executor.consistency.observe_terms(task.key, "project_names", manager)
    return manager


def _prepare_source(executor, task, selected):
    from .workflow_log_store import WorkflowLogStore

    if not _wait(executor):
        return {index: ("", "任务已取消") for index, _ in selected}
    entries = tuple(deepcopy(group.representative.entry) for _, group in selected)
    collection = TranslationEntryCollection(entries)
    isolated = replace(task, collection=collection, translate_entries=entries, polish_entries=())
    store = WorkflowLogStore(task.esp_path, workflow="project_names", max_concurrent=executor.config.max_concurrent)
    try:
        if executor.log_ready is not None:
            executor.log_ready(task.key, store.log_dir)
        executor.log(task.key, f"项目名称准备：翻译 {len(entries)} 个代表名称；日志 {store.log_dir}")
        result = executor._translate(isolated, store, name_preparation=True)
    except Exception as exc:
        logger.exception("Project name preparation failed for source %s", task.key)
        return {index: ("", str(exc) or type(exc).__name__) for index, _ in selected}
    finally:
        store.close()
    results = {}
    for index, group in selected:
        original = group.representative.entry
        outcome = getattr(result, "entry_outcomes", {}).get(original.identity)
        current = collection.get(original.identity)
        reason = "任务已取消" if executor.stop.is_set() else ""
        text = ""
        if not reason and (outcome is None or outcome.status != "succeeded"):
            reason = getattr(outcome, "reason", "") or "没有已提交的名称翻译结果"
        if not reason:
            text = outcome.text
            if (
                outcome.entry_key != original.identity
                or current is None
                or current.original != original.original
                or current.context != original.context
                or current.translation != text
                or current.stage != outcome.stage
            ):
                reason = "名称候选与隔离副本的提交证据不一致"
            else:
                reason = _invalid_text(group, text)
        results[index] = (text, reason)
    return results


def _invalid_text(group, text):
    original = group.representative.entry.original
    if not text.strip() or not valid_term_pair(original, text):
        return "未返回有效名称译文"
    if not protected_syntax_matches(original, text):
        return "名称译文改变了受保护标记"
    return ""


def _publish(executor, groups, results, managers):
    progress = getattr(executor, "name_progress", None)
    by_term = defaultdict(list)
    for index, group in enumerate(groups):
        by_term[normalize_original(group.representative.entry.original)].append((index, group))
    writes = defaultdict(list)
    for related in by_term.values():
        # A normalized pool key cannot express category-dependent, spelling-
        # dependent, or plugin-local decisions. Such names remain per-entry.
        if len(related) != 1:
            continue
        index, group = related[0]
        text, reason = results[index]
        authority = group.authority
        if reason or authority.blocked or authority.exact_translation is not None or authority.existing_translations:
            continue
        occurrence = group.representative
        manager = managers[occurrence.source_key]
        context = manager.lookup_context_for_entry(occurrence.entry)
        writes[(occurrence.source_key, context)].append((
            occurrence.entry.original,
            text,
            "auto_project_name",
            occurrence.entry.context or "",
        ))
    totals = Counter()
    for (source, _), terms in writes.items():
        totals[source] += len(terms)
    if progress is not None:
        progress.begin("publish", totals, "发布项目共享名称术语")
    published = Counter()
    for (source, context), terms in writes.items():
        if not _wait(executor):
            if progress is not None:
                progress.cancel()
            return
        observer = (
            None
            if executor.consistency is None
            else lambda manager, accepted, source=source: executor.consistency.record_generated_terms(
                source, manager, accepted
            )
        )
        access = None if executor.consistency is None else executor.consistency.terminology_write_access
        count = GeneratedTermWriter(managers[source], access=access).save(terms, context=context, observer=observer)
        executor.prepared_name_term_counts[source] = executor.prepared_name_term_counts.get(source, 0) + count
        published[source] += len(terms)
        if count < len(terms):
            executor.log(source, f"本批 {len(terms) - count} 个名称未新增到共享术语库；名称译文仍保留并参与后续校对")
        if progress is not None:
            progress.update("publish", source, current=published[source], detail=f"名称术语处理完成；新增 {count} 条")


def _access(executor):
    return executor.consistency.terminology_access() if executor.consistency else nullcontext()


def _wait(executor):
    while not executor.stop.is_set():
        if executor.pause.wait(0.05):
            return not executor.stop.is_set()
    return False


def _checkpoint(executor):
    if not _wait(executor):
        raise _PreparationCancelled()

"""Locale-aware dictionary candidates committed through the project boundary."""

from dataclasses import replace

from transbridge.translation_memory.contracts import TmMatchStatus, TranslationMemoryQuery
from transbridge.translation_memory.manager import TranslationMemoryManager
from transbridge.translation_memory.service import TranslationMemoryQueryService

from .entry_task_results import EntryTaskResults
from .types import ExecutionContext


def dictionary_scope(args, ctx, *, required: bool):
    request = getattr(ctx, "request_context", None) or getattr(ctx, "runtime_context", None)
    metadata = dict(getattr(request, "metadata", ()) or ())
    source = args.get("source_locale") or metadata.get("source_locale") or getattr(ctx, "source_locale", "")
    target = args.get("target_locale") or metadata.get("target_locale") or getattr(ctx, "target_locale", "")
    if isinstance(ctx, ExecutionContext):
        fingerprint = ctx._target_source_fingerprint
    else:
        snapshot = getattr(getattr(ctx, "active_slot", None), "source_snapshot", None)
        fingerprint = getattr(snapshot, "sha256", "")
    if required and (not source or not target or not fingerprint):
        raise ValueError("请指定 source_locale 和 target_locale，并先解析可验证的来源快照；未知语言不会自动套用词典。")
    return str(source), str(target), fingerprint


def apply_dictionary(args, ctx, collection):
    source_locale, target_locale, fingerprint = dictionary_scope(args, ctx, required=True)
    task = EntryTaskResults(
        ctx,
        collection,
        kind="dictionary",
        inputs={
            "source_locale": source_locale,
            "target_locale": target_locale,
            "source_fingerprint": fingerprint,
            "overwrite": bool(args.get("overwrite", False)),
        },
    )
    manager = TranslationMemoryManager()
    manager.load()
    service = TranslationMemoryQueryService(manager)
    data = {"applied": 0, "key_hits": 0, "text_hits": 0, "misses": 0, "needs_review": 0, "conflicts": 0}
    changes = []
    error = ""
    for entry in task.base.values():
        if entry.translation and not args.get("overwrite", False):
            continue
        try:
            cancellation = getattr(ctx, "cancellation_token", None)
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            result = service.query(
                TranslationMemoryQuery(
                    entry.identity,
                    entry.original,
                    source_locale,
                    target_locale,
                    entry.stage,
                    fingerprint,
                )
            )
        except Exception as exc:
            error = f"词典查询未完成：{exc}"
            break
        selected = result.selected
        if result.requires_confirmation or (selected is not None and selected.match_status is TmMatchStatus.STALE):
            data["needs_review"] += 1
            data["conflicts"] += int(result.requires_confirmation)
            continue
        if selected is None:
            data["misses"] += 1
            continue
        changes.append(replace(entry, translation=selected.translation, stage=1))
        data["applied"] += 1
        data[f"{selected.matched_via}_hits"] += 1
    task.candidates(changes)
    result = task.finish(error=error)
    data["applied"] = result.data.get("merge", {}).get("applied_count", 0)
    result.data.update(data)
    return result

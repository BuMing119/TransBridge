"""Locale-aware dictionary candidates committed through the project boundary."""

from dataclasses import replace

from transbridge.translation_memory.contracts import TmMatchStatus, TranslationMemoryQuery
from transbridge.translation_memory.manager import TranslationMemoryManager
from transbridge.translation_memory.service import TranslationMemoryQueryService

from ._project_tool_mutations import ProjectToolTarget
from .base import ToolResult


def dictionary_scope(args, ctx, *, required: bool):
    request = getattr(ctx, "request_context", None) or getattr(ctx, "runtime_context", None)
    metadata = dict(getattr(request, "metadata", ()) or ())
    source = args.get("source_locale") or metadata.get("source_locale") or getattr(ctx, "source_locale", "")
    target = args.get("target_locale") or metadata.get("target_locale") or getattr(ctx, "target_locale", "")
    snapshot = getattr(getattr(ctx, "active_slot", None), "source_snapshot", None)
    fingerprint = getattr(snapshot, "sha256", "")
    if required and (not source or not target or not fingerprint):
        raise ValueError("请指定 source_locale 和 target_locale，并先解析可验证的来源快照；未知语言不会自动套用词典。")
    return str(source), str(target), fingerprint


def apply_dictionary(args, ctx, collection):
    target = ProjectToolTarget.capture(ctx)
    source_locale, target_locale, fingerprint = dictionary_scope(args, ctx, required=True)
    manager = TranslationMemoryManager()
    manager.load()
    service = TranslationMemoryQueryService(manager)
    data = {"applied": 0, "key_hits": 0, "text_hits": 0, "misses": 0, "needs_review": 0, "conflicts": 0}
    changes = []
    for entry in collection:
        if entry.translation and not args.get("overwrite", False):
            continue
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
    target.commit_records(changes)
    return ToolResult.ok(
        f"词典套用: 命中 {data['applied']}，待确认 {data['needs_review']}，未命中 {data['misses']}", data=data
    )

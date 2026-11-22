# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Resolve assistant arguments against the desktop task scope and execution rules."""

from transbridge.application.translation.task_scope import TranslationScope, task_entry_groups
from transbridge.application.workbench_filters import FilterState

from .translation_options import translation_config, validate_polish_options
from .workbench_rows import filtered_tool_rows


def translation_candidates(args, ctx, collection, *, mode, scope_override=None):
    if mode not in {"translate", "polish", "mixed"}:
        raise ValueError(f"无效翻译模式：{mode}")
    if mode == "polish":
        validate_polish_options(args)
    scope = TranslationScope.from_filters(
        (getattr(ctx, "translation_scope", None) or {}) if scope_override is None else scope_override
    )
    entry_ids = args.get("entry_ids")
    if entry_ids is not None:
        if not isinstance(entry_ids, list) or any(not isinstance(key, str) or not key for key in entry_ids):
            raise ValueError("entry_ids 必须是完整词条标识列表。")
        explicit = set()
        for key in entry_ids:
            entry = collection.get(key) if collection else None
            if entry is None:
                raise ValueError(f"词条不存在或标识不唯一，请重新核对完整词条标识：{key}")
            explicit.add(entry.identity)
        scope = TranslationScope(preset="selection", selected_entry_ids=frozenset(key.serialize() for key in explicit))
    elif scope_override is None and not (
        scope.stage_filters or scope.label_filters or scope.category_filters or scope.preset
    ):
        scope = (
            TranslationScope()
            if mode == "polish" and args.get("scope") == "all"
            else TranslationScope.default_for_mode(mode)
        )
    if "scope" in args and (mode != "polish" or args["scope"] != "all"):
        raise ValueError("无效 scope：请通过 set_scope 的阶段、标签或分类选择词条。")
    rows = filtered_tool_rows(ctx, collection, FilterState(), getattr(ctx, "entry_labels", None)) if collection else ()
    memberships = {}

    def observed_rows():
        for entry, key, labels in rows:
            if mode == "mixed" and labels:
                memberships[entry.identity] = labels
            yield entry, key, labels

    entries = scope.candidates_from_rows(observed_rows())
    if mode == "polish":
        entries = [entry for entry in entries if entry.translation]
    return scope, entries, memberships


def preview_translation_scope(args, ctx, collection, *, mode, scope_override=None):
    scope, candidates, memberships = translation_candidates(
        args, ctx, collection, mode=mode, scope_override=scope_override
    )
    config = translation_config(mode, args) if candidates and (mode != "translate" or "profile" in args) else None
    translate, polish = task_entry_groups(candidates, mode=mode, config=config, memberships=memberships)
    return scope, [*translate, *polish]


def translation_targets(args, ctx, collection, *, mode, scope_override=None):
    _, entries = preview_translation_scope(args, ctx, collection, mode=mode, scope_override=scope_override)
    return entries

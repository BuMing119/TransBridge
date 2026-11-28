# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind personal dictionary operations to an admitted workbench source."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from transbridge.application.contracts import RequestContext
from transbridge.application.io.migration_snapshot import freeze_collection_entries
from transbridge.application.projects.dictionary_apply import (
    dictionary_library,
    dictionary_mod_id,
    dictionary_source_path,
    prepare_library_application,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.translation_memory.library import DictionaryLibrary

from ._project_tool_mutations import ProjectToolTarget
from .diagnostics import technical_error
from .types import ExecutionContext, ToolResult
from .undo_capture import capture_variant_command
from .workbench_rows import check_workbench_source

if TYPE_CHECKING:
    from transbridge.application.projects.gui_facade import GuiProjectCommandFacade


@dataclass(frozen=True)
class _DictionaryInputs:
    target: ProjectToolTarget
    library: DictionaryLibrary
    mod_id: str
    read: Callable[[], tuple[TranslationEntry, ...]]
    check: Callable[[], None]
    commands: GuiProjectCommandFacade | None
    caller: RequestContext | None


def _library_key(library: DictionaryLibrary) -> tuple[Path, str, str, str]:
    return library.base_dir, library.game, library.source_locale, library.target_locale


def _capture(
    execution: ExecutionContext, checkpoint: Callable[[], None], *, selected_only: bool = False
) -> _DictionaryInputs | None:
    target = ProjectToolTarget.capture(execution)
    if target.collection is None:
        return None
    app = target.app
    current_collection = app.collection
    collection_revision = getattr(current_collection, "collection_revision", None)

    def check_source():
        checkpoint()
        check_workbench_source(execution, app)
        if dictionary_source_path(app) != execution._target_dictionary_source_path:
            raise ValueError("词典匹配的来源文件已变化，请重新核对。")
        if (
            (target.project_revision, target.variant_revision)
            != (
                getattr(app, "project_revision", None),
                getattr(app, "variant_revision", None),
            )
            or app.collection is not current_collection
            or getattr(current_collection, "collection_revision", None) != collection_revision
        ):
            raise ValueError("工程或译文已变化，请重新执行词典操作。")

    check_source()
    library = dictionary_library(app, variant_identity=target.identity)
    library_key = _library_key(library)

    def check():
        check_source()
        if _library_key(dictionary_library(app)) != library_key:
            raise ValueError("当前词典的游戏、语言或存储目录已变化，请重新核对。")

    keys = None
    if selected_only:
        keys = tuple(entry.identity for entry in getattr(execution, "selected_entries", ()))
        if not keys:
            raise ValueError("没有选中条目。")
    return _DictionaryInputs(
        target,
        library,
        dictionary_mod_id(app),
        freeze_collection_entries(target.collection, keys),
        check,
        getattr(app, "project_commands", None),
        execution.request_context or getattr(app, "runtime_context", None),
    )


def _execution_context(ctx) -> ExecutionContext:
    if isinstance(ctx, ExecutionContext):
        return ctx
    return ExecutionContext.call_on_app_thread(ctx, lambda: ExecutionContext(app_context=ctx))


def _checkpoint(execution: ExecutionContext) -> Callable[[], None]:
    token = getattr(execution, "cancellation_token", None)
    return token.raise_if_cancelled if token is not None else lambda: None


def _missing_collection() -> ToolResult:
    return ToolResult.fail("当前没有加载翻译集合", error_category="input", error_code="COLLECTION_NOT_LOADED")


def apply_dictionary(ctx) -> ToolResult:
    execution = _execution_context(ctx)
    checkpoint = _checkpoint(execution)
    inputs = execution.call_on_app_thread(execution.app_context, lambda: _capture(execution, checkpoint))
    if inputs is None:
        return _missing_collection()
    target = inputs.target
    if any(
        value is None
        for value in (inputs.caller, target.identity, target.project_revision, target.variant_revision, inputs.commands)
    ):
        return ToolResult.fail("工程写入服务不可用，词典尚未套用。", error_category="config")
    checkpoint()
    plan = prepare_library_application(inputs.read(), inputs.library, mod_file_id=inputs.mod_id)
    warnings = []
    committed = False

    def commit():
        nonlocal committed
        inputs.check()
        if not plan.states:
            return
        result = capture_variant_command(
            execution,
            lambda: plan.commit(inputs.commands, inputs.caller, **target.expected()),
            warnings=warnings,
        )
        if not result.is_success:
            raise ValueError("；".join(item.message for item in result.diagnostics) or "词典套用未提交。")
        committed = True
        target.acknowledge_projection(preserve_scope=True)

    try:
        execution.safe_mutate_wait(commit)
    except Exception as exc:
        if not committed:
            raise
        warnings.append(f"词典已套用，但助手未能确认提交回执：{technical_error(exc)}。请勿重复执行。")
    stats = plan.result
    return ToolResult.ok(
        f"词典已填充 {stats.applied} 条译文，{stats.misses} 条未命中。",
        data={
            "applied": stats.applied,
            "key_hits": stats.key_hits,
            "text_hits": stats.text_hits,
            "misses": stats.misses,
        },
        warnings=warnings or None,
    )


def save_dictionary(ctx, *, selected_only: bool) -> ToolResult:
    execution = _execution_context(ctx)
    gate, effect_id = execution.assistant_gate, execution.assistant_effect_id
    if (gate is None and (effect_id or execution.assistant_required)) or (gate is not None and not effect_id):
        return ToolResult.fail("请求执行许可缺失。", error_category="permission", error_code="TURN_LEASE_STALE")

    checkpoint = _checkpoint(execution)
    app = execution.app_context
    inputs = execution.call_on_app_thread(app, lambda: _capture(execution, checkpoint, selected_only=selected_only))
    if inputs is None:
        return _missing_collection()
    if not inputs.mod_id:
        return ToolResult.fail("无法识别当前来源文件，请先打开要存为词典的来源。", error_category="input")
    warnings = []

    def commit(publish):
        execution.call_on_app_thread(app, inputs.check)
        if gate is None:
            publish()
            return
        committed = False

        def apply():
            nonlocal committed
            publish()
            committed = True

        try:
            decision = gate.commit(effect_id, apply)
            if not decision.accepted:
                raise RuntimeError(f"词典提交请求被拒绝：{decision.reason}")
        except Exception as exc:
            if not committed:
                raise
            warnings.append(f"词典操作已提交，助手回执未确认：{technical_error(exc)}")

    checkpoint()
    saved = inputs.library.save(inputs.read(), inputs.mod_id, checkpoint=checkpoint, commit=commit)
    return ToolResult.ok(f"已将 {saved} 条译文存入个人词典。", data={"saved": saved}, warnings=warnings or None)

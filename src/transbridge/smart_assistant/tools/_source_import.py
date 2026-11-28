# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind desktop file preparation and project commands to the admitted request."""

from dataclasses import replace
from functools import partial
from pathlib import Path

from transbridge.application.io import FormatId
from transbridge.application.io.migration_snapshot import detached_entries
from transbridge.application.io.open_file_options import OpenFileOptions
from transbridge.application.io.open_translation_file import (
    apply_open_file_dictionary,
    prepare_open_file,
    register_opened_source,
)
from transbridge.application.io.translation_import import changed_translation_states, prepare_translation_import
from transbridge.application.projects.dictionary_apply import automatic_dictionary_library
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui.text_encoding import context_text_encodings

from ._project_tool_mutations import ProjectToolTarget
from .diagnostics import technical_error
from .input_prerequisites import source_input_failure
from .types import ExecutionContext, ToolResult
from .undo_capture import capture_variant_command

_FORMATS = {
    "esp": frozenset({FormatId.PLUGIN_SSE}),
    "xt": frozenset({FormatId.XML_XT}),
    "eet": frozenset({FormatId.XML_EET, FormatId.BINARY_EET}),
    "sst": frozenset({FormatId.SST_SSU8, FormatId.SST_SSU9}),
    "json": frozenset({FormatId.JSON_DSD, FormatId.JSON_PARATRANZ, FormatId.JSON_TRANSBRIDGE}),
}


def run_file_operation(file_type, path, args, ctx):
    action = args.get("action", "create_slot")
    if action not in {"create_slot", "append"}:
        raise ValueError("action 必须是 create_slot 或 append。")
    allowed = {"path", "action"}
    if action == "create_slot" and file_type == "esp":
        allowed.add("skip_empty")
    if file_type == "json":
        allowed.add("format")
    if action == "append":
        allowed.add("replace_existing")
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"此操作不支持参数 {', '.join(sorted(unknown))}。")
    replace_existing = args.get("replace_existing", False)
    if type(replace_existing) is not bool:
        raise ValueError("replace_existing 必须是布尔值。")
    skip_empty = args.get("skip_empty", True)
    if type(skip_empty) is not bool:
        raise ValueError("skip_empty 必须是布尔值。")
    hint = None
    if args.get("format", "auto") != "auto":
        hint = FormatId(f"json.{args['format']}")
        if hint is FormatId.JSON_TRANSBRIDGE and action == "create_slot":
            raise ValueError("内部 JSON 只能通过 append 向现有来源导入译文。")
    target = ProjectToolTarget.capture(ctx)
    app = target.app
    caller = getattr(ctx, "request_context", None) or getattr(app, "runtime_context", None)
    if target.identity is None or caller is None or getattr(app, "project_commands", None) is None:
        raise ValueError("请先打开工程版本。")
    project_id, variant_id = target.identity
    if caller.project_id not in (None, project_id) or caller.variant_id not in (None, variant_id):
        raise ValueError("请求所选的工程版本已变化。")
    request = replace(caller, project_id=project_id, variant_id=variant_id)
    authority = target.identity, target.project_revision, target.variant_revision
    cancellation = getattr(ctx, "cancellation_token", None)

    def check_current():
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        target.check()
        if app.project_opening or app.project_view_failed:
            raise ValueError("工作台尚未就绪，请恢复后重新操作。")
        if (app.project_revision, app.variant_revision) != authority[1:]:
            raise ValueError("工程或翻译版本已变化，请重新选择文件。")

    check_current()
    encodings = context_text_encodings(app, request)
    options = encodings.read_options()
    formats = _FORMATS[file_type]
    if action == "create_slot":
        opened = prepare_open_file(
            path,
            OpenFileOptions(format_hint=hint, skip_empty=skip_empty),
            request,
            apply_dictionary=partial(
                apply_open_file_dictionary,
                library=automatic_dictionary_library(app, encodings=encodings, variant_identity=target.identity),
                mod_file_id=Path(path).stem,
                plugin_scope=Path(path).name,
            ),
            allowed_formats=formats,
            cancellation=cancellation,
            **options,
        )
        return _register(target, opened, request, authority, check_current)
    slot = app.active_slot
    if slot is None or target.collection is None:
        raise ValueError("请先选择要导入译文的工程来源。")
    missing = source_input_failure(ctx, target.collection)
    if missing is not None:
        return missing
    from transbridge.ui.coordinators.migration_coordinator import MigrationTarget

    migration_target = MigrationTarget.capture(slot)
    entries = (
        migration_target.read_entries()
        if target.collection is migration_target.collection
        else detached_entries(target.collection)
    )
    draft = prepare_translation_import(
        path,
        TranslationEntryCollection(entries),
        format_hint=hint,
        allowed_formats=formats,
        replace_existing=replace_existing,
        context=request,
        target_snapshot=migration_target.source_snapshot,
        **options,
    )
    states = changed_translation_states(entries, draft.collection)
    warnings = []
    committed = False

    def commit():
        nonlocal committed
        check_current()
        if app.active_slot is not slot or not migration_target.is_current(app):
            raise ValueError("活动来源已变化，操作未提交。")
        if states:
            result = capture_variant_command(
                ctx,
                lambda: app.project_commands.replace_entry_states(
                    states,
                    request,
                    expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
                    expected_project_revision=authority[1],
                    expected_variant_revision=authority[2],
                ),
                warnings=warnings,
            )
            if not result.is_success:
                raise ValueError("；".join(item.message for item in result.diagnostics))
            committed = True
            target.acknowledge_projection(preserve_scope=True)

    try:
        target.dispatch(commit)
    except Exception as exc:
        if not committed:
            raise
        warnings.append(f"译文已导入，但操作回执待核对：{technical_error(exc)}。请勿重复执行。")
    return ToolResult.ok(
        f"已导入 {len(states)} 条译文。",
        data={"action": action, "added_count": len(states), "total_count": len(entries), "target_label": slot.label},
        warnings=warnings or None,
    )


def _register(target, opened, request, authority, check_current):
    from transbridge.ui.coordinators.open_file_coordinator import OpenedSourceView
    from transbridge.ui.source_hydration import source_slot_key

    app = target.app
    identity = target.identity
    label = Path(opened.source.hydration.location).stem
    ctx = target.context
    execution = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=app)
    ui = ExecutionContext(app_context=app).safe_mutate_wait
    cancellation = getattr(ctx, "cancellation_token", None)
    hydration = None
    warnings = []

    def begin():
        check_current()
        return app.begin_project_open()

    def register():
        nonlocal hydration
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        target.check()
        hydration, _ = register_opened_source(app.project_commands, opened, request, authority)

    def install(addition, token):
        if app.active_version_identity != identity:
            raise ValueError("活动工程版本已变化。")
        if not app.install_working_sources(identity, additions=(addition,), view_token=token):
            raise ValueError("工作台暂未同步到提交后的工程版本。")
        target.acknowledge_projection()

    def mark_view_failed():
        if app.active_version_identity == identity and not app.project_opening:
            app.project_view_failed = True

    view = None

    def prepare_and_install(token):
        nonlocal view
        if view is None:
            view = OpenedSourceView(
                source_slot_key(hydration), hydration, opened.strings_lookup, "chinese", opened.note
            )
        addition = view.prepare_slot()
        ui(lambda: install(addition, token))

    def commit():
        # The worker holds request admission only for the project command.
        try:
            execution._run_dispatched(register)
        except Exception as exc:
            if hydration is None:
                raise
            warnings.append(f"来源已登记，但操作回执待核对：{technical_error(exc)}。请勿重复登记。")

    view_token = ui(begin)
    activated = False
    try:
        try:
            commit()
            prepare_and_install(view_token)
            activated = True
        finally:
            ui(lambda: app.finish_project_open(view_token))
        if app.project_view_failed:
            raise ValueError("工作台尚未同步到已登记的来源。")
    except Exception as exc:
        if hydration is None:
            raise
        if activated and not app.project_view_failed:
            warnings.append(f"来源已登记，但工作台收尾失败：{technical_error(exc)}。请重新打开工程。")
        else:
            try:
                prepare_and_install(None)
                activated = True
            except Exception as refresh_error:
                activated = False
                ui(mark_view_failed)
                warnings.append(f"来源已登记。请重新打开工程以刷新工作台：{technical_error(refresh_error)}")
    if cancellation is not None and cancellation.is_cancelled:
        warnings.append("停止时来源已登记，已提交的登记会保留。")
    return ToolResult.ok(
        f"已登记工程来源「{label}」。",
        data={
            "action": "create_slot",
            "label": label,
            "entry_count": len(opened.source.hydration.entries),
            "activated": activated,
        },
        warnings=warnings or None,
    )

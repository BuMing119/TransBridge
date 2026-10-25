"""Append an authoritative source and install the complete workbench together."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.io import EntryKey, FormatId
from transbridge.application.projects import ProjectSourceRequest
from transbridge.persistence.v2 import ProjectId, VariantId, VariantRef
from transbridge.ui.project_view_binding import prepare_project_view
from transbridge.ui.source_hydration import prepare_project_slots


@dataclass(frozen=True, slots=True)
class AddedSourceView:
    snapshot: object
    slots: tuple
    state: object


class ProjectSourceAddOperation:
    def __init__(self, host):
        self._host = host

    def start(self, path: str, expected_identity: tuple[str, str]) -> bool:
        host = self._host
        context = host.context
        if context.active_version_identity != expected_identity:
            host.show_message("目标工程版本已变化，请重新拖入插件。", error=True)
            return False
        if context.authoritative_projection_diverged():
            host.show_message("当前工作台有尚未提交的修改，请先保存或处理编辑错误后再添加插件。", error=True)
            return False
        project_id, variant_id = expected_identity
        variant_ref = VariantRef(VariantId(variant_id), ProjectId(project_id))
        project_revision, variant_revision = context.project_revision, context.variant_revision
        old_slots = tuple(context.slots.items())
        token = None
        committed = False

        def begin():
            nonlocal token
            token = context.begin_project_open()

        def release():
            nonlocal token
            if token is not None:
                context.finish_project_open(token)
                token = None

        def run():
            nonlocal committed
            result = host.project_commands.add_source(
                ProjectSourceRequest(path, FormatId.PLUGIN_SSE),
                host.runtime_context,
                expected_variant_ref=variant_ref,
                expected_project_revision=project_revision,
                expected_variant_revision=variant_revision,
            )
            if not result.is_success or result.value is None:
                diagnostic = result.diagnostics[0]
                raise RuntimeError(f"{diagnostic.code}: {diagnostic.message}")
            committed = True
            hydration = result.value.hydration
            if hydration is None:
                raise RuntimeError("工程来源缺少界面读取信息。")
            snapshot = host.app_runtime.use_cases.resolve("project_projection").snapshot()
            if snapshot is None or (
                snapshot.values.get("project_id"),
                snapshot.values.get("variant_id"),
                snapshot.values.get("project_revision"),
                snapshot.values.get("variant_revision"),
            ) != (project_id, variant_id, result.value.project_revision, result.value.variant_revision):
                raise RuntimeError("添加期间工程版本已发生变化。")
            slots = (*old_slots, *prepare_project_slots((hydration,), snapshot))
            required = {EntryKey.from_dict(entry["entry_key"]) for entry in snapshot.values.get("entries", ())}
            visible = {entry.identity for _, slot in slots for entry in slot.collection}
            if required != visible:
                raise RuntimeError("添加后的工程来源未能完整加载。")
            return AddedSourceView(snapshot, slots, prepare_project_view(snapshot))

        def failed(message):
            if committed:
                context.project_view_failed = True
            release()
            if committed:
                message = f"插件已加入工程，但工作台加载失败：{message} 请通过重新打开工程的保存确认流程恢复工作台。"
            else:
                message = f"添加插件失败：{message}"
            host.show_message(message, error=True)

        def received(bundle):
            try:
                if not context.install_project_open(token, bundle.snapshot, bundle.slots, state=bundle.state):
                    raise RuntimeError("工程内容在添加期间已发生变化。")
                release()
                if context.project_view_failed:
                    raise RuntimeError("工程内容在视图安装期间已发生变化。")
            except Exception as exc:
                failed(str(exc))
                return
            host.show_workbench()
            host.show_message("插件已加入当前项目。")

        try:
            accepted = host.start_foreground_task(
                run, message="正在将插件加入当前项目…", before_start=begin, on_result=received, on_error=failed
            )
        except Exception as exc:
            failed(str(exc))
            return False
        if not accepted:
            release()
        return accepted

# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Append an authoritative source and install the complete workbench together."""

from __future__ import annotations

import logging

from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectSourceRequest
from transbridge.persistence.v2 import ProjectId, VariantId, VariantRef
from transbridge.ui.source_hydration import slot_from_hydration

_logger = logging.getLogger(__name__)


class ProjectSourceAddOperation:
    def __init__(self, host):
        self._host = host

    def start(self, path: str, expected_identity: tuple[str, str]) -> bool:
        host = self._host
        context = host.context
        if context.active_version_identity != expected_identity:
            host.show_message("目标工程版本已变化，请重新拖入插件。", error=True)
            return False
        if context.project_opening or context.project_view_failed:
            host.show_message("工作台尚未就绪，请恢复后再添加插件。", error=True)
            return False
        if context.authoritative_projection_diverged():
            host.show_message("当前工作台有尚未提交的修改，请先保存或处理编辑错误后再添加插件。", error=True)
            return False
        project_id, variant_id = expected_identity
        variant_ref = VariantRef(VariantId(variant_id), ProjectId(project_id))
        project_revision, variant_revision = context.project_revision, context.variant_revision
        token = None
        committed = None

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
            committed = result.value
            return slot_from_hydration(committed.hydration)

        def failed(message, *, slot=None):
            release()
            if committed is None:
                host.show_message(f"添加插件失败：{message}", error=True)
                return
            if context.active_version_identity == expected_identity:
                context.project_view_failed = True
            host.show_message(
                f"插件已加入工程，但工作台加载失败：{message}",
                error=True,
                retry=lambda: received(slot),
                retry_label="重试刷新工作台",
            )

        def received(slot):
            if context.active_version_identity != expected_identity:
                release()
                return
            try:
                if slot is None:
                    slot = slot_from_hydration(committed.hydration)
                if not context.install_working_sources(
                    expected_identity, additions=((committed.source_id, slot),), view_token=token
                ):
                    raise RuntimeError("工程内容在添加期间已发生变化。")
                release()
                if context.project_view_failed:
                    raise RuntimeError("工程内容在视图安装期间已发生变化。")
            except Exception as exc:
                _logger.exception("Could not install an added source in the workbench")
                failed(str(exc), slot=slot)
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

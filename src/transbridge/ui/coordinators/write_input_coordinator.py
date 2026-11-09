# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Collect a write plan's plugin files without changing the project view."""

from dataclasses import replace
from uuid import uuid4

from PyQt6.QtWidgets import QFileDialog

from transbridge.application.io.contracts import FormatId
from transbridge.application.io.write_formats import SST_FORMATS
from transbridge.application.projects.write_inputs import bind_plugin_write_input
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.ui.project_rows import ProjectRowsCollection


def unbound_plugin_slots(scope):
    return tuple(
        (key, slot)
        for key, slot in scope.slots.items()
        if getattr(slot, "format_id", None) is FormatId.PLUGIN_SSE
        and getattr(slot.source_snapshot, "content", None) is None
    )


def requires_write_inputs(scope, output_format=None):
    return (output_format not in SST_FORMATS and bool(unbound_plugin_slots(scope))) or any(
        isinstance(slot.collection, ProjectRowsCollection) for slot in scope.slots.values()
    )


def select_write_inputs(host, scope, resume, *, output_format=None, select_plugins=True, on_unavailable=None) -> None:
    identity = host.context.active_version_identity
    variant_revision = host.context.variant_revision
    project_revision = host.context.project_revision
    unavailable = on_unavailable or (lambda message: host.show_message(message, error=True) if message else None)
    selections = {}
    required = unbound_plugin_slots(scope) if select_plugins and output_format not in SST_FORMATS else ()
    for key, slot in required:
        path, _ = QFileDialog.getOpenFileName(
            host, f"为本次写回选择插件 · {slot.label}", "", "插件 (*.esp *.esm *.esl)"
        )
        if not path:
            unavailable(None)
            return
        selections[key] = path
    if (
        host.context.active_version_identity != identity
        or host.context.variant_revision != variant_revision
        or host.context.project_revision != project_revision
    ):
        unavailable("工程版本已变化，请重新发起写回。")
        return
    request = replace(
        host.runtime_context,
        project_id=identity[0],
        variant_id=identity[1],
        run_id=f"plugin-write-input-{uuid4().hex}",
    )

    def prepare():
        preparer = TranslationIoProjectSourcePreparer()
        snapshots = {}
        for key, slot in scope.slots.items():
            snapshot = slot.source_snapshot
            if isinstance(slot.collection, ProjectRowsCollection):
                snapshot = slot.collection.native_template()
            if key in selections:
                snapshot = bind_plugin_write_input(
                    snapshot, (entry.snapshot() for entry in slot.collection), selections[key], request, preparer
                )
            snapshots[key] = snapshot
        return snapshots

    def ready(snapshots):
        if (
            host.context.active_version_identity != identity
            or host.context.variant_revision != variant_revision
            or host.context.project_revision != project_revision
        ):
            unavailable("工程版本已变化，请重新发起写回。")
            return
        if getattr(host, "close_pending", False):
            unavailable(None)
            return
        resume(replace(scope, snapshots=snapshots))

    message = "正在验证本次选择的插件和 Strings…" if selections else "正在准备本次写出所需的格式数据…"
    if not host.start_foreground_task(prepare, message=message, on_result=ready, on_error=unavailable):
        unavailable("另一项后台操作仍在进行，请稍候。")

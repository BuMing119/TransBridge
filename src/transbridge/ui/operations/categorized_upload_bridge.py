# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compose ordinary/category file uploads from authoritative project sources."""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from transbridge.paratranz.project_upload import CategorizedUploadSession

from .categorized_upload_dialog import CategorizedUploadDialog


def open_categorized_upload(context, parent=None, *, batch: bool = False):
    return open_file_upload(context, parent, batch=batch, categorized=True)


def open_file_upload(context, parent=None, *, batch=False, categorized=False, **values):
    try:
        if values:
            from .download_context import DownloadContext

            context = DownloadContext(context, values)
        namespace = None
        if not batch:
            namespaces = {entry.identity.namespace for entry in context.collection or ()}
            if len(namespaces) != 1:
                raise ValueError("单独分类上传需要当前插件的唯一来源，请先选择一个插件。")
            namespace = namespaces.pop()
        session = CategorizedUploadSession(context, source_namespace=namespace, categorized=categorized)
    except (ValueError, RuntimeError, AttributeError) as exc:
        QMessageBox.warning(parent, "无法上传", str(exc))
        return None
    dialog = CategorizedUploadDialog(
        context.project_name or "当前工程",
        str(context.active_variant or "当前版本"),
        session.target.project_name or str(session.target.project_id),
        load_preview=session.load_preview,
        upload=session.upload,
        retry_upload=session.retry_upload,
        batch=batch,
        categorized=categorized,
        parent=parent,
    )
    dialog.show()
    return dialog

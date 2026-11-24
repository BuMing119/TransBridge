# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compose ordinary/category file uploads from authoritative project sources."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QMessageBox

from transbridge.paratranz.project_upload import CategorizedUploadSession

from .categorized_upload_dialog import CategorizedUploadDialog
from .upload_mode_dialog import UploadModeDialog


def open_categorized_upload(context, parent=None, *, batch: bool = False):
    return open_file_upload(context, parent, batch=batch, categorized=True)


def open_file_upload(context, parent=None, *, batch=False, categorized: bool | None = None, **values):
    if batch is None and categorized is None:
        choice = UploadModeDialog(parent)
        try:
            if choice.exec() != QDialog.DialogCode.Accepted:
                return None
            categorized = choice.categorized
        finally:
            choice.deleteLater()
    categorized = bool(categorized)

    try:
        if values:
            from .download_context import DownloadContext

            context = DownloadContext(context, values)
        namespace = None
        if batch is False:
            namespaces = {entry.identity.namespace for entry in context.collection or ()}
            if len(namespaces) != 1:
                raise ValueError("单独分类上传需要当前插件的唯一来源，请先选择一个插件。")
            namespace = namespaces.pop()
        if batch is None:
            session = CategorizedUploadSession(context, categorized=categorized, select_sources=True)
        else:
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
        load_catalog=session.load_source_catalog if batch is None else None,
        batch=batch,
        categorized=categorized,
        parent=parent,
    )
    dialog.show()
    return dialog

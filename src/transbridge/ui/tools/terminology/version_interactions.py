"""Review and confirmation flows for publication, draft discard, and history restore."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QMessageBox

from .change_review_dialog import ChangeReviewDialog


class VersionInteractionController:
    """Keep reads off the GUI thread and submit exactly the object the user reviewed."""

    def __init__(self, parent, presenter, run_read, run_job, refresh) -> None:
        self._parent = parent
        self._presenter = presenter
        self._run_read = run_read
        self._run_job = run_job
        self._refresh = refresh

    def publish(self) -> None:
        self._run_read(self._presenter.preview_publish, self._review_publish, "正在准备发布前的完整术语变化…")

    def _review_publish(self, preview) -> None:
        if self._presenter.closed:
            return
        dialog = ChangeReviewDialog(preview.diff, self._parent)
        if dialog.exec() == QDialog.DialogCode.Accepted and not self._presenter.closed:
            self._run_job(lambda: self._presenter.publish_preview(preview), "正在发布已审阅的术语内容…")

    def discard(self) -> None:
        self._run_read(self._presenter.active_draft, self._confirm_discard, "正在读取当前草稿…")

    def _confirm_discard(self, draft) -> None:
        if self._presenter.closed:
            return
        if draft is None:
            self._refresh()
            return
        if (
            not _confirm(
                self._parent,
                "放弃当前草稿",
                _discard_impact(draft) + "\n已发布版本和历史会保留。此操作无法撤销。",
                "放弃草稿",
            )
            or self._presenter.closed
        ):
            return
        self._run_read(
            lambda: self._presenter.discard_draft(draft.ref),
            lambda _result: self._refresh(),
            "正在放弃已确认的草稿…",
        )

    def restore(self, version_ref) -> None:
        self._run_read(
            self._presenter.active_draft,
            lambda draft: self._confirm_restore(version_ref, draft),
            "正在检查恢复前的草稿状态…",
        )

    def _confirm_restore(self, version_ref, draft) -> None:
        if self._presenter.closed:
            return
        version_id = getattr(version_ref, "version_id", str(version_ref))
        message = f"将以历史版本 {version_id} 的内容创建新版；当前版本和中间历史会保留。"
        if draft is not None:
            message += "\n\n" + _discard_impact(draft) + "\n需要先放弃此草稿才能恢复，放弃操作无法撤销。"
        if (
            not _confirm(
                self._parent,
                "恢复历史内容",
                message,
                "放弃草稿并恢复" if draft is not None else "创建新版",
            )
            or self._presenter.closed
        ):
            return
        if draft is None:
            self._restore(version_ref)
        else:
            self._run_read(
                lambda: self._presenter.discard_draft(draft.ref),
                lambda _result: self._restore(version_ref, refresh=True),
                "正在放弃已确认的草稿…",
            )

    def _restore(self, version_ref, *, refresh: bool = False) -> None:
        if self._presenter.closed:
            return
        if refresh:
            self._refresh()
        self._run_job(lambda: self._presenter.restore(version_ref), "历史内容恢复任务已开始。")


def _discard_impact(draft) -> str:
    return (
        f"将放弃草稿中的 {len(draft.decisions)} 条术语决定、{len(draft.actions)} 条人工调整记录"
        f"和 {len(draft.conflict_resolutions)} 项冲突处理。"
    )


def _confirm(parent, title: str, message: str, accept_text: str) -> bool:
    dialog = QMessageBox(parent)
    dialog.setWindowTitle(title)
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(message)
    accept = dialog.addButton(accept_text, QMessageBox.ButtonRole.AcceptRole)
    cancel = dialog.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    dialog.setDefaultButton(cancel)
    dialog.exec()
    return dialog.clickedButton() is accept


__all__ = ["VersionInteractionController"]

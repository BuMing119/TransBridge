from PyQt6.QtWidgets import QDialog, QMessageBox

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.operations.upload_mode_dialog import UploadModeDialog

from .base import OpCard
from .presenter import OperationCardPresenter


class UploadCard(OpCard):
    def __init__(self, ctx, run_worker, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(
            "上传到 ParaTranz",
            "单独或批量上传到 ParaTranz，均可选择普通上传或分类上传。",
            "上传",
            parent,
            theme_view=theme_view,
        )
        self._ctx = ctx
        self._presenter = OperationCardPresenter(ctx)
        self.btn.clicked.connect(self.upload)
        self.batch_btn.clicked.connect(self.batch_upload)

    def _choose_upload_mode(self, *, batch: bool = False) -> bool:
        """Return whether the caller should continue with ordinary upload."""
        from transbridge.ui.operations.categorized_upload_bridge import open_categorized_upload

        dialog = UploadModeDialog(batch=batch, parent=self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            if dialog.mode == "plain":
                return True
            open_categorized_upload(self._ctx, parent=self, batch=batch)
            return False
        finally:
            dialog.deleteLater()

    def update_batch_visibility(self):
        self.set_batch_visible(self._presenter.batch_available)

    def batch_upload(self):
        if len(self._ctx.slots) > 1 and self._choose_upload_mode(batch=True):
            self._launch_upload(batch=True)

    def upload(self):
        if self._ctx.collection and self._choose_upload_mode():
            self._launch_upload()

    def _launch_upload(self, *, batch=False):
        if not self._dispatch_planned("upload", self._ctx, batch=batch):
            QMessageBox.warning(self, "上传入口不可用", "当前文件上传入口不可用，请重新打开工作台后重试。")

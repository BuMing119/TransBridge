"""Download card dispatches exclusively through the shared operation plan."""

from PyQt6.QtWidgets import QMessageBox

from transbridge.ui.foundation.adapters import ThemeView

from .base import OpCard


class DownloadCard(OpCard):
    def __init__(self, ctx, run_worker, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(
            "从paratranz上同步",
            "选择工程中的插件，对照云端译文后更新并保存当前本地版本。",
            "从paratranz上同步",
            parent,
            theme_view=theme_view,
        )
        self._ctx = ctx
        self.btn.clicked.connect(self.download)
        self.batch_btn.clicked.connect(self.batch_download)

    def update_batch_visibility(self):
        """The unified download window owns the source selection."""
        self.set_batch_visible(False)

    def batch_download(self):
        """Compatibility alias; current and multiple sources share one window."""
        return self.download()

    def download(self):
        if self._dispatch_planned("download", self._ctx):
            return
        QMessageBox.warning(
            self,
            "下载入口不可用",
            "下载必须通过 ParaTranz 同步计划执行；当前计划入口不可用，本次未修改本地内容。",
        )

# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
from PyQt6.QtWidgets import QMessageBox

from transbridge.ui.foundation.adapters import ThemeView

from .base import OpCard


class UploadCard(OpCard):
    def __init__(self, ctx, run_worker, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(
            "上传到 ParaTranz",
            "选择插件和文件，普通或分类上传到 ParaTranz。",
            "上传…",
            parent,
            theme_view=theme_view,
        )
        self._ctx = ctx
        self.btn.clicked.connect(self.upload)

    def upload(self):
        if (self._ctx.collection or self._ctx.slots) and not self._dispatch_planned("upload", self._ctx, batch=None):
            QMessageBox.warning(self, "上传入口不可用", "当前文件上传入口不可用，请重新打开工作台后重试。")

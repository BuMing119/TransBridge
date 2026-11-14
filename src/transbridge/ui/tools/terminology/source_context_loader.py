# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Load complete scheme mappings only while their advanced view is visible."""

from PyQt6.QtCore import QEvent, QObject, QTimer

from transbridge.application.terminology.browse_queries import TermBrowseQuery

from .async_actions import AsyncActionRunner


class SourceContextLoader(QObject):
    def __init__(self, browser, browsing, context, *, thread_pool=None):
        super().__init__(browser)
        self.browser, self.browsing, self.context = browser, browsing, context
        self._runner = AsyncActionRunner(self, on_error=self._failed, thread_pool=thread_pool)
        self._dirty = True
        self._closed = False
        self._token = None
        browser.installEventFilter(self)
        browser.context_loader = self

    def invalidate(self):
        if self._closed:
            return
        self._dirty = True
        if self._token is not None:
            self._token.cancel()
            self._token = None
        self.browser.edit_button.setEnabled(False)
        self.browser.table.setEnabled(False)
        self.browser.status.setText("打开方案映射后读取最新内容。")
        if self.browser.isVisible():
            QTimer.singleShot(0, self._load)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self._load)
        elif event.type() == QEvent.Type.Hide:
            self.invalidate()
        return super().eventFilter(watched, event)

    def _load(self):
        if self._closed or not self._dirty or self._token is not None or not self.browser.isVisible():
            return
        if self.context.project_id is None or self.context.variant_id is None:
            return
        self.browser.status.setText("正在读取方案映射…")
        query = TermBrowseQuery(self.context.project_id, self.context.variant_id)
        self._token = self._runner.run_read(
            lambda token: self.browsing.describe(query, cancellation=token), self._ready, key="source-context"
        )

    def _ready(self, context):
        self._token = None
        self._dirty = False
        self.browser.table.setEnabled(True)
        self.browser.render_context(context)

    def _failed(self, error):
        self._token = None
        self.browser.status.setText(f"读取方案映射失败：{error}。重新打开可重试。")

    def close(self):
        self._closed = True
        self._runner.close()

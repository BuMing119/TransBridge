# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""One on-demand alignment choice per launch across all app windows."""

from weakref import ref

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox

_APP_ATTRIBUTE = "_transbridge_alignment_consent"


class AlignmentConsent(QObject):
    """Keep this launch's choice in memory and persist only an explicit remembered answer."""

    changed = pyqtSignal()

    def __init__(self, parent=None, *, settings=None):
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings("TransBridge", "TerminologyAlignment")
        remembered = self._settings.value("remember", False, type=bool)
        self.enabled = bool(remembered and self._settings.value("enabled", False, type=bool))
        self.decided = bool(remembered)
        self._asked = bool(remembered)
        self._dialog = None
        self._waiting = []

    def request(self, parent):
        """Ask on a real model miss, once per QApplication, when the owner is visible."""
        if sip.isdeleted(self) or self.decided or self._asked or sip.isdeleted(parent):
            return
        if not parent.isVisible():
            if not any(item() is parent for item in self._waiting):
                self._waiting.append(ref(parent))
                parent.installEventFilter(self)
            return
        self._show(parent)

    def configure(self, parent):
        """Allow an explicit preferences action to change a remembered or session choice."""
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        if not sip.isdeleted(parent):
            self._show(parent)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Show:
            watched.removeEventFilter(self)
            owner = ref(watched)

            def shown():
                window = owner()
                if window is not None and not sip.isdeleted(window) and window.isVisible():
                    self.request(window)

            QTimer.singleShot(0, shown)
        return False

    def _show(self, parent):
        self._asked = True
        for item in self._waiting:
            window = item()
            if window is not None and not sip.isdeleted(window):
                window.removeEventFilter(self)
        self._waiting.clear()
        dialog = QMessageBox(parent)
        dialog.setWindowTitle("AI 术语位置判断")
        dialog.setIcon(QMessageBox.Icon.Question)
        dialog.setText("是否启用 AI 术语位置判断？")
        dialog.setInformativeText(
            "启用后，方案预览会自动将计数或匹配关系存在疑点的原文与译文交给当前 AI 服务处理。"
            "规则可处理的条目仍在本地完成；此操作可能产生模型服务费用。"
        )
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        dialog.button(QMessageBox.StandardButton.Yes).setText("启用")
        dialog.button(QMessageBox.StandardButton.No).setText("不启用")
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        remember = QCheckBox("永不再问", dialog)
        remember.setChecked(self._settings.value("remember", False, type=bool))
        dialog.setCheckBox(remember)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._dialog = dialog
        dialog.finished.connect(self._finish)
        dialog.destroyed.connect(self._dismiss)
        dialog.open()

    def _finish(self, _result):
        dialog = self._dialog
        if dialog is None:
            return
        button = dialog.clickedButton()
        answer = QMessageBox.StandardButton.NoButton if button is None else dialog.standardButton(button)
        explicit = answer in (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No)
        self.enabled = explicit and answer == QMessageBox.StandardButton.Yes
        self.decided = True
        if explicit:
            remember = dialog.checkBox().isChecked()
            if remember or self._settings.value("remember", False, type=bool):
                self._settings.setValue("remember", remember)
                if remember:
                    self._settings.setValue("enabled", self.enabled)
                else:
                    self._settings.remove("enabled")
                self._settings.sync()
        dialog.destroyed.disconnect(self._dismiss)
        self._dialog = None
        dialog.deleteLater()
        self.changed.emit()

    def _dismiss(self):
        if self._dialog is not None:
            self._dialog = None
            self.enabled = False
            self.decided = True
            self.changed.emit()


def get_alignment_consent():
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("AI 术语位置判断需要已启动的 Qt 应用。")
    service = getattr(app, _APP_ATTRIBUTE, None)
    if service is None or sip.isdeleted(service):
        service = AlignmentConsent(app)
        setattr(app, _APP_ATTRIBUTE, service)
    return service

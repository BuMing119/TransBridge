# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Release details and download presentation, without network or installer ownership."""

from enum import StrEnum

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QTextDocument, QTextOption
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from transbridge.application.update_service import ReleaseInfo
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, configure_dialog, make_primary_button


class UpdateState(StrEnum):
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    ERROR = "error"
    READY = "ready"
    INSTALLING = "installing"


def _size_text(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.1f} KB"


class _ReleaseNotesBrowser(QTextBrowser):
    """Render release Markdown without fetching embedded images or local files."""

    def loadResource(self, resource_type: int, name: QUrl):  # noqa: N802 - Qt override
        return None


class UpdateDialog(QDialog):
    download_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    install_requested = pyqtSignal()

    def __init__(self, release: ReleaseInfo, current_version: str, parent=None, *, can_install: bool = True) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setProperty("tbTaskDialog", True)
        self.setWindowTitle("TransBridge 软件更新")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumSize(520, 500)
        self.resize(640, 660)
        self.release = release
        self.state = UpdateState.AVAILABLE
        self._transfer_active = False
        self._can_install = can_install
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(self._label("TRANSBRIDGE · 软件更新", "tbTaskMeta"))
        version_row = QHBoxLayout()
        version_column = QVBoxLayout()
        version_column.setSpacing(4)
        version_column.addWidget(self._label("最新版本", "tbTaskSubtitle"))
        self.version_label = self._label(f"v{release.version}")
        self.version_label.setObjectName("tbUpdateVersion")
        self.version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.version_label.setAccessibleName(f"最新版本 {release.version}")
        version_column.addWidget(self.version_label)
        self.current_label = self._label(f"当前版本 v{current_version}", "tbTaskMeta")
        version_column.addWidget(self.current_label)
        version_row.addLayout(version_column, 1)
        badge = self._label("有可用更新")
        badge.setObjectName("tbUpdateBadge")
        version_row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(version_row)
        notes_heading = QHBoxLayout()
        notes_heading.addWidget(self._label("更新日志", "tbTaskSectionTitle"))
        notes_heading.addStretch(1)
        self.release_link = QPushButton("查看发布页面 ↗", self)
        self.release_link.setFlat(True)
        self.release_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.release_link.clicked.connect(lambda: self._open_web_link(QUrl(release.html_url)))
        notes_heading.addWidget(self.release_link)
        layout.addLayout(notes_heading)
        self.notes = _ReleaseNotesBrowser(self)
        self.notes.setObjectName("tbUpdateNotes")
        self.notes.setAccessibleName("此版本的完整更新日志")
        self.notes.setOpenLinks(False)
        self.notes.setOpenExternalLinks(False)
        self.notes.anchorClicked.connect(self._open_web_link)
        self.notes.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.notes.document().setDocumentMargin(16)
        self.notes.document().setMarkdown(
            release.notes.strip() or "本次发布未提供更新说明。",
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML,
        )
        self.notes.setMinimumHeight(140)
        layout.addWidget(self.notes, 1)
        status_panel = QFrame(self)
        status_panel.setProperty("tbTaskSurface", True)
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(16, 12, 16, 12)
        status_layout.setSpacing(8)
        self.status_title = self._label("准备下载", "tbTaskSectionTitle")
        self.status_detail = self._label(f"Windows 安装包 · {_size_text(release.size)}", "tbTaskHint")
        self.progress = QProgressBar(self)
        ComponentStyle.apply_static(self.progress, ComponentKind.PROGRESS)
        self.progress.setAccessibleName("更新包下载进度")
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.hide()
        status_layout.addWidget(self.status_title)
        status_layout.addWidget(self.status_detail)
        status_layout.addWidget(self.progress)
        layout.addWidget(status_panel)
        self.install_hint = self._label(
            "点击更新后，下载完成将自动保存工作、安装并重新打开软件。Windows 可能要求管理员权限。", "tbTaskHint"
        )
        layout.addWidget(self.install_hint)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.secondary_button = QPushButton("稍后", self)
        ComponentStyle.apply_static(self.secondary_button, ComponentKind.BUTTON)
        self.secondary_button.clicked.connect(self._secondary_action)
        self.primary_button = make_primary_button("更新并重启", self)
        self.primary_button.clicked.connect(self._primary_action)
        self.primary_button.setDefault(True)
        actions.addWidget(self.secondary_button)
        actions.addWidget(self.primary_button)
        layout.addLayout(actions)
        if not can_install:
            self.install_hint.setText("当前从源码运行，请使用安装版执行自动更新，或查看发布页面。")
        self._sync_actions()

    def _label(self, text: str, role: str | None = None) -> QLabel:
        label = QLabel(text, self)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        if role is not None:
            label.setProperty(role, True)
        return label

    @staticmethod
    def _open_web_link(url: QUrl) -> None:
        if url.scheme().lower() in {"https", "http"} and url.host():
            QDesktopServices.openUrl(url)

    def _sync_actions(self) -> None:
        self.primary_button.setEnabled(
            self._can_install
            and not self._transfer_active
            and self.state in {UpdateState.AVAILABLE, UpdateState.CANCELLED, UpdateState.ERROR, UpdateState.READY}
        )
        self.secondary_button.setEnabled(self.state not in {UpdateState.CANCELLING, UpdateState.INSTALLING})
        self.secondary_button.setText("取消下载" if self._transfer_active else "稍后")

    def set_transfer_active(self, active: bool) -> None:
        self._transfer_active = active
        self._sync_actions()

    def _set_state(self, state: UpdateState, title: str, detail: str, action: str) -> None:
        self.state = state
        self.status_title.setText(title)
        self.status_detail.setText(detail)
        self.primary_button.setText(action)
        self.primary_button.setAccessibleName(action)
        self._sync_actions()

    def show_downloading(self) -> None:
        self.progress.show()
        self.progress.setRange(0, 0)
        self.install_hint.setText("下载完成将自动保存工作、安装并重新打开软件。下载期间可继续使用主界面。")
        self._set_state(UpdateState.DOWNLOADING, "正在连接下载…", "正在获取安装包，请稍候。", "正在下载…")

    def set_download_progress(self, done: int, total: int) -> None:
        if self.state != UpdateState.DOWNLOADING:
            return
        if total > 0:
            self.progress.setRange(0, 1000)
            self.progress.setValue(max(0, min(1000, done * 1000 // total)))
            self.status_title.setText("正在校验安装包…" if done >= total else "正在下载更新")
            self.status_detail.setText(f"{self.progress.value() / 10:g}% · {_size_text(done)} / {_size_text(total)}")

    def show_cancelled(self) -> None:
        self.progress.hide()
        self._set_state(UpdateState.CANCELLED, "下载已取消", "可以稍后重新更新此版本。", "重试更新")

    def show_error(self, message: str) -> None:
        self.progress.hide()
        self._set_state(UpdateState.ERROR, "下载未完成", message, "重试更新")

    def show_ready(self, message: str = "安装包已下载并通过校验。") -> None:
        self.progress.hide()
        self.install_hint.setText("将保存工作并退出，自动安装后重新打开软件。Windows 可能要求管理员权限。")
        self._set_state(UpdateState.READY, "可以安装了", message, "保存并重启")

    def show_installing(self) -> None:
        self._set_state(
            UpdateState.INSTALLING, "正在保存并退出…", "关闭完成后自动安装，成功后重新打开软件。", "正在退出…"
        )

    def _primary_action(self) -> None:
        if not self.primary_button.isEnabled():
            return
        if self.state == UpdateState.READY:
            self.install_requested.emit()
        else:
            self.download_requested.emit()

    def _request_cancel(self) -> None:
        if self._transfer_active and self.state != UpdateState.CANCELLING:
            self._set_state(UpdateState.CANCELLING, "正在取消下载…", "正在结束下载并清理临时文件。", "正在取消…")
            self.cancel_requested.emit()

    def _secondary_action(self) -> None:
        if self._transfer_active:
            self._request_cancel()
        else:
            self.reject()

    def reject(self) -> None:
        if self.state == UpdateState.INSTALLING:
            return
        self._request_cancel()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.state == UpdateState.INSTALLING:
            event.ignore()
            return
        self._request_cancel()
        super().closeEvent(event)

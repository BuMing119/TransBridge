"""Start-center state and the thin Qt view that renders it.

The immutable state is intentionally independent from repository and task
implementations.  The widget only emits stable user intents; callers own file
dialogs, application commands, and navigation side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class StartDestinationState(StrEnum):
    RESTORING_LAST = "restoring-last"
    START_CENTER_EMPTY = "start-center-empty"
    START_CENTER_RECOVERY_FAILED = "start-center-recovery-failed"
    START_CENTER_USER_REQUESTED = "start-center-user-requested"


@dataclass(frozen=True, slots=True)
class RecentProjectViewState:
    project_key: str
    name: str
    path: str
    available: bool
    reason: str = ""
    active: bool = False

    def __post_init__(self) -> None:
        if not self.project_key.strip() or not self.name.strip():
            raise ValueError("recent project identity and name must not be empty")
        if not self.available and not self.reason.strip():
            raise ValueError("an unavailable recent project requires a reason")


@dataclass(frozen=True, slots=True)
class RecoveryItemViewState:
    storage_key: str
    title: str
    recoverable: bool
    reason: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.storage_key.strip() or not self.title.strip():
            raise ValueError("recovery identity and title must not be empty")
        if not self.recoverable and not self.reason.strip():
            raise ValueError("an unavailable recovery item requires a reason")


@dataclass(frozen=True, slots=True)
class StartCenterViewState:
    destination: StartDestinationState
    revision: int
    recent_projects: tuple[RecentProjectViewState, ...] = ()
    recovery_items: tuple[RecoveryItemViewState, ...] = ()
    active_project_name: str | None = None
    dirty: bool = False
    diagnostic_code: str = ""
    diagnostic_message: str = ""

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("start-center revision must not be negative")
        if self.destination is StartDestinationState.START_CENTER_RECOVERY_FAILED:
            if not self.diagnostic_code.strip() or not self.diagnostic_message.strip():
                raise ValueError("restore failure requires a stable diagnostic")


class StartCenterWidget(QWidget):
    """Render start/draft projections without accessing disk or services."""

    choose_plugin_requested = pyqtSignal()
    open_project_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)
    recovery_details_requested = pyqtSignal(str)
    create_empty_requested = pyqtSignal()
    open_fomod_requested = pyqtSignal()
    return_to_current_requested = pyqtSignal()
    project_name_changed = pyqtSignal(str)
    variant_name_changed = pyqtSignal(str)
    skip_empty_changed = pyqtSignal(bool)
    choose_migration_requested = pyqtSignal()
    prepare_requested = pyqtSignal()
    commit_requested = pyqtSignal()
    return_to_landing_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAccessibleName("开始中心")
        self._state_revision = -1
        self._draft_revision = -1
        self._pages = QStackedWidget(self)
        self._landing_page = self._build_landing_page()
        self._draft_page = self._build_draft_page()
        self._pages.addWidget(self._landing_page)
        self._pages.addWidget(self._draft_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.addWidget(self._pages)

    def _build_landing_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        title = QLabel("开始翻译", page)
        title.setObjectName("startCenterTitle")
        title.setStyleSheet("font-size: 24px; font-weight: 600")
        title.setAccessibleName("开始翻译")
        layout.addWidget(title)
        layout.addWidget(QLabel("选择一个 ESP / ESM / ESL，工程名称和常用设置会自动准备。", page))

        self.choose_plugin_button = QPushButton("选择插件开始翻译", page)
        self.choose_plugin_button.setObjectName("startCenterPrimaryAction")
        self.choose_plugin_button.setAccessibleName("选择插件开始翻译")
        self.choose_plugin_button.clicked.connect(self.choose_plugin_requested)
        layout.addWidget(self.choose_plugin_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._status_label = QLabel(page)
        self._status_label.setWordWrap(True)
        self._status_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._status_label.setAccessibleName("开始中心状态")
        layout.addWidget(self._status_label)

        self._return_button = QPushButton("返回当前工程", page)
        self._return_button.setAccessibleName("返回当前本地翻译工程")
        self._return_button.clicked.connect(self.return_to_current_requested)
        layout.addWidget(self._return_button)

        layout.addWidget(QLabel("继续工作", page))
        self._recent_list = QListWidget(page)
        self._recent_list.setAccessibleName("最近本地翻译工程")
        self._recent_list.itemActivated.connect(
            lambda item: self.open_recent_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))
        )
        layout.addWidget(self._recent_list)

        layout.addWidget(QLabel("可恢复任务", page))
        self._recovery_list = QListWidget(page)
        self._recovery_list.setAccessibleName("可恢复任务")
        self._recovery_list.itemActivated.connect(
            lambda item: self.recovery_details_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))
        )
        layout.addWidget(self._recovery_list)

        secondary = QHBoxLayout()
        self._open_button = QPushButton("打开本地工程", page)
        self._open_button.setAccessibleName("打开已有本地翻译工程")
        self._open_button.clicked.connect(self.open_project_requested)
        self._empty_button = QPushButton("创建空工程", page)
        self._empty_button.setAccessibleName("创建空的本地翻译工程")
        self._empty_button.clicked.connect(self.create_empty_requested)
        self._fomod_button = QPushButton("FOMOD 安装包翻译", page)
        self._fomod_button.setAccessibleName("打开 FOMOD 安装包翻译")
        self._fomod_button.clicked.connect(self.open_fomod_requested)
        secondary.addWidget(self._open_button)
        secondary.addWidget(self._empty_button)
        secondary.addWidget(self._fomod_button)
        secondary.addStretch(1)
        layout.addLayout(secondary)
        return page

    def _build_draft_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        title = QLabel("创建本地翻译工程", page)
        title.setStyleSheet("font-size: 22px; font-weight: 600")
        layout.addWidget(title)
        form = QFormLayout()
        self._source_label = QLabel(page)
        self._name_edit = QLineEdit(page)
        self._name_edit.setAccessibleName("本地翻译工程名称")
        self._name_edit.textEdited.connect(self.project_name_changed)
        self._name_edit.returnPressed.connect(self._activate_draft_primary)
        self._variant_edit = QLineEdit(page)
        self._variant_edit.setAccessibleName("默认翻译版本名称")
        self._variant_edit.textEdited.connect(self.variant_name_changed)
        self._variant_edit.returnPressed.connect(self._activate_draft_primary)
        self._migration_label = QLabel("不导入", page)
        self._migration_label.setAccessibleName("已有译文来源")
        self._migration_button = QPushButton("选择已有译文", page)
        self._migration_button.setAccessibleName("选择已有译文来源")
        self._migration_button.clicked.connect(self.choose_migration_requested)
        migration_row = QHBoxLayout()
        migration_row.addWidget(self._migration_label, 1)
        migration_row.addWidget(self._migration_button)
        form.addRow("插件", self._source_label)
        form.addRow("工程名称", self._name_edit)
        form.addRow("默认翻译版本", self._variant_edit)
        form.addRow("已有译文", migration_row)
        layout.addLayout(form)

        advanced = QGroupBox("高级解析设置", page)
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QVBoxLayout(advanced)
        self._skip_empty = QCheckBox("忽略空源文本", advanced)
        self._skip_empty.setAccessibleName("忽略空的源文本")
        self._skip_empty.toggled.connect(self.skip_empty_changed)
        advanced_layout.addWidget(self._skip_empty)
        layout.addWidget(advanced)

        self._draft_summary = QLabel(page)
        self._draft_summary.setWordWrap(True)
        self._draft_summary.setAccessibleName("工程创建方案摘要")
        layout.addWidget(self._draft_summary)
        self._draft_error = QLabel(page)
        self._draft_error.setWordWrap(True)
        self._draft_error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._draft_error.setAccessibleName("工程创建错误")
        layout.addWidget(self._draft_error)

        actions = QHBoxLayout()
        self._draft_back = QPushButton("返回", page)
        self._draft_back.setAccessibleName("返回开始中心")
        self._draft_back.clicked.connect(self.return_to_landing_requested)
        self._draft_primary = QPushButton(page)
        self._draft_primary.setAccessibleName("检查或提交工程创建方案")
        self._draft_primary.clicked.connect(self._emit_draft_primary)
        actions.addWidget(self._draft_back)
        actions.addStretch(1)
        actions.addWidget(self._draft_primary)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def render(self, state: StartCenterViewState) -> None:
        if state.revision < self._state_revision:
            return
        self._state_revision = state.revision
        self._pages.setCurrentWidget(self._landing_page)
        if state.destination is StartDestinationState.RESTORING_LAST:
            status = "正在恢复上次工程…"
        elif state.destination is StartDestinationState.START_CENTER_RECOVERY_FAILED:
            status = f"{state.diagnostic_code}: {state.diagnostic_message}"
        elif state.destination is StartDestinationState.START_CENTER_USER_REQUESTED:
            suffix = "；有未保存修改" if state.dirty else ""
            status = f"工程“{state.active_project_name or '当前工程'}”仍保持打开{suffix}。"
        else:
            status = "尚无可恢复工程，请选择插件开始翻译。"
        self._status_label.setText(status)
        self._status_label.setAccessibleDescription(status)
        self._return_button.setVisible(
            state.destination is StartDestinationState.START_CENTER_USER_REQUESTED
            and state.active_project_name is not None
        )
        self._render_recent(state.recent_projects)
        self._render_recovery(state.recovery_items)
        self.choose_plugin_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def _render_recent(self, recent: tuple[RecentProjectViewState, ...]) -> None:
        self._recent_list.clear()
        for project in recent:
            suffix = "" if project.available else f" — {project.reason}"
            item = QListWidgetItem(f"{project.name}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, project.path)
            item.setFlags(item.flags() if project.available else item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self._recent_list.addItem(item)
        self._recent_list.setVisible(bool(recent))

    def _render_recovery(self, recovery: tuple[RecoveryItemViewState, ...]) -> None:
        self._recovery_list.clear()
        for candidate in recovery:
            suffix = "可继续" if candidate.recoverable else candidate.reason
            item = QListWidgetItem(f"{candidate.title} — {suffix}")
            item.setData(Qt.ItemDataRole.UserRole, candidate.storage_key)
            self._recovery_list.addItem(item)
        self._recovery_list.setVisible(bool(recovery))

    def render_draft(self, state) -> None:
        """Render a GuidedProjectDraftState via its narrow public fields."""

        if state.revision < self._draft_revision:
            return
        self._draft_revision = state.revision
        self._pages.setCurrentWidget(self._draft_page)
        self._source_label.setText(state.source_path or "无源文件（空工程）")
        if self._name_edit.text() != state.project_name:
            self._name_edit.setText(state.project_name)
        if self._variant_edit.text() != state.default_variant_name:
            self._variant_edit.setText(state.default_variant_name)
        migrations = tuple(state.migration_sources)
        self._migration_label.setText("、".join(migrations) if migrations else "不导入")
        options = dict(state.parse_options)
        self._skip_empty.setChecked(bool(options.get("skip_empty", False)))
        self._draft_summary.setText(state.summary)
        diagnostic = ""
        if state.diagnostic_code:
            diagnostic = f"{state.diagnostic_code}: {state.diagnostic_message}"
        self._draft_error.setText(diagnostic)
        self._draft_error.setAccessibleDescription(diagnostic or "当前没有工程创建错误")
        prepared = bool(state.preview_token)
        self._draft_primary.setText("创建并开始翻译" if prepared else "检查创建方案")
        self._draft_primary.setProperty("commitReady", prepared)
        self._draft_primary.setEnabled(state.can_submit and not state.in_flight)
        self._name_edit.setEnabled(not state.in_flight)
        self._variant_edit.setEnabled(not state.in_flight)
        if diagnostic:
            self._draft_error.setFocus(Qt.FocusReason.OtherFocusReason)
        elif self._draft_primary.isEnabled():
            self._draft_primary.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._name_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _emit_draft_primary(self) -> None:
        if bool(self._draft_primary.property("commitReady")):
            self.commit_requested.emit()
        else:
            self.prepare_requested.emit()

    def _activate_draft_primary(self) -> None:
        if self._pages.currentWidget() is self._draft_page and self._draft_primary.isEnabled():
            self._draft_primary.click()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape and self._pages.currentWidget() is self._draft_page:
            self.return_to_landing_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = [
    "RecentProjectViewState",
    "RecoveryItemViewState",
    "StartCenterViewState",
    "StartCenterWidget",
    "StartDestinationState",
]

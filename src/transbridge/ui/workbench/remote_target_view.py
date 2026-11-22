# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Workbench ParaTranz binding view and asynchronous project picker."""

from __future__ import annotations

from copy import copy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.ports.paratranz import ParaTranzProject
from transbridge.application.projects import normalize_paratranz_endpoint
from transbridge.paratranz.project_binding import BindingSelection
from transbridge.paratranz.project_catalog import (
    ParaTranzCatalogCancellation,
    ParaTranzCatalogKey,
    ParaTranzProjectCatalog,
)
from transbridge.paratranz.service import ParaTranzService
from transbridge.ui.foundation.components import (
    ComponentDensity,
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    WrappingLabel,
    reserve_text_width,
)
from transbridge.ui.workers import ApiWorker

from .remote_target_presenter import RemoteTargetPresenter


class ParaTranzTargetDialog(QDialog):
    def __init__(
        self,
        ctx,
        catalog: ParaTranzProjectCatalog,
        config_revision: int,
        parent=None,
        *,
        explanation: str | None = None,
        confirmation_text: str | None = None,
        binding_selection: BindingSelection | None = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._catalog = catalog
        self._config_revision = config_revision
        self._binding_selection = binding_selection
        self.verified_project: ParaTranzProject | None = None
        self._config = copy(ctx.config)
        self._endpoint = normalize_paratranz_endpoint(self._config.base_url)
        self._account_id = None
        if ctx.current_user is not None:
            self._account_id = ctx.current_user.get("id")
        if self._account_id is None:
            self._account_id = getattr(self._config, "user_id", None)
        self._projects = ()
        self._generation = 0
        self._workers: list[ApiWorker] = []
        self._cancellation: ParaTranzCatalogCancellation | None = None
        self._closing = False
        self.setWindowTitle("选择 ParaTranz 同步目标")
        self.resize(520, 460)

        layout = QVBoxLayout(self)
        explanation_label = QLabel(explanation or "选择“我的项目”中的一个项目。此选择只会在确认后绑定到当前本地工程。")
        explanation_label.setWordWrap(True)
        layout.addWidget(explanation_label)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索项目名称或 ID…")
        self._search.textChanged.connect(self._render)
        ComponentStyle.apply_static(self._search, ComponentKind.INPUT)
        layout.addWidget(self._search)
        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self._list, 1)
        self._status = WrappingLabel("正在加载我的项目…", self)
        layout.addWidget(self._status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(confirmation_text or "绑定到当前工程")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setEnabled(False)
        self._list.currentItemChanged.connect(lambda *_: self._ok.setEnabled(self.selected_project() is not None))
        self._load()

    def selected_project(self) -> dict | None:
        item = self._list.currentItem()
        value = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        return None if not isinstance(value, dict) else dict(value)

    @property
    def target_context(self) -> tuple[str, int | None, int]:
        return self._endpoint, self._account_id, self._config_revision

    def accept(self) -> None:
        project = self.selected_project()
        if project is None or not self._list.isEnabled():
            return
        if self._binding_selection is None:
            super().accept()
            return
        self._list.setEnabled(False)
        self._search.setEnabled(False)
        self._ok.setEnabled(False)
        self._status.setText("正在验证当前账号的项目成员身份…")
        self._generation += 1
        generation = self._generation
        cancellation = ParaTranzCatalogCancellation()
        self._cancellation = cancellation
        worker = ApiWorker(
            self._binding_selection.member_project,
            self._catalog,
            project["id"],
            cancellation=cancellation,
            route_http_errors=False,
            cancel_read_on_close=cancellation.cancel,
        )
        worker.result.connect(lambda member: self._verified(generation, member))
        worker.error.connect(lambda error: self._verification_failed(generation, f"无法验证项目成员身份：{error}"))
        self._start_worker(worker)

    def _verified(self, generation: int, member: ParaTranzProject | None) -> None:
        if generation != self._generation:
            return
        if member is None:
            self._verification_failed(generation, "当前 ParaTranz 账号不是所选项目的成员。")
            return
        self.verified_project = member

    def _verification_failed(self, generation: int, message: str) -> None:
        if generation == self._generation:
            self._status.setText(message)
            self._list.setEnabled(True)
            self._search.setEnabled(True)
            self._ok.setEnabled(self.selected_project() is not None)

    def reject(self) -> None:
        if self._closing:
            return
        self.verified_project = None
        self._generation += 1
        if self._cancellation is not None:
            self._cancellation.cancel()
        if any(worker.isRunning() for worker in self._workers):
            # Keep the dialog alive until every QThread has emitted ``finished``.
            # Cancellation is cooperative, so an in-flight HTTP call may need to
            # return before its next cancellation checkpoint.
            self._closing = True
            self._status.setText("正在取消项目加载…")
            self._search.setEnabled(False)
            self._list.setEnabled(False)
            self._ok.setEnabled(False)
            return
        self._finish_reject()

    def _load(self) -> None:
        config = self._config
        if not config.token:
            self._status.setText("尚未配置 ParaTranz Token，请先在设置中完成连接。")
            return
        self._generation += 1
        generation = self._generation
        cancellation = ParaTranzCatalogCancellation()
        self._cancellation = cancellation

        def fetch():
            service = ParaTranzService.from_config(config)
            try:
                key = ParaTranzCatalogKey(config.base_url, self._account_id, self._config_revision)
                return self._catalog.list_my_projects(service, key, cancellation=cancellation).projects
            finally:
                service.close()

        worker = ApiWorker(fetch)
        worker.result.connect(lambda projects: self._loaded(generation, projects))
        worker.error.connect(lambda error: self._failed(generation, error))
        self._start_worker(worker)

    def _start_worker(self, worker: ApiWorker) -> None:
        worker.finished.connect(lambda: self._worker_finished(worker))
        self._workers.append(worker)
        worker.start()

    def _worker_finished(self, worker: ApiWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()
        if self._closing and not any(item.isRunning() for item in self._workers):
            self._finish_reject()
        elif self.verified_project is not None and not self._workers:
            super().accept()

    def _finish_reject(self) -> None:
        self._closing = False
        QDialog.reject(self)

    def _loaded(self, generation: int, projects) -> None:
        if generation != self._generation:
            return
        self._projects = tuple(projects)
        self._status.setText(f"共 {len(self._projects)} 个可选项目")
        self._render()

    def _failed(self, generation: int, error: str) -> None:
        if generation == self._generation:
            self._status.setText(f"加载失败：{error}")

    def _render(self) -> None:
        keyword = self._search.text().strip().lower()
        self._list.clear()
        for project in self._projects:
            project_id = int(project.project_id)
            name = project.name
            if keyword and keyword not in name.lower() and keyword not in str(project_id):
                continue
            item = QListWidgetItem(f"{name}  ·  #{project_id}")
            item.setData(Qt.ItemDataRole.UserRole, {"id": project_id, "name": name})
            self._list.addItem(item)


class RemoteTargetView(QWidget):
    binding_changed = pyqtSignal()
    state_changed = pyqtSignal(object)

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._ctx = ctx
        self._presenter = RemoteTargetPresenter()
        self._catalog = ParaTranzProjectCatalog()
        self._config_revision = 0
        self._project_generation = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._label = ElidedLabel(parent=self)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._label.setAccessibleName("当前 ParaTranz 同步目标")
        row.addWidget(self._label, 1)
        self._choose = QPushButton("绑定项目…", self)
        ComponentStyle.apply_static(self._choose, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        reserve_text_width(self._choose, ("绑定项目…", "更换…"))
        self._choose.clicked.connect(self.choose_target)
        row.addWidget(self._choose)
        self._more = QToolButton(self)
        self._more.setText("…")
        self._more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._more.setAccessibleName("ParaTranz 绑定操作")
        self._more.setToolTip("ParaTranz 绑定操作")
        self._more.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        ComponentStyle.apply_static(self._more, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        more_policy = self._more.sizePolicy()
        more_policy.setRetainSizeWhenHidden(True)
        self._more.setSizePolicy(more_policy)
        menu = QMenu(self._more)
        self._clear = menu.addAction("解除绑定")
        self._clear.triggered.connect(self.clear_target)
        self._more.setMenu(menu)
        row.addWidget(self._more)
        ctx.paratranz_binding_changed.connect(lambda _binding: self.refresh())
        ctx.config_changed.connect(self._on_config_changed)
        ctx.user_changed.connect(lambda _user: self.refresh())
        ctx.project_changed.connect(self._on_project_changed)
        self.refresh()

    def refresh(self) -> None:
        target = self._ctx.resolve_paratranz_target()
        state = self._presenter.present(target)
        self.state = state
        self._label.set_full_text(state.summary or state.title)
        detail = state.title if not state.detail else f"{state.title}\n{state.detail}"
        self._label.setToolTip(detail)
        self._label.setAccessibleDescription(detail)
        ComponentStyle.apply_state(self._label, SemanticState(state.semantic_state))
        self._choose.setText(state.action_text)
        editable = self._can_edit_binding()
        self._choose.setEnabled(editable)
        self._choose.setToolTip("" if editable else "请先打开一个可用的本地工程。")
        self._more.setVisible(state.can_clear)
        self._more.setEnabled(editable)
        self._clear.setEnabled(editable and state.can_clear)
        self.state_changed.emit(state)

    def choose_target(self) -> None:
        if not self._can_edit_binding():
            return
        try:
            selection = BindingSelection.capture(self._ctx)
        except ValueError as exc:
            QMessageBox.warning(self, "无法绑定", str(exc))
            return
        project_generation = self._project_generation
        dialog = ParaTranzTargetDialog(
            self._ctx, self._catalog, self._config_revision, self, binding_selection=selection
        )
        if not dialog.exec():
            return
        project = dialog.verified_project
        if project is None:
            return
        if (
            self._ctx.active_project_id != selection.local_project_id
            or self._project_generation != project_generation
            or not self._can_edit_binding()
        ):
            QMessageBox.warning(self, "工程已变化", "本地工程已变化或正在切换，请重新选择 ParaTranz 同步目标。")
            return
        endpoint, frozen_user_id, config_revision = dialog.target_context
        user_id = None if self._ctx.current_user is None else self._ctx.current_user.get("id")
        if user_id is None:
            user_id = getattr(self._ctx.config, "user_id", None)
        if (
            config_revision != self._config_revision
            or endpoint != normalize_paratranz_endpoint(self._ctx.config.base_url)
            or frozen_user_id != user_id
        ):
            QMessageBox.warning(self, "配置已变化", "ParaTranz 账号或服务配置已变化，请重新选择同步目标。")
            return
        try:
            result = selection.bind(self._ctx, project)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "绑定失败", str(exc))
            return
        if not result.is_success:
            message = result.diagnostics[0].message if result.diagnostics else "未知错误"
            QMessageBox.critical(self, "绑定失败", message)
            return
        self.binding_changed.emit()

    def clear_target(self) -> None:
        if not self._can_edit_binding() or not self.state.can_clear:
            return
        try:
            result = self._ctx.clear_paratranz_binding()
        except RuntimeError as exc:
            QMessageBox.critical(self, "解除失败", str(exc))
            return
        if not result.is_success:
            message = result.diagnostics[0].message if result.diagnostics else "未知错误"
            QMessageBox.critical(self, "解除失败", message)
            return
        self.binding_changed.emit()

    def _can_edit_binding(self) -> bool:
        return (
            self._ctx.active_project_id is not None
            and not getattr(self._ctx, "project_opening", False)
            and not getattr(self._ctx, "project_view_failed", False)
        )

    def _on_project_changed(self) -> None:
        self._project_generation += 1
        self._more.menu().close()
        self.refresh()

    def _on_config_changed(self, _config) -> None:
        self._config_revision += 1
        self._catalog.clear()
        self.refresh()


__all__ = ["ParaTranzTargetDialog", "RemoteTargetView"]

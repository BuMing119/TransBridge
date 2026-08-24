"""Workbench ParaTranz binding view and asynchronous project picker."""

from __future__ import annotations

from copy import copy
from datetime import datetime
from threading import Event

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.projects import ParaTranzProjectBinding, normalize_paratranz_endpoint
from transbridge.paratranz.project_catalog import ParaTranzCatalogKey, ParaTranzProjectCatalog
from transbridge.paratranz.service import ParaTranzService
from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    reserve_text_width,
)
from transbridge.ui.workers import ApiWorker

from .remote_target_presenter import RemoteTargetPresenter


class _CatalogCancellation:
    """Small public cancellation source for the dialog-owned catalog request."""

    def __init__(self) -> None:
        self._event = Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def cancel(self) -> None:
        self._event.set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "ParaTranz project catalog request cancelled")


class ParaTranzTargetDialog(QDialog):
    def __init__(self, ctx, catalog: ParaTranzProjectCatalog, config_revision: int, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._catalog = catalog
        self._config_revision = config_revision
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
        self._cancellation: _CatalogCancellation | None = None
        self._closing = False
        self.setWindowTitle("选择 ParaTranz 同步目标")
        self.resize(520, 460)

        layout = QVBoxLayout(self)
        explanation = QLabel("选择“我的项目”中的一个项目。此选择只会在确认后绑定到当前本地工程。")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索项目名称或 ID…")
        self._search.textChanged.connect(self._render)
        ComponentStyle.apply_static(self._search, ComponentKind.INPUT)
        layout.addWidget(self._search)
        self._list = QListWidget(self)
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())
        layout.addWidget(self._list, 1)
        self._status = QLabel("正在加载我的项目…", self)
        layout.addWidget(self._status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("绑定到当前工程")
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
        if self.selected_project() is None:
            return
        super().accept()

    def reject(self) -> None:
        if self._closing:
            return
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
        cancellation = _CatalogCancellation()
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
        worker.finished.connect(lambda: self._worker_finished(worker))
        worker.start()
        self._workers.append(worker)

    def _worker_finished(self, worker: ApiWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()
        if self._closing and not any(item.isRunning() for item in self._workers):
            self._finish_reject()

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

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._ctx = ctx
        self._presenter = RemoteTargetPresenter()
        self._catalog = ParaTranzProjectCatalog()
        self._config_revision = 0
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._label = ElidedLabel(parent=self)
        self._label.setAccessibleName("当前 ParaTranz 同步目标")
        row.addWidget(self._label, 1)
        self._choose = QPushButton("选择…", self)
        ComponentStyle.apply_static(self._choose, ComponentKind.BUTTON)
        reserve_text_width(self._choose, ("选择…", "更换…"))
        self._choose.clicked.connect(self.choose_target)
        row.addWidget(self._choose)
        self._clear = QPushButton("解除", self)
        self._clear.setFlat(True)
        ComponentStyle.apply_static(self._clear, ComponentKind.BUTTON)
        clear_policy = self._clear.sizePolicy()
        clear_policy.setRetainSizeWhenHidden(True)
        self._clear.setSizePolicy(clear_policy)
        self._clear.clicked.connect(self.clear_target)
        row.addWidget(self._clear)
        ctx.paratranz_binding_changed.connect(lambda _binding: self.refresh())
        ctx.config_changed.connect(self._on_config_changed)
        ctx.user_changed.connect(lambda _user: self.refresh())
        ctx.project_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        target = self._ctx.resolve_paratranz_target()
        state = self._presenter.present(target)
        self._label.set_full_text(state.title)
        detail = state.title if not state.detail else f"{state.title}\n{state.detail}"
        self._label.setToolTip(detail)
        self._label.setAccessibleDescription(detail)
        ComponentStyle.apply_state(self._label, SemanticState(state.semantic_state))
        self._choose.setText(state.action_text)
        self._choose.setEnabled(self._ctx.active_project_id is not None)
        self._clear.setVisible(state.can_clear)

    def choose_target(self) -> None:
        if self._ctx.active_project_id is None:
            return
        dialog = ParaTranzTargetDialog(self._ctx, self._catalog, self._config_revision, self)
        if not dialog.exec():
            return
        project = dialog.selected_project()
        if project is None:
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
        now = datetime.now().astimezone().isoformat()
        binding = ParaTranzProjectBinding(
            int(project["id"]),
            str(project["name"]),
            self._ctx.config.base_url,
            user_id,
            now,
            now,
        )
        try:
            result = self._ctx.set_paratranz_binding(binding)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, "绑定失败", str(exc))
            return
        if not result.is_success:
            message = result.diagnostics[0].message if result.diagnostics else "未知错误"
            QMessageBox.critical(self, "绑定失败", message)
            return
        self.binding_changed.emit()

    def clear_target(self) -> None:
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

    def _on_config_changed(self, _config) -> None:
        self._config_revision += 1
        self._catalog.clear()
        self.refresh()


__all__ = ["ParaTranzTargetDialog", "RemoteTargetView"]

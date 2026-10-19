"""Conversation chrome and composer state, separate from chat execution and persistence."""

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea

from transbridge.smart_assistant.service_readiness import read_service_availability
from transbridge.ui.foundation.components import ElidedLabel
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .onboarding_view import OnboardingView
from .request_progress_view import RequestProgressPresenter
from .task_presentation import related_tasks
from .theme_support import CONTEXT_STRUCTURE_STYLE, QUIET_BUTTON_STYLE


class ConversationPresentation(QObject):
    def __init__(self, facade):
        super().__init__(facade)
        self.facade = facade
        self._closed = False
        self._request_count = 0
        self.toolbar = QFrame(facade)
        self.toolbar.setObjectName("smartAssistantContext")
        self.toolbar.setStyleSheet(CONTEXT_STRUCTURE_STYLE)
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 3)
        toolbar_layout.setSpacing(6)
        self.context_label = ElidedLabel("")
        self.context_label.setAccessibleName("当前项目与选区")
        self.context_label.setMinimumWidth(0)
        toolbar_layout.addWidget(self.context_label, 1)
        facade._main_layout.insertWidget(0, self.toolbar)
        self.empty = OnboardingView(theme=facade._theme)
        navigation = getattr(facade, "_navigation", {})
        availability = navigation.get("service_availability")
        self._service_availability = availability() if availability else read_service_availability()
        self.empty.configure_navigation(
            open_settings=navigation.get("open_settings"), open_project=navigation.get("open_project")
        )
        self.quick = self.empty.quick
        self.quick.action_clicked.connect(facade.set_input)
        self.quick.skill_triggered.connect(facade._input_actions.select_skill)
        facade._msg_layout.insertWidget(0, self.empty)
        self.notice = QPushButton()
        self.notice.setStyleSheet(QUIET_BUTTON_STYLE)
        self.notice.setAccessibleName("需要处理的输入")
        self.notice.hide()
        facade._main_layout.insertWidget(1, self.notice)
        self.tasks_button = QPushButton()
        self.tasks_button.setCheckable(True)
        self.tasks_button.setAccessibleName("展开当前会话的任务")
        self.tasks_button.setToolTip("查看当前会话的全部任务、结果及执行过程")
        self.tasks_button.setStyleSheet(QUIET_BUTTON_STYLE)
        self.tasks_button.hide()
        toolbar_layout.addWidget(self.tasks_button)
        self.menu_button = QPushButton()
        self.menu_button.setFixedSize(28, 28)
        self.menu_button.setStyleSheet(QUIET_BUTTON_STYLE)
        self.menu_button.setAccessibleName("会话菜单")
        self.menu_button.setToolTip("会话菜单 · 任务、历史搜索")
        self.menu_button.clicked.connect(self._show_menu)
        toolbar_layout.addWidget(self.menu_button)
        self.search_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), facade)
        self.search_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.search_shortcut.activated.connect(lambda: facade._message_list.navigation.open_search())
        self.dialog = QScrollArea(facade)
        self.dialog.setFrameShape(QFrame.Shape.NoFrame)
        self.dialog.setAccessibleName("当前会话任务面板")
        self.dialog.setWidgetResizable(True)
        self.dialog.setMinimumWidth(0)
        self.dialog.setMaximumHeight(220)
        self.dialog.hide()
        facade._main_layout.insertWidget(2, self.dialog)
        self.tasks_button.toggled.connect(self.dialog.setVisible)
        self.progress = None
        self._monitor = None
        binding = facade._request_binding
        if binding is not None:
            self.dialog.setWidget(binding.view)
            binding.view.show()
            binding.view.pending_changed.connect(self._pending)
            self.progress = RequestProgressPresenter(binding, facade._message_list, facade._theme)
            self.progress.display(binding.view._requests)
            binding.view.locate.connect(self._locate_source)
            binding.view.review.connect(self.progress.review_result)
            binding.view.requests_changed.connect(self._requests_changed)
            self._requests_changed(binding.view._requests)
            self.notice.clicked.connect(self.show_details)
        else:
            self.dialog.setWidget(QLabel("发送要求后，可以在这里查看任务进度和结果。"))
        facade._input_view.configure_actions(stop=self.stop, details=self.show_details)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.apply_theme(facade._theme)
        self.refresh()

    def apply_theme(self, theme):
        for widget in (self.toolbar, self.empty, self.notice, self.dialog, self.tasks_button, self.menu_button):
            theme.apply_surface(widget)
        self.menu_button.setIcon(tabler_icon(self.menu_button, "dots", 18))
        theme.apply_semantic(self.context_label, "muted")
        self.empty.apply_theme(theme)
        if self.progress is not None:
            self.progress.theme = theme

    def refresh(self):
        if self._closed:
            return
        facade = self.facade
        ctx = facade.context
        project = getattr(ctx, "active_project", None)
        name = getattr(ctx, "project_name", "") or (getattr(project, "name", "") if project is not None else "")
        path = getattr(ctx, "esp_path", "")
        if not name and isinstance(path, (str, Path)) and path:
            name = Path(path).name
        selected = getattr(ctx, "selected_entries", None)
        if selected is None:
            selected = getattr(ctx, "selected_ids", ())
        selected = selected or ()
        scope = f"当前选中 {len(selected)} 条" if selected else "当前未选中词条"
        text = f"{name or '未打开项目'}  ·  {scope}"
        self.context_label.set_full_text(text)
        self.context_label.setToolTip(text + "；每次发送时记录范围，已提交任务不会跟随选区改变。")
        facade._theme.apply_semantic(self.context_label, "muted")
        availability = getattr(facade, "_navigation", {}).get("service_availability")
        service = availability() if availability else self._service_availability
        self.empty.set_state(
            model_configured=service.ready,
            service_message=service.message,
            project_name=name,
            selected_count=len(selected),
        )
        self.empty.setVisible(
            not facade._message_list._owned_widgets
            and not (
                self.progress and any(facade._message_list.contains(card) for card in self.progress.cards.values())
            )
        )
        binding = facade._request_binding
        running = facade._controller.state.value in {"thinking", "executing", "awaiting_task"}
        if binding is not None:
            running = running or binding._accepting > 0 or binding.admission is not None
        facade._input_view.set_running(running)
        monitor = facade._task_monitor
        if monitor is not self._monitor:
            if self._monitor is not None:
                self._monitor.tasks_changed.disconnect(self._tasks_changed)
            self._monitor = monitor
            if monitor is not None:
                monitor.tasks_changed.connect(self._tasks_changed)
                self._tasks_changed(tuple(monitor._tasks.values()))

    def _requests_changed(self, requests):
        from .request_progress_view import request_status

        active = sum(r.status in {"open", "stopping"} for r in requests)
        waiting = sum(request_status(r) in {"等待确认", "执行结果待核对"} or r.status == "failed" for r in requests)
        self._request_count = len(requests)
        self.tasks_button.setText(f"待处理 {waiting}" if waiting else f"进行中 {active}")
        self.tasks_button.setVisible(bool(active or waiting))
        self.tasks_button.setAccessibleDescription(
            f"共 {len(requests)} 项任务，进行中 {active} 项，待处理 {waiting} 项"
        )
        if self._monitor is not None:
            self._tasks_changed(tuple(self._monitor._tasks.values()))

    def _tasks_changed(self, tasks):
        binding = self.facade._request_binding
        if binding is None:
            return
        grouped = {
            task.get("task_id", "") for request in binding.view._requests for task in related_tasks(request, tasks)
        }
        self._monitor.set_grouped_tasks(grouped)
        binding.view.set_tasks(tasks)
        self.progress.set_tasks(tasks)

    def _pending(self, count, routing):
        self.notice.setText(f"有 {count} 条要求尚未接纳 · 查看详情" if count else "有待处理的输入 · 查看详情")
        self.notice.setVisible(bool(count or routing))

    def show_details(self):
        self._set_details_visible(True)

    def _set_details_visible(self, visible):
        self.tasks_button.setChecked(visible)
        self.dialog.setVisible(visible)

    def _show_menu(self):
        menu = QMenu(self.menu_button)
        label = "收起任务面板" if not self.dialog.isHidden() else f"全部任务与结果（{self._request_count}）"
        menu.addAction(label, lambda: self._set_details_visible(self.dialog.isHidden()))
        search = menu.addAction("搜索会话历史", self.facade._message_list.navigation.open_search)
        search.setShortcut(QKeySequence.StandardKey.Find)
        # The scoped shortcut owns activation; the menu only advertises the same key.
        search.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))
        menu.deleteLater()

    def _locate_source(self, message_ids):
        self._set_details_visible(False)
        self.progress.locate_source(message_ids)

    def stop(self):
        binding = self.facade._request_binding
        if binding is not None:
            binding.management.stop_generation()
        else:
            self.facade._submission.invalidate()
            self.facade._plan_execution.interrupt_round()
            self.facade._confirmation_view.invalidate_pending()
            self.facade._react_execution.abort()
            self.facade._controller.handle_abort()
        self.refresh()

    def close(self):
        self._closed = True
        self.timer.stop()
        self.search_shortcut.setEnabled(False)
        if self._monitor is not None:
            self._monitor.tasks_changed.disconnect(self._tasks_changed)
        self.dialog.close()
        if self.progress is not None:
            self.progress.close()

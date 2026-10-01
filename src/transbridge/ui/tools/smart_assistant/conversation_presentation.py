"""Conversation chrome and composer state, separate from chat execution and persistence."""

from pathlib import Path

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from transbridge.ui.foundation.components import ElidedLabel

from .quick_actions import QuickActionsChips
from .request_progress_view import RequestProgressPresenter


class ConversationPresentation(QObject):
    def __init__(self, facade):
        super().__init__(facade)
        self.facade = facade
        self._closed = False
        self.context_label = ElidedLabel("")
        self.context_label.setAccessibleName("当前项目与选区")
        self.context_label.setMinimumWidth(0)
        facade._main_layout.insertWidget(0, self.context_label)
        self.empty = QWidget()
        layout = QVBoxLayout(self.empty)
        layout.setContentsMargins(12, 32, 12, 32)
        title = QLabel("今天想处理什么？")
        layout.addWidget(title)
        self.quick = QuickActionsChips(theme=facade._theme)
        self.quick.action_clicked.connect(facade.set_input)
        self.quick.skill_triggered.connect(facade._input_actions.select_skill)
        layout.addWidget(self.quick)
        facade._msg_layout.insertWidget(0, self.empty)
        self.notice = QPushButton()
        self.notice.setAccessibleName("需要处理的输入")
        self.notice.hide()
        facade._main_layout.insertWidget(1, self.notice)
        self.dialog = QDialog(facade)
        self.dialog.setWindowTitle("任务与会话详情")
        self.dialog.resize(620, 440)
        self.progress = None
        binding = facade._request_binding
        if binding is not None:
            QVBoxLayout(self.dialog).addWidget(binding.view)
            binding.view.show()
            binding.view.pending_changed.connect(self._pending)
            self.progress = RequestProgressPresenter(binding, facade._message_list, facade._theme)
            self.progress.display(binding.view._requests)
            self.notice.clicked.connect(self.show_details)
        else:
            QVBoxLayout(self.dialog).addWidget(QLabel("当前会话没有持久化的任务详情。"))
        facade._input_view.configure_actions(stop=self.stop, details=self.show_details)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.apply_theme(facade._theme)
        self.refresh()

    def apply_theme(self, theme):
        for widget in (self.empty, self.notice, self.dialog):
            theme.apply_surface(widget)
        theme.apply_semantic(self.context_label, "muted")
        self.quick.apply_theme(theme)
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

    def _pending(self, count, routing):
        self.notice.setText(f"有 {count} 条要求需要确认所属任务" if count else "有待处理的输入 · 查看详情")
        self.notice.setVisible(bool(count or routing))

    def show_details(self):
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

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
        self.dialog.close()
        if self.progress is not None:
            self.progress.close()

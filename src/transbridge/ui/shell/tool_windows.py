"""Create and reuse auxiliary windows without owning their business state."""

from __future__ import annotations

from PyQt6.QtCore import Qt

from transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI
from transbridge.ui.paratranz.config_dialog import ConfigDialog
from transbridge.ui.workers import ApiWorker


class ToolWindows:
    def __init__(self, host) -> None:
        self._host = host
        self.assistant_panel = None

    def load_current_user(self) -> None:
        context = self._host.context
        config = context.config

        def fetch():
            return ParatranzUserAPI(token=config.token, config=config).get_my_user()

        def done(user) -> None:
            context.current_user = user
            user_id = user.get("id") if isinstance(user, dict) else None
            if user_id and config.user_id != user_id:
                config.user_id = user_id
                config.save_to_file()

        worker = ApiWorker(fetch)
        worker.result.connect(done)
        worker.error.connect(lambda error: self._host.show_message(f"获取用户信息失败: {error}"))
        worker.start()
        self._host.workers.append(worker)

    def refresh_projects(self) -> None:
        self._host.paratranz_widget.refresh_projects()

    def show_config(self) -> None:
        ConfigDialog(self._host.context, None).exec()
        if self._host.context.config.token and not self._host.context.current_user:
            self.load_current_user()

    def show_user(self) -> None:
        if not self._host.context.current_user:
            self._host.show_message("请先配置 API Token")
            return
        from transbridge.ui.paratranz.user_dialog import UserInfoDialog

        UserInfoDialog(self._host.context, self._host).exec()

    def show_mails(self) -> None:
        if not self._host.context.current_user:
            self._host.show_message("请先配置 API Token")
            return
        from transbridge.ui.paratranz.mails_dialog import MailsDialog

        MailsDialog(self._host.context, self._host).exec()

    def open_ai_translator(self) -> None:
        runtime = getattr(self._host, "app_runtime", None)
        if runtime is None:
            self._host.workbench.open_tool("ai_translator")
        else:
            self._host.workbench.open_tool("ai_translator", task_runtime=runtime.tasks)

    def open_dictionary(self) -> None:
        from transbridge.ui.tools.dictionary_panel import DictionaryPanel

        DictionaryPanel(self._host.context, self._host).exec()

    def open_fomod(self, new_archive: str | None = None) -> None:
        from transbridge.ui.tools.fomod import FomodPanel

        panel = FomodPanel(
            self._host.context,
            self._host,
            operation_plan_facade=getattr(self._host, "operation_plan_facade", None),
        )
        if new_archive:
            panel.prefill_new_archive(new_archive)
        panel.exec()

    def get_assistant_panel(self):
        if self.assistant_panel is None or self.assistant_panel.is_disposed:
            from transbridge.ui.tools.smart_assistant import SmartAssistantPanel

            previous = self.assistant_panel
            if previous is not None:
                previous.deleteLater()
                self.assistant_panel = None
            panel = SmartAssistantPanel(
                self._host.context,
                self._host,
                session_commands=self._host.session_commands,
                session_projection=self._host.session_projection,
                runtime_context=self._host.runtime_context,
            )
            self.assistant_panel = panel
            panel.visibility_changed.connect(self.on_assistant_visibility_changed)
            panel_identity = id(panel)
            panel.destroyed.connect(lambda _obj=None: self._clear_assistant_panel(panel_identity))
            self._host.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, panel)
            panel.hide()
        return self.assistant_panel

    def _clear_assistant_panel(self, panel_identity: int) -> None:
        if self.assistant_panel is not None and id(self.assistant_panel) == panel_identity:
            self.assistant_panel = None

    def dispose(self, *, wait_for_worker: bool = True) -> None:
        panel = self.assistant_panel
        if panel is not None:
            panel.dispose(wait_for_worker=wait_for_worker)

    def toggle_smart_assistant(self) -> None:
        panel = self.get_assistant_panel()
        if panel.isVisible():
            panel.hide()
        else:
            panel.show()
            panel.raise_()

    def on_assistant_visibility_changed(self, visible: bool) -> None:
        self._host.operation_menu.smart_assistant.setChecked(visible)
        if self._host.operation_menu.view_assistant is not self._host.operation_menu.smart_assistant:
            self._host.operation_menu.view_assistant.setChecked(visible)

"""Create and reuse auxiliary windows without owning their business state."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI
from transbridge.ui.paratranz.config_dialog import ConfigDialog
from transbridge.ui.workers import ApiWorker

from .overlay_geometry import workspace_overlay_rect

_logger = logging.getLogger(__name__)


def _fetch_current_user(config):
    api = ParatranzUserAPI(token=config.token, config=config)
    try:
        user = api.get_my_user()
        try:
            return api.with_avatar_payload(user)
        except Exception:
            _logger.warning("ParaTranz user loaded, but the avatar download failed", exc_info=True)
            return user
    finally:
        api.close()


class ToolWindows:
    def __init__(self, host) -> None:
        self._host = host
        self.assistant_panel = None
        self._assistant_overlay_host_geometry: QRect | None = None
        self._terminology_launcher = None

    def load_current_user(self) -> None:
        context = self._host.context
        config = context.config

        def fetch():
            return _fetch_current_user(config)

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

    def show_ui_settings(self) -> None:
        foundation = getattr(self._host, "ui_foundation", None)
        if foundation is None:
            self._host.show_message("通用设置当前不可用，请稍后重试。")
            return
        from transbridge.ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(
            foundation.theme,
            foundation.config,
            self._host,
            registry=foundation.registry,
            locale_service=foundation.locale,
        )
        dialog.service_settings_requested.connect(self.show_config)
        dialog.exec()

    def show_user(self) -> None:
        if not self._host.context.current_user:
            self._host.show_message("请先配置 API Token")
            return
        from transbridge.ui.paratranz.user_dialog import UserInfoDialog

        UserInfoDialog(self._host.context, self._host).exec()
        self.load_current_user()

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

    def open_terminology(self) -> None:
        if self._terminology_launcher is None:
            from transbridge.ui.tools.terminology import TerminologyLauncher

            self._terminology_launcher = TerminologyLauncher(self._host)
        self._terminology_launcher.open()

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
                None,
                session_commands=self._host.session_commands,
                session_projection=self._host.session_projection,
                runtime_context=self._host.runtime_context,
                theme_view=getattr(self._host, "theme_view", None),
            )
            icon_getter = getattr(self._host, "windowIcon", None)
            application = QApplication.instance()
            if callable(icon_getter):
                icon = icon_getter()
            else:
                icon = None if application is None else application.windowIcon()
            if icon is not None and not icon.isNull():
                panel.setWindowIcon(icon)
            self.assistant_panel = panel
            self._assistant_overlay_host_geometry = None
            panel.visibility_changed.connect(self.on_assistant_visibility_changed)
            panel_identity = id(panel)
            panel.destroyed.connect(lambda _obj=None: self._clear_assistant_panel(panel_identity))
            panel.hide()
        return self.assistant_panel

    def _clear_assistant_panel(self, panel_identity: int) -> None:
        if self.assistant_panel is not None and id(self.assistant_panel) == panel_identity:
            self.assistant_panel = None
            self._assistant_overlay_host_geometry = None

    def dispose(self, *, wait_for_worker: bool = True) -> None:
        if self._terminology_launcher is not None:
            self._terminology_launcher.close()
            self._terminology_launcher = None
        panel = self.assistant_panel
        if panel is not None:
            panel.close()
            panel.dispose(wait_for_worker=wait_for_worker)
            panel.deleteLater()
            self.assistant_panel = None
            self._assistant_overlay_host_geometry = None

    def toggle_smart_assistant(self) -> None:
        panel = self.get_assistant_panel()
        if panel.isMinimized():
            panel.showNormal()
            panel.raise_()
            panel.activateWindow()
        elif panel.isVisible():
            panel.hide()
        else:
            # The parentless Qt.Window remains independent from the translation
            # workbench; SmartAssistantPanel gives its HWND a separate taskbar
            # identity when it is shown on Windows.
            host_geometry = QRect(self._host.frameGeometry())
            if self._assistant_overlay_host_geometry != host_geometry:
                overlay_rect = workspace_overlay_rect(self._host.rect())
                panel.resize(overlay_rect.size())
                panel.move(self._host.mapToGlobal(overlay_rect.topLeft()))
                self._assistant_overlay_host_geometry = host_geometry
            panel.show()
            panel.raise_()
            panel.activateWindow()

    def on_assistant_visibility_changed(self, visible: bool) -> None:
        self._host.operation_menu.smart_assistant.setChecked(visible)
        if self._host.operation_menu.view_assistant is not self._host.operation_menu.smart_assistant:
            self._host.operation_menu.view_assistant.setChecked(visible)

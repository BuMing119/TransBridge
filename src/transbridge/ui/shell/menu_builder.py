"""Build top-level actions without owning their application behavior."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMainWindow, QMenu

from .action_catalog import DEFAULT_ACTION_CATALOG, ActionCatalog, IntentId

VoidCallback = Callable[[], None]


@dataclass(frozen=True, slots=True)
class MenuCallbacks:
    new_project: VoidCallback
    open_project: VoidCallback
    parse: VoidCallback
    migrate: VoidCallback
    upload: VoidCallback
    batch_upload: VoidCallback
    download: VoidCallback
    batch_download: VoidCallback
    write: VoidCallback
    batch_write: VoidCallback
    new_variant: VoidCallback
    copy_variant: VoidCallback
    save_snapshot: VoidCallback
    load_snapshot: VoidCallback
    export_transbridge: VoidCallback
    import_transbridge: VoidCallback
    refresh_projects: VoidCallback
    show_appearance: VoidCallback
    show_config: VoidCallback
    open_ai_translator: VoidCallback
    toggle_smart_assistant: VoidCallback
    open_dictionary: VoidCallback
    open_fomod: VoidCallback
    show_user: VoidCallback
    show_mails: VoidCallback
    show_about: VoidCallback
    manual_save: VoidCallback
    show_context_help: VoidCallback = lambda: None
    show_task_activity: VoidCallback = lambda: None
    exit_app: VoidCallback | None = None


@dataclass(frozen=True, slots=True)
class MenuHandles:
    parse: QAction
    migrate: QAction
    upload: QAction
    batch_upload: QAction
    download: QAction
    batch_download: QAction
    write: QAction
    batch_write: QAction
    variant_menu: QMenu
    new_variant: QAction
    copy_variant: QAction
    save_snapshot: QAction
    load_snapshot: QAction
    export_transbridge: QAction
    import_transbridge: QAction
    ai_translator: QAction
    smart_assistant: QAction
    view_assistant: QAction
    dictionary: QAction
    fomod: QAction
    appearance: QAction


class MenuBuilder:
    """Construct menus/shortcuts and return stable action handles."""

    def __init__(
        self,
        window: QMainWindow,
        callbacks: MenuCallbacks,
        catalog: ActionCatalog = DEFAULT_ACTION_CATALOG,
    ) -> None:
        self._window = window
        self._callbacks = callbacks
        self._catalog = catalog
        locale = getattr(getattr(window, "ui_foundation", None), "locale", None)
        candidate = getattr(locale, "gettext", None)
        self._gettext: Callable[[str], str] = candidate if callable(candidate) else lambda value: value

    @staticmethod
    def _action(menu: QMenu, text: str, callback: VoidCallback, shortcut: str | None = None) -> QAction:
        action = menu.addAction(text)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(callback)
        return action

    def _intent_action(
        self,
        menu: QMenu,
        intent_id: IntentId,
        callback: VoidCallback,
    ) -> QAction:
        descriptor = self._catalog.get(intent_id)
        action = self._action(menu, self._gettext(descriptor.label), callback, descriptor.shortcut)
        action.setData(intent_id.value)
        action.setProperty("intent_id", intent_id.value)
        action.setCheckable(descriptor.checkable)
        status_tip = self._gettext(descriptor.status_tip or descriptor.label)
        action.setStatusTip(status_tip)
        action.setToolTip(status_tip)
        return action

    def build(self) -> MenuHandles:
        callbacks = self._callbacks
        bar = self._window.menuBar()

        file_menu = bar.addMenu(self._gettext("文件"))
        import_transbridge = self._intent_action(file_menu, IntentId.PROJECT_IMPORT, callbacks.import_transbridge)
        export_transbridge = self._intent_action(file_menu, IntentId.PROJECT_EXPORT, callbacks.export_transbridge)
        file_menu.addSeparator()
        self._intent_action(file_menu, IntentId.APP_EXIT, callbacks.exit_app or self._window.close)

        project_menu = bar.addMenu(self._gettext("项目"))
        self._intent_action(project_menu, IntentId.PROJECT_CREATE, callbacks.new_project)
        self._intent_action(project_menu, IntentId.PROJECT_OPEN, callbacks.open_project)
        self._intent_action(project_menu, IntentId.PROJECT_SAVE, callbacks.manual_save)
        project_menu.addSeparator()
        variant_menu = project_menu.addMenu(self._gettext("翻译版本"))
        new_variant = self._intent_action(variant_menu, IntentId.PROJECT_VARIANT_CREATE, callbacks.new_variant)
        copy_variant = self._intent_action(variant_menu, IntentId.PROJECT_VARIANT_COPY, callbacks.copy_variant)
        variant_menu.addSeparator()
        save_snapshot = self._intent_action(variant_menu, IntentId.PROJECT_SNAPSHOT_SAVE, callbacks.save_snapshot)
        load_snapshot = self._intent_action(variant_menu, IntentId.PROJECT_SNAPSHOT_LOAD, callbacks.load_snapshot)
        project_menu.addSeparator()
        self._intent_action(project_menu, IntentId.PROJECT_REFRESH, callbacks.refresh_projects)

        translation_menu = bar.addMenu(self._gettext("翻译"))
        parse = self._intent_action(translation_menu, IntentId.SOURCE_PARSE, callbacks.parse)
        migrate = self._intent_action(translation_menu, IntentId.SOURCE_MIGRATE, callbacks.migrate)
        translation_menu.addSeparator()
        ai_translator = self._intent_action(translation_menu, IntentId.TRANSLATION_AI, callbacks.open_ai_translator)
        dictionary = self._intent_action(translation_menu, IntentId.TRANSLATION_DICTIONARY, callbacks.open_dictionary)

        sync_menu = bar.addMenu(self._gettext("同步与发布"))
        upload = self._intent_action(sync_menu, IntentId.SYNC_UPLOAD, callbacks.upload)
        batch_upload = self._intent_action(sync_menu, IntentId.SYNC_UPLOAD_BATCH, callbacks.batch_upload)
        sync_menu.addSeparator()
        download = self._intent_action(sync_menu, IntentId.SYNC_DOWNLOAD, callbacks.download)
        batch_download = self._intent_action(sync_menu, IntentId.SYNC_DOWNLOAD_BATCH, callbacks.batch_download)
        sync_menu.addSeparator()
        write = self._intent_action(sync_menu, IntentId.PUBLISH_WRITE, callbacks.write)
        batch_write = self._intent_action(sync_menu, IntentId.PUBLISH_WRITE_BATCH, callbacks.batch_write)
        sync_menu.addSeparator()
        fomod = self._intent_action(sync_menu, IntentId.PUBLISH_FOMOD, callbacks.open_fomod)

        view_menu = bar.addMenu(self._gettext("视图"))
        self._intent_action(view_menu, IntentId.TASK_OPEN_ACTIVITY, callbacks.show_task_activity)
        smart_assistant = self._intent_action(
            view_menu,
            IntentId.VIEW_SMART_ASSISTANT,
            callbacks.toggle_smart_assistant,
        )
        # Compatibility handles deliberately point to one authoritative QAction.
        view_assistant = smart_assistant

        settings_menu = bar.addMenu(self._gettext("设置"))
        appearance = self._intent_action(settings_menu, IntentId.SETTINGS_APPEARANCE, callbacks.show_appearance)
        settings_menu.addSeparator()
        self._intent_action(settings_menu, IntentId.SETTINGS_SERVICES, callbacks.show_config)
        settings_menu.addSeparator()
        self._intent_action(settings_menu, IntentId.SETTINGS_ACCOUNT, callbacks.show_user)
        self._intent_action(settings_menu, IntentId.SETTINGS_MESSAGES, callbacks.show_mails)

        help_menu = bar.addMenu(self._gettext("帮助"))
        self._intent_action(help_menu, IntentId.HELP_CONTEXT, callbacks.show_context_help)
        self._intent_action(help_menu, IntentId.HELP_ABOUT, callbacks.show_about)

        # QAction owns discoverable shortcuts.  Ctrl+K stays available for S12.

        return MenuHandles(
            parse=parse,
            migrate=migrate,
            upload=upload,
            batch_upload=batch_upload,
            download=download,
            batch_download=batch_download,
            write=write,
            batch_write=batch_write,
            variant_menu=variant_menu,
            new_variant=new_variant,
            copy_variant=copy_variant,
            save_snapshot=save_snapshot,
            load_snapshot=load_snapshot,
            export_transbridge=export_transbridge,
            import_transbridge=import_transbridge,
            ai_translator=ai_translator,
            smart_assistant=smart_assistant,
            view_assistant=view_assistant,
            dictionary=dictionary,
            fomod=fomod,
            appearance=appearance,
        )

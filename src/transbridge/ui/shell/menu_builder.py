# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
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
    prepare_content: VoidCallback
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
    open_history_search: VoidCallback = lambda: None
    open_terminology: VoidCallback = lambda: None
    show_context_help: VoidCallback = lambda: None
    show_task_activity: VoidCallback = lambda: None
    show_terminology_alignment: VoidCallback = lambda: None
    clear_alignment_cache: VoidCallback = lambda: None
    rename_project: VoidCallback = lambda: None
    update_source: VoidCallback = lambda: None
    change_text_encoding: VoidCallback = lambda: None
    delete_project: VoidCallback = lambda: None
    delete_snapshot: VoidCallback = lambda: None
    exit_app: VoidCallback | None = None
    manage_variants: VoidCallback = lambda: None
    manage_history: VoidCallback = lambda: None
    open_project_properties: VoidCallback = lambda: None


@dataclass(frozen=True, slots=True)
class MenuHandles:
    prepare_content: QAction
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
    history_search: QAction
    terminology: QAction
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
        if descriptor.shortcut:
            # The top-level menu can be hidden while the window shortcut is active.
            self._window.addAction(action)
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

        project_menu = bar.addMenu(self._gettext("工程"))
        self._intent_action(project_menu, IntentId.PROJECT_CREATE, callbacks.new_project)
        self._intent_action(project_menu, IntentId.PROJECT_OPEN, callbacks.open_project)
        self._intent_action(project_menu, IntentId.PROJECT_SAVE, callbacks.manual_save)
        project_menu.addSeparator()
        prepare_content = self._intent_action(
            project_menu, IntentId.WORKBENCH_CONTENT_PREPARE, callbacks.prepare_content
        )
        self._intent_action(project_menu, IntentId.PROJECT_SOURCE_UPDATE, callbacks.update_source)
        project_menu.addSeparator()
        self._intent_action(project_menu, IntentId.PROJECT_VARIANT_MANAGE, callbacks.manage_variants)
        self._intent_action(project_menu, IntentId.PROJECT_SNAPSHOT_MANAGE, callbacks.manage_history)
        self._intent_action(project_menu, IntentId.PROJECT_PROPERTIES, callbacks.open_project_properties)
        project_menu.addSeparator()
        import_transbridge = self._intent_action(project_menu, IntentId.PROJECT_IMPORT, callbacks.import_transbridge)
        export_transbridge = self._intent_action(project_menu, IntentId.PROJECT_EXPORT, callbacks.export_transbridge)
        project_menu.addSeparator()
        self._intent_action(project_menu, IntentId.PROJECT_DELETE, callbacks.delete_project)
        self._intent_action(project_menu, IntentId.APP_EXIT, callbacks.exit_app or self._window.close)

        translation_menu = bar.addMenu(self._gettext("翻译"))
        migrate = self._intent_action(translation_menu, IntentId.SOURCE_MIGRATE, callbacks.migrate)
        ai_translator = self._intent_action(translation_menu, IntentId.TRANSLATION_AI, callbacks.open_ai_translator)
        translation_menu.addSeparator()
        terminology = self._intent_action(translation_menu, IntentId.TERMINOLOGY_WORKBENCH, callbacks.open_terminology)
        dictionary = self._intent_action(translation_menu, IntentId.TRANSLATION_DICTIONARY, callbacks.open_dictionary)
        history_search = self._intent_action(
            translation_menu,
            IntentId.TRANSLATION_HISTORY_SEARCH,
            callbacks.open_history_search,
        )
        translation_menu.addSeparator()
        write = self._intent_action(translation_menu, IntentId.PUBLISH_WRITE, callbacks.write)
        batch_write = self._intent_action(translation_menu, IntentId.PUBLISH_WRITE_BATCH, callbacks.batch_write)
        translation_menu.addSeparator()
        fomod = self._intent_action(translation_menu, IntentId.PUBLISH_FOMOD, callbacks.open_fomod)

        sync_menu = bar.addMenu(self._gettext("ParaTranz"))
        upload = self._intent_action(sync_menu, IntentId.SYNC_UPLOAD, callbacks.upload)
        batch_upload = self._intent_action(sync_menu, IntentId.SYNC_UPLOAD_BATCH, callbacks.batch_upload)
        download = self._intent_action(sync_menu, IntentId.SYNC_DOWNLOAD, callbacks.download)
        batch_download = self._intent_action(sync_menu, IntentId.SYNC_DOWNLOAD_BATCH, callbacks.batch_download)
        batch_download.setVisible(False)
        sync_menu.addSeparator()
        self._intent_action(sync_menu, IntentId.PROJECT_REFRESH, callbacks.refresh_projects)
        sync_menu.addSeparator()
        self._intent_action(sync_menu, IntentId.SETTINGS_ACCOUNT, callbacks.show_user)
        self._intent_action(sync_menu, IntentId.SETTINGS_MESSAGES, callbacks.show_mails)

        view_menu = bar.addMenu(self._gettext("视图"))
        smart_assistant = self._intent_action(
            view_menu,
            IntentId.VIEW_SMART_ASSISTANT,
            callbacks.toggle_smart_assistant,
        )
        # Compatibility handles deliberately point to one authoritative QAction.
        view_assistant = smart_assistant
        self._intent_action(view_menu, IntentId.TASK_OPEN_ACTIVITY, callbacks.show_task_activity)

        settings_menu = bar.addMenu(self._gettext("设置"))
        appearance = self._intent_action(settings_menu, IntentId.SETTINGS_APPEARANCE, callbacks.show_appearance)
        settings_menu.addSeparator()
        self._intent_action(settings_menu, IntentId.SETTINGS_SERVICES, callbacks.show_config)
        self._intent_action(
            settings_menu, IntentId.SETTINGS_TERMINOLOGY_ALIGNMENT, callbacks.show_terminology_alignment
        )
        help_menu = bar.addMenu(self._gettext("帮助"))
        self._intent_action(help_menu, IntentId.HELP_CONTEXT, callbacks.show_context_help)
        self._intent_action(help_menu, IntentId.HELP_ABOUT, callbacks.show_about)

        # These handles predate the management windows; existing callers can
        # still trigger them without duplicating entries in the menu bar.
        variant_menu = QMenu(self._gettext("翻译版本"), self._window)
        new_variant = self._intent_action(variant_menu, IntentId.PROJECT_VARIANT_CREATE, callbacks.new_variant)
        copy_variant = self._intent_action(variant_menu, IntentId.PROJECT_VARIANT_COPY, callbacks.copy_variant)
        save_snapshot = self._intent_action(variant_menu, IntentId.PROJECT_SNAPSHOT_SAVE, callbacks.save_snapshot)
        load_snapshot = self._intent_action(variant_menu, IntentId.PROJECT_SNAPSHOT_LOAD, callbacks.load_snapshot)

        return MenuHandles(
            prepare_content=prepare_content,
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
            history_search=history_search,
            terminology=terminology,
            fomod=fomod,
            appearance=appearance,
        )

"""Qt composition for the settings center, separated from dialog behavior."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from transbridge.config.ui_preferences import ThemeMode
from transbridge.ui.foundation.accessibility import configure_accessible_widget
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.foundation.preview import ThemePreviewWidget

from .advanced_page import AdvancedSettingsPage
from .ai_defaults_page import AiDefaultsPage
from .ai_service_page import AiServicePage
from .connection_checks import (
    SettingsConnectionController,
    embedding_operation,
    llm_operation,
    paratranz_operation,
)
from .embedding_page import EmbeddingSettingsPage
from .page_common import unavailable_page
from .paratranz_page import ParaTranzSettingsPage
from .sections import SECTION_LABELS, SettingsSection
from .terminology_page import TerminologySettingsPage


def build_settings_center(dialog: object, initial_section: SettingsSection) -> None:
    root = QVBoxLayout(dialog)
    root.setContentsMargins(16, 16, 16, 16)
    root.setSpacing(10)
    body = QHBoxLayout()
    body.setSpacing(12)
    dialog._section_list = QListWidget(dialog)
    dialog._section_list.setObjectName("tbSettingsNavigation")
    dialog._section_list.setAccessibleName(dialog._tr("设置分类"))
    dialog._section_list.setFixedWidth(190)
    dialog._stack = QStackedWidget(dialog)
    dialog._stack.setObjectName("tbSettingsPages")
    dialog._settings_pages = {}
    dialog._connection_controllers = []

    appearance = _appearance_page(dialog)
    _add_page(dialog, SettingsSection.APPEARANCE, appearance)
    config = dialog._config_draft.llm
    if config is None:

        def disabled() -> QWidget:
            return unavailable_page(dialog._tr("此设置入口尚未接入服务配置。"), dialog)

        for section in (
            SettingsSection.AI_SERVICE,
            SettingsSection.EMBEDDING,
            SettingsSection.AI_DEFAULTS,
            SettingsSection.TERMINOLOGY,
            SettingsSection.ADVANCED,
        ):
            _add_page(dialog, section, disabled())
    else:
        ai_page = AiServicePage(config, secret_read_only=dialog._config_draft.llm_secret_read_only, parent=dialog)
        _add_page(
            dialog,
            SettingsSection.AI_SERVICE,
            ai_page,
        )
        _connect_check(
            dialog,
            ai_page.test_button,
            ai_page.test_status,
            lambda: _prepare_llm(ai_page, config),
            "测试 AI 连接",
        )
        embedding_page = EmbeddingSettingsPage(
            config,
            secret_read_only=dialog._config_draft.embedding_secret_read_only,
            parent=dialog,
        )
        _add_page(
            dialog,
            SettingsSection.EMBEDDING,
            embedding_page,
        )
        _connect_check(
            dialog,
            embedding_page.test_button,
            embedding_page.test_status,
            lambda: _prepare_embedding(embedding_page, config),
            "测试 Embedding 连接",
        )
        embedding_page.manage_models_button.clicked.connect(lambda: _manage_embedding_models(embedding_page))
        _add_page(dialog, SettingsSection.AI_DEFAULTS, AiDefaultsPage(config, dialog))
        _add_page(dialog, SettingsSection.TERMINOLOGY, TerminologySettingsPage(config, dialog))
        _add_page(
            dialog,
            SettingsSection.ADVANCED,
            AdvancedSettingsPage(
                config,
                secret_read_only=dialog._config_draft.mcp_secret_read_only,
                parent=dialog,
            ),
        )

    paratranz = dialog._config_draft.paratranz
    if paratranz is None:
        page = unavailable_page(dialog._tr("此设置入口尚未接入 ParaTranz 配置。"), dialog)
    else:
        source = dialog._paratranz_config
        read_only = getattr(source, "_secret_source", "") == "environment"
        page = ParaTranzSettingsPage(
            paratranz,
            token_configured=bool(getattr(source, "token", None)),
            token_read_only=read_only,
            parent=dialog,
        )
        _connect_check(
            dialog,
            page.test_button,
            page.test_status,
            lambda: _prepare_paratranz(page, source),
            "验证 ParaTranz 连接",
        )
    _insert_page(dialog, SettingsSection.PARATRANZ, page)

    body.addWidget(dialog._section_list)
    body.addWidget(dialog._stack, 1)
    root.addLayout(body, 1)
    dialog._section_list.currentRowChanged.connect(dialog._stack.setCurrentIndex)

    dialog._buttons = QDialogButtonBox(dialog)
    dialog._apply_button = dialog._buttons.addButton(dialog._tr("应用"), QDialogButtonBox.ButtonRole.AcceptRole)
    dialog._default_button = dialog._buttons.addButton(
        dialog._tr("恢复默认外观"), QDialogButtonBox.ButtonRole.ResetRole
    )
    dialog._cancel_button = dialog._buttons.addButton(dialog._tr("取消"), QDialogButtonBox.ButtonRole.RejectRole)
    dialog._apply_button.setAccessibleName(dialog._tr("应用设置"))
    dialog._default_button.setAccessibleName(dialog._tr("恢复默认主题设置"))
    dialog._cancel_button.setAccessibleName(dialog._tr("取消设置"))
    dialog._apply_button.clicked.connect(dialog.apply_draft)
    dialog._default_button.clicked.connect(dialog.restore_default)
    dialog._cancel_button.clicked.connect(dialog.reject)
    for button in (dialog._apply_button, dialog._default_button, dialog._cancel_button):
        ComponentStyle.apply_static(button, ComponentKind.BUTTON)
    dialog._apply_button.setDefault(True)
    root.addWidget(dialog._buttons)
    dialog.select_section(initial_section)
    dialog._section_list.currentRowChanged.connect(lambda _row: _sync_default_action(dialog))
    _sync_default_action(dialog)
    dialog.setTabOrder(dialog._section_list, dialog._mode_combo)
    dialog.setTabOrder(dialog._mode_combo, dialog._theme_combo)
    dialog.setTabOrder(dialog._theme_combo, dialog._api_button)
    dialog.setTabOrder(dialog._api_button, dialog._default_button)
    dialog.setTabOrder(dialog._default_button, dialog._apply_button)
    dialog.setTabOrder(dialog._apply_button, dialog._cancel_button)


def _sync_default_action(dialog: object) -> None:
    appearance_selected = dialog.current_section is SettingsSection.APPEARANCE
    dialog._default_button.setEnabled(appearance_selected)
    reason = dialog._tr("仅在外观分类中恢复主题默认值")
    dialog._default_button.setToolTip("" if appearance_selected else reason)
    dialog._default_button.setAccessibleDescription(reason)


def _appearance_page(dialog: object) -> QWidget:
    scroll = QScrollArea(dialog)
    dialog._scroll = scroll
    scroll.setWidgetResizable(True)
    configure_accessible_widget(
        scroll,
        name=dialog._tr("外观设置内容"),
        description=dialog._tr("内容过长时可滚动，操作按钮保持可见"),
    )
    content = QWidget(scroll)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    appearance = QGroupBox(dialog._tr("外观"), content)
    form = QFormLayout(appearance)
    dialog._mode_combo = QComboBox(appearance)
    configure_accessible_widget(
        dialog._mode_combo,
        name=dialog._tr("主题模式"),
        description=dialog._tr("选择跟随系统、浅色或深色"),
    )
    ComponentStyle.apply_static(dialog._mode_combo, ComponentKind.INPUT)
    for label, mode in (("跟随系统", ThemeMode.SYSTEM), ("浅色", ThemeMode.LIGHT), ("深色", ThemeMode.DARK)):
        dialog._mode_combo.addItem(dialog._tr(label), mode)
    dialog._mode_combo.currentIndexChanged.connect(dialog._on_mode_changed)
    form.addRow(dialog._tr("模式"), dialog._mode_combo)
    dialog._theme_combo = QComboBox(appearance)
    configure_accessible_widget(
        dialog._theme_combo,
        name=dialog._tr("主题提供者"),
        description=dialog._tr("选择可用的主题提供者"),
    )
    ComponentStyle.apply_static(dialog._theme_combo, ComponentKind.INPUT)
    for option in dialog._theme_options:
        dialog._theme_combo.addItem(option.display_name, option.theme_id)
    dialog._theme_combo.currentIndexChanged.connect(dialog._on_theme_changed)
    form.addRow(dialog._tr("主题"), dialog._theme_combo)
    dialog._provider_metadata = QLabel(appearance)
    dialog._provider_metadata.setWordWrap(True)
    configure_accessible_widget(dialog._provider_metadata, name=dialog._tr("主题提供者信息"))
    ComponentStyle.apply_static(dialog._provider_metadata, ComponentKind.NOTIFICATION)
    form.addRow(dialog._tr("提供者"), dialog._provider_metadata)
    dialog._effective_scheme = QLabel(appearance)
    configure_accessible_widget(dialog._effective_scheme, name=dialog._tr("当前生效配色"))
    ComponentStyle.apply_static(dialog._effective_scheme, ComponentKind.NOTIFICATION)
    form.addRow(dialog._tr("预览配色"), dialog._effective_scheme)
    layout.addWidget(appearance)
    dialog._preview = ThemePreviewWidget(content)
    layout.addWidget(dialog._preview)
    note = QLabel(dialog._tr("语言设置将在重启应用后生效；无障碍功能保持系统默认键盘行为。"), content)
    note.setWordWrap(True)
    configure_accessible_widget(note, name=dialog._tr("语言与无障碍说明"), description=note.text())
    ComponentStyle.apply_static(note, ComponentKind.NOTIFICATION)
    layout.addWidget(note)
    dialog._api_button = QPushButton(dialog._tr("打开 AI 服务设置"), content)
    configure_accessible_widget(
        dialog._api_button,
        name=dialog._tr("打开 AI 服务设置"),
        description=dialog._tr("切换到统一设置中心的 AI 服务分类"),
    )
    ComponentStyle.apply_static(dialog._api_button, ComponentKind.BUTTON)

    def open_services() -> None:
        dialog.select_section(SettingsSection.AI_SERVICE)
        dialog.service_settings_requested.emit()

    dialog._api_button.clicked.connect(open_services)
    layout.addWidget(dialog._api_button)
    dialog._feedback = QLabel(content)
    dialog._feedback.setWordWrap(True)
    configure_accessible_widget(dialog._feedback, name=dialog._tr("设置结果"), state_text=dialog._last_notice)
    ComponentStyle.apply_static(dialog._feedback, ComponentKind.NOTIFICATION)
    dialog._feedback.setText(dialog._last_notice)
    layout.addWidget(dialog._feedback)
    layout.addStretch(1)
    scroll.setWidget(content)
    return scroll


def _add_page(dialog: object, section: SettingsSection, page: QWidget) -> None:
    label = dict(SECTION_LABELS)[section]
    item = QListWidgetItem(dialog._tr(label))
    item.setData(Qt.ItemDataRole.UserRole, section.value)
    dialog._section_list.addItem(item)
    dialog._stack.addWidget(_scroll_page(page, dialog))
    dialog._settings_pages[section] = page


def _insert_page(dialog: object, section: SettingsSection, page: QWidget) -> None:
    target = next(index for index, (value, _label) in enumerate(SECTION_LABELS) if value is section)
    label = dict(SECTION_LABELS)[section]
    item = QListWidgetItem(dialog._tr(label))
    item.setData(Qt.ItemDataRole.UserRole, section.value)
    dialog._section_list.insertItem(target, item)
    dialog._stack.insertWidget(target, _scroll_page(page, dialog))
    dialog._settings_pages[section] = page


def _scroll_page(page: QWidget, parent: QWidget) -> QWidget:
    if isinstance(page, QScrollArea):
        return page
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(page)
    return scroll


def _connect_check(dialog: object, button: QPushButton, status: QLabel, prepare, idle_text: str) -> None:
    controller = SettingsConnectionController(button, status, prepare, idle_text=idle_text)
    dialog._connection_controllers.append(controller)


def _prepare_llm(page: object, config: object):
    page.apply_to_draft()
    return llm_operation(config)


def _prepare_embedding(page: object, config: object):
    page.apply_to_draft()
    return embedding_operation(config)


def _prepare_paratranz(page: object, source: object):
    page.apply_to_draft()
    draft = page._draft
    return paratranz_operation(source, draft.base_url, draft.timeout, draft.replacement_token)


def _manage_embedding_models(page: object) -> None:
    from transbridge.infra.embedding_model_store import EmbeddingModelStore
    from transbridge.ui.tools.ai_translator.embedding_model_dialog import EmbeddingModelManagerDialog

    try:
        store = EmbeddingModelStore()
        model_id = page.local_model_edit.text().strip()
        current = store.installed_path(model_id) if model_id else None
    except (KeyError, OSError, ValueError):
        page.test_status.setText("无法读取本地模型目录。")
        return

    def clear_current() -> None:
        page.local_model_edit.clear()
        page.mode_combo.setCurrentIndex(page.mode_combo.findData("disabled"))

    manager = EmbeddingModelManagerDialog(
        store,
        current_model_path=None if current is None else str(current),
        parent=page,
        on_before_remove_current=clear_current,
    )
    manager.exec()
    if manager.selected_model_id and manager.selected_model_path is not None:
        page.select_local_model(manager.selected_model_id)
        page.apply_to_draft()


__all__ = ["build_settings_center"]

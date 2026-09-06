"""Shared task composition for one or more AI translation content sources."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from transbridge.config.language_profiles import discover_language_profiles
from transbridge.ui.foundation.components import (
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    reserve_text_width,
)

from .custom_profile_view import build_custom_profile_view
from .embedding_config_view import build_embedding_config_section
from .postprocess_view import build_postprocess_view
from .scope_view import build_scope_view
from .task_sources_view import TaskSourcesView
from .task_widget_style import (
    configure_task_button,
    configure_task_footer,
    configure_task_host,
    configure_task_input,
    configure_task_list,
    configure_task_panel,
    configure_task_segment,
    configure_task_service_bar,
    configure_task_surface,
    configure_task_tabs,
    configure_task_title,
)


def build_single_task_view(
    view: object,
    parent: QWidget,
    callbacks: object,
    *,
    language_profiles: object | None = None,
) -> None:
    """Create the new task shell while retaining every legacy control contract."""

    configure_task_host(parent)
    root = QVBoxLayout(parent)
    root.setContentsMargins(18, 16, 18, 16)
    root.setSpacing(12)
    _build_header(view, parent, callbacks, root)
    _build_hidden_service_controls(view, parent, callbacks)
    profiles = discover_language_profiles() if language_profiles is None else language_profiles
    body = QHBoxLayout()
    body.setSpacing(16)
    view.sources_panel = TaskSourcesView(getattr(parent, "_ctx", None), parent)
    view.sources_panel.selection_changed.connect(getattr(callbacks, "on_sources_changed", lambda: None))
    body.addWidget(view.sources_panel)
    _build_task_tabs(view, parent, callbacks, body, profiles)
    root.addLayout(body, 1)
    _build_footer(view, parent, callbacks, root)
    _connect_task_signals(view, callbacks)


def refresh_service_summary(view: object) -> None:
    provider = "Anthropic" if view.controls.provider_combo.currentIndex() == 1 else "OpenAI 兼容"
    model = view.controls.model_edit.text().strip() or "未配置模型"
    ready = bool(view.controls.apikey_edit.text().strip() and view.controls.model_edit.text().strip())
    state = "已配置" if ready else "未配置完整"
    text = f"AI 服务：{provider} · {model} · {state}"
    view.controls.service_summary_label.set_full_text(text)
    view.controls.service_summary_label.setToolTip(text)
    view.controls.service_summary_label.setAccessibleDescription(text)
    ComponentStyle.apply_state(
        view.controls.service_summary_label,
        SemanticState.SUCCESS if ready else SemanticState.WARNING,
    )


def _build_header(view: object, parent: QWidget, callbacks: object, root: QVBoxLayout) -> None:
    title = QLabel("AI 翻译任务", parent)
    title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    configure_task_title(title)
    root.addWidget(title)

    context_text = _context_text(parent)
    view._context_label = ElidedLabel(context_text, parent)
    view._context_label.set_full_text(context_text)
    view._context_label.setToolTip(context_text)
    view._context_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    view._context_label.setAccessibleName("当前 AI 翻译工程")
    configure_task_title(view._context_label, "subtitle")
    root.addWidget(view._context_label)

    mode_surface = QFrame(parent)
    configure_task_surface(mode_surface)
    mode_surface.setProperty("tbTaskModeBar", True)
    mode_layout = QHBoxLayout(mode_surface)
    mode_layout.setContentsMargins(8, 6, 8, 6)
    mode_layout.setSpacing(6)
    view._mode_group = QButtonGroup(parent)
    for name, text in (
        ("mode_translate", "翻译"),
        ("mode_polish", "润色"),
        ("mode_mixed", "混合"),
        ("mode_custom", "自定义"),
    ):
        button = QRadioButton(text, mode_surface)
        button.setAccessibleName(f"AI 任务模式：{text}")
        configure_task_segment(button)
        setattr(view.controls, name, button)
        view._mode_group.addButton(button)
        mode_layout.addWidget(button, 1)
    view.controls.mode_translate.setChecked(True)
    root.addWidget(mode_surface)


def _build_hidden_service_controls(view: object, parent: QWidget, callbacks: object) -> None:
    """Keep widget-backed config/controller state without exposing duplicate editors."""

    state = QWidget(parent)
    state.setVisible(False)
    layout = QVBoxLayout(state)

    def row(label_text: str, widget: QWidget) -> QHBoxLayout:
        result = QHBoxLayout()
        result.addWidget(QLabel(label_text, state))
        result.addWidget(widget)
        return result

    view.controls.provider_combo = QComboBox(state)
    view.controls.provider_combo.addItems(["OpenAI 兼容", "Anthropic"])
    view.controls.provider_combo.currentIndexChanged.connect(callbacks.on_provider_changed)
    layout.addLayout(row("供应商", view.controls.provider_combo))
    view.controls.model_edit = QLineEdit(state)
    layout.addLayout(row("模型", view.controls.model_edit))
    view.controls.apikey_edit = QLineEdit(state)
    view.controls.apikey_edit.setEchoMode(QLineEdit.EchoMode.Password)
    layout.addLayout(row("API Key", view.controls.apikey_edit))
    view.controls.baseurl_edit = QLineEdit(state)
    layout.addLayout(row("Base URL", view.controls.baseurl_edit))
    view.controls.llm_test_btn = QPushButton("测试 LLM 连接", state)
    view.controls.llm_test_btn.clicked.connect(lambda: callbacks.on_test_connection("llm"))
    layout.addWidget(view.controls.llm_test_btn)
    layout.addWidget(build_embedding_config_section(view, callbacks, row))
    view.controls.advanced_btn = QPushButton("高级配置…", state)
    view.controls.advanced_btn.setCheckable(True)
    view.controls.batch_btn = QPushButton("批量翻译…", state)
    view.controls.batch_btn.setEnabled(False)  # Hidden legacy control; no second task entry.
    layout.addWidget(view.controls.advanced_btn)
    layout.addWidget(view.controls.batch_btn)
    view._compatibility_service_state = state


def _build_task_tabs(
    view: object,
    parent: QWidget,
    callbacks: object,
    root: QHBoxLayout,
    language_profiles: object,
) -> None:
    view._task_surface = QFrame(parent)
    configure_task_surface(view._task_surface)
    task_layout = QVBoxLayout(view._task_surface)
    task_layout.setContentsMargins(0, 0, 0, 0)
    task_layout.setSpacing(0)
    task_layout.addWidget(_build_naming_scheme_group(view, callbacks, view._task_surface))
    view.controls.tabs = QTabWidget(view._task_surface)
    configure_task_tabs(view.controls.tabs)
    view.controls.tabs.setAccessibleName("AI 任务配置")

    basic = QWidget(view.controls.tabs)
    basic_layout = QVBoxLayout(basic)
    basic_layout.setContentsMargins(14, 12, 14, 12)
    basic_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    language_form = QFormLayout()
    view.controls.target_lang_combo = QComboBox(basic)
    configure_task_input(view.controls.target_lang_combo)
    for profile in language_profiles:
        view.controls.target_lang_combo.addItem(f"{profile.display_name} ({profile.locale})", profile.locale)
    language_form.addRow("目标语言", view.controls.target_lang_combo)
    basic_layout.addLayout(language_form)
    build_scope_view(view, callbacks)
    basic_layout.addWidget(view._scope_filter_box)
    view.controls.custom_profile_group = build_custom_profile_view(view)
    configure_task_panel(view.controls.custom_profile_group)
    basic_layout.addWidget(view.controls.custom_profile_group)
    basic_layout.addWidget(view.controls.scope_stack)
    view.controls.tabs.addTab(_scroll_tab(basic), "基础配置")

    terms = QWidget(view.controls.tabs)
    terms_layout = QVBoxLayout(terms)
    terms_layout.setContentsMargins(14, 12, 14, 12)
    terms_layout.addWidget(_build_terms_group(view, callbacks, terms))
    terms_layout.addStretch(1)
    view.controls.tabs.addTab(_scroll_tab(terms), "术语库")

    quality = build_postprocess_view(view, attach=False)
    view.controls.tabs.addTab(_scroll_tab(quality), "质量处理")

    runtime = QWidget(view.controls.tabs)
    runtime_layout = QVBoxLayout(runtime)
    runtime_layout.setContentsMargins(14, 12, 14, 12)
    runtime_layout.addWidget(_build_runtime_group(view, runtime))
    runtime_layout.addStretch(1)
    view.controls.tabs.addTab(_scroll_tab(runtime), "运行参数")
    task_layout.addWidget(view.controls.tabs, 1)
    root.addWidget(view._task_surface, 1)


def _build_naming_scheme_group(view: object, callbacks: object, parent: QWidget) -> QGroupBox:
    group = QGroupBox("本次采用的译名方案", parent)
    configure_task_panel(group)
    layout = QVBoxLayout(group)
    row = QHBoxLayout()
    view.controls.naming_scheme_combo = QComboBox(group)
    view.controls.naming_scheme_combo.setAccessibleName("本次采用的译名方案")
    view.controls.naming_scheme_combo.addItem("保持当前译名", None)
    view.controls.naming_scheme_combo.setEnabled(False)
    configure_task_input(view.controls.naming_scheme_combo)
    row.addWidget(view.controls.naming_scheme_combo, 1)
    view.controls.naming_scheme_manage_btn = QPushButton("管理方案…", group)
    view.controls.naming_scheme_manage_btn.setAccessibleName("管理译名方案")
    view.controls.naming_scheme_manage_btn.setEnabled(False)
    configure_task_button(view.controls.naming_scheme_manage_btn)
    row.addWidget(view.controls.naming_scheme_manage_btn)
    layout.addLayout(row)
    view.controls.naming_scheme_status_label = QLabel(
        "保持项目译文中的现有译名；术语来源仍在“术语库”页设置。",
        group,
    )
    view.controls.naming_scheme_status_label.setWordWrap(True)
    view.controls.naming_scheme_status_label.setAccessibleName("译名方案说明")
    layout.addWidget(view.controls.naming_scheme_status_label)
    return group


def _build_terms_group(view: object, callbacks: object, parent: QWidget) -> QGroupBox:
    group = QGroupBox("术语来源（拖拽调整优先级）", parent)
    configure_task_panel(group)
    layout = QVBoxLayout(group)
    view.controls.priority_list = QListWidget(group)
    configure_task_list(view.controls.priority_list)
    view.controls.priority_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    view.controls.priority_list.setMaximumHeight(120)
    for source_id, source_label in (
        ("dynamic", "dynamic（动态词库）"),
        ("paratranz", "paratranz（ParaTranz 术语）"),
        ("json", "json（本地 JSON）"),
        ("csv", "csv（本地 CSV）"),
        ("excel", "excel（本地 Excel）"),
    ):
        item = QListWidgetItem(source_label)
        item.setData(Qt.ItemDataRole.UserRole, source_id)
        view.controls.priority_list.addItem(item)
    layout.addWidget(view.controls.priority_list)
    view.controls.save_term_source_as_scheme_btn = QPushButton("前往术语工作台创建译名方案…", group)
    view.controls.save_term_source_as_scheme_btn.setAccessibleName("前往术语工作台创建译名方案")
    view.controls.save_term_source_as_scheme_btn.setToolTip(
        "译名方案是项目术语资产，请在术语工作台中选择来源、预览并创建"
    )
    configure_task_button(view.controls.save_term_source_as_scheme_btn)
    view.controls.save_term_source_as_scheme_btn.clicked.connect(
        getattr(callbacks, "on_save_term_source_as_scheme", lambda: None)
    )
    layout.addWidget(view.controls.save_term_source_as_scheme_btn)
    for label, name, file_filter in (
        ("本地 JSON", "json_path_edit", "JSON 文件 (*.json)"),
        ("本地 CSV", "csv_path_edit", "CSV 文件 (*.csv)"),
        ("本地 Excel", "excel_path_edit", "Excel 文件 (*.xlsx *.xls)"),
    ):
        row = QHBoxLayout()
        row.addWidget(QLabel(label, group))
        edit = QLineEdit(group)
        configure_task_input(edit)
        edit.setPlaceholderText("可选")
        setattr(view.controls, name, edit)
        row.addWidget(edit, 1)
        browse = QPushButton("浏览", group)
        configure_task_button(browse)
        browse.clicked.connect(
            lambda _checked=False, target=edit, spec=file_filter: callbacks.browse_file(target, spec)
        )
        row.addWidget(browse)
        layout.addLayout(row)
    columns = QHBoxLayout()
    view.controls.excel_orig_col_edit = QLineEdit("A", group)
    configure_task_input(view.controls.excel_orig_col_edit)
    view.controls.excel_orig_col_edit.setMaximumWidth(60)
    view.controls.excel_trans_col_edit = QLineEdit("B", group)
    configure_task_input(view.controls.excel_trans_col_edit)
    view.controls.excel_trans_col_edit.setMaximumWidth(60)
    columns.addWidget(QLabel("Excel 原文列", group))
    columns.addWidget(view.controls.excel_orig_col_edit)
    columns.addWidget(QLabel("译文列", group))
    columns.addWidget(view.controls.excel_trans_col_edit)
    columns.addStretch(1)
    layout.addLayout(columns)
    limit_row = QHBoxLayout()
    limit_row.addWidget(QLabel("每批术语上限", group))
    view.controls.max_terms_spin = _spin(group, 10, 500, 50)
    limit_row.addWidget(view.controls.max_terms_spin)
    limit_row.addStretch(1)
    edit_terms = QPushButton("查看/编辑动态术语库", group)
    configure_task_button(edit_terms)
    edit_terms.clicked.connect(callbacks.on_view_terms)
    limit_row.addWidget(edit_terms)
    layout.addLayout(limit_row)
    return group


def _build_runtime_group(view: object, parent: QWidget) -> QGroupBox:
    group = QGroupBox("本次任务运行参数", parent)
    configure_task_panel(group)
    form = QFormLayout(group)
    view.controls.concurrent_spin = _spin(group, 1, 50, 20)
    view.controls.concurrent_spin.setToolTip("本次 AI 工作流共享的最大在途 LLM 请求数")
    form.addRow("最大并发请求", view.controls.concurrent_spin)
    view.controls.tokens_spin = _spin(group, 200, 32000, 2500, 200)
    view.controls.tokens_spin.setToolTip("每个 LLM 请求中业务内容的 Token 上限")
    form.addRow("输入 Token 上限", view.controls.tokens_spin)
    view.controls.output_tokens_spin = _spin(group, 0, 65536, 0, 256)
    view.controls.output_tokens_spin.setSpecialValueText("不限制（供应商支持时）")
    form.addRow("输出 Token 上限", view.controls.output_tokens_spin)
    return group


def _build_footer(view: object, parent: QWidget, callbacks: object, root: QVBoxLayout) -> None:
    service = QFrame(view._task_surface)
    configure_task_service_bar(service)
    service_layout = QHBoxLayout(service)
    service_layout.setContentsMargins(10, 6, 10, 6)
    view.controls.service_summary_label = ElidedLabel("AI 服务：正在读取…", service)
    view.controls.service_summary_label.setAccessibleName("AI 服务摘要")
    service_layout.addWidget(view.controls.service_summary_label, 1)
    view.controls.settings_btn = QPushButton("打开统一设置", service)
    configure_task_button(view.controls.settings_btn)
    view.controls.settings_btn.clicked.connect(getattr(callbacks, "on_open_settings", lambda: None))
    service_layout.addWidget(view.controls.settings_btn)
    view._task_surface.layout().addWidget(service)

    footer = QFrame(parent)
    configure_task_footer(footer)
    actions = QHBoxLayout(footer)
    actions.setContentsMargins(8, 12, 0, 0)
    view._history_btn = QPushButton("历史报告", footer)
    configure_task_button(view._history_btn)
    view._history_btn.clicked.connect(callbacks.on_open_history)
    actions.addWidget(view._history_btn)
    view.controls.preflight_label = ElidedLabel("正在检查运行条件…", footer)
    view.controls.preflight_label.setObjectName("aiPreflightReason")
    view.controls.preflight_label.setAccessibleName("AI 运行条件")
    ComponentStyle.apply_state(view.controls.preflight_label, SemanticState.WARNING)
    actions.addWidget(view.controls.preflight_label, 1)
    view._cancel_btn = QPushButton("取消", footer)
    configure_task_button(view._cancel_btn)
    view._cancel_btn.clicked.connect(parent.close)
    actions.addWidget(view._cancel_btn)
    view._save_preset_btn = QPushButton("保存为任务预设", footer)
    configure_task_button(view._save_preset_btn)
    view._save_preset_btn.clicked.connect(getattr(callbacks, "on_save_task_preset", lambda: None))
    actions.addWidget(view._save_preset_btn)
    view.controls.start_btn = QPushButton("▶ 开始翻译", footer)
    reserve_text_width(view.controls.start_btn, ("开始 AI 翻译", "开始 AI 任务"))
    configure_task_button(view.controls.start_btn, primary=True)
    view.controls.start_btn.clicked.connect(callbacks.on_start)
    actions.addWidget(view.controls.start_btn)
    root.addWidget(footer)


def _connect_task_signals(view: object, callbacks: object) -> None:
    for control in (
        view.controls.mode_translate,
        view.controls.mode_polish,
        view.controls.mode_mixed,
        view.controls.mode_custom,
    ):
        control.toggled.connect(lambda checked: callbacks.on_mode_changed() if checked else None)
    view.controls.overwrite_check.toggled.connect(callbacks.update_estimate)
    view.controls.overwrite_check.toggled.connect(callbacks.update_quick_run)


def _context_text(parent: QWidget) -> str:
    context = getattr(parent, "_ctx", None)
    project = str(getattr(context, "project_name", "") or "").strip()
    return f"当前工程 · {project}" if project else "选择处理内容，配置本次 AI 任务"


def _spin(parent: QWidget, minimum: int, maximum: int, value: int, step: int = 1) -> QSpinBox:
    control = QSpinBox(parent)
    configure_task_input(control)
    control.setRange(minimum, maximum)
    control.setSingleStep(step)
    control.setValue(value)
    return control


def _scroll_tab(content: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(content)
    return scroll


__all__ = ["build_single_task_view", "refresh_service_summary"]

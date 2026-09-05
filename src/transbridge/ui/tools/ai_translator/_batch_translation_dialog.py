"""Large, task-scoped batch AI translation dialog."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import (
    ComponentStyle,
    ElidedLabel,
    SemanticState,
)

from ._theme_support import AiThemeBinding, set_widget_brush
from .batch_basic_page import BatchBasicPage
from .batch_draft import BatchTranslationDraft
from .batch_plugin_list import BatchPluginList
from .batch_quality_page import BatchQualityPage
from .batch_runtime_page import BatchRuntimePage
from .batch_terms_page import BatchTermsPage
from .task_widget_style import (
    configure_task_button,
    configure_task_footer,
    configure_task_host,
    configure_task_service_bar,
    configure_task_surface,
    configure_task_tabs,
    configure_task_title,
)

if TYPE_CHECKING:
    from transbridge.config.llm import LLMConfig
    from transbridge.ui.context import AppContext, CollectionSlot

_logger = logging.getLogger(__name__)


class _BatchTranslationDialog(QDialog):
    """Compose plugin selection and detached per-run settings."""

    open_settings_requested = pyqtSignal()

    def __init__(
        self,
        ctx: AppContext,
        parent=None,
        *,
        theme_view: ThemeView | None = None,
        llm_config: LLMConfig | None = None,
        profile_repository=None,
    ) -> None:
        super().__init__(parent)
        configure_task_host(self)
        self.setObjectName("aiBatchTranslationDialog")
        self._ctx = ctx
        if profile_repository is None:
            from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository

            profile_repository = AiWorkflowProfileRepository()
        self._profile_repository = profile_repository
        self.setWindowTitle("AI 翻译任务 · 多个插件")
        self.setMinimumSize(720, 480)
        self.resize(1040, 700)
        if llm_config is None:
            from transbridge.config.llm import LLMConfig

            llm_config = LLMConfig.load_from_file()
        self._draft = BatchTranslationDraft.from_config(llm_config)
        self._active_preset_name = self._apply_selected_profile()
        self._llm_config = self._draft.config
        self._build_ui()
        self._update_status()
        self._theme_binding = AiThemeBinding(self, theme_view, self._apply_theme)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        title = QLabel("AI 翻译任务", self)
        title.setAccessibleName("批量 AI 翻译任务标题")
        configure_task_title(title)
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(title)
        project_text = self._project_text()
        self._project_label = ElidedLabel(project_text, self)
        configure_task_title(self._project_label, "subtitle")
        self._project_label.setAccessibleName("当前批量翻译工程")
        self._project_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._project_label.set_full_text(project_text)
        self._project_label.setToolTip(self._project_label.full_text)
        root.addWidget(self._project_label)
        self._preset_label = QLabel(self._preset_text(), self)
        configure_task_title(self._preset_label, "meta")
        self._preset_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._preset_label.setAccessibleName("当前批量翻译任务预设")
        root.addWidget(self._preset_label)

        body_scroll = QScrollArea(self)
        body_scroll.setWidgetResizable(True)
        body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body_scroll.setAccessibleName("批量翻译任务内容")
        body_surface = QWidget(body_scroll)
        body_surface.setProperty("tbTaskBody", True)
        body = QHBoxLayout(body_surface)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        self._plugins = BatchPluginList(self._ctx, self)
        body.addWidget(self._plugins)

        task_surface = QFrame(self)
        configure_task_surface(task_surface)
        task_surface.setObjectName("aiBatchTaskSurface")
        task_layout = QVBoxLayout(task_surface)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.setSpacing(0)
        self._tabs = QTabWidget(task_surface)
        configure_task_tabs(self._tabs)
        self._tabs.setAccessibleName("批量翻译任务配置")
        self._basic_page = BatchBasicPage(self._draft.config, self._tabs)
        self._terms_page = BatchTermsPage(self._draft.config, self._tabs)
        self._quality_page = BatchQualityPage(self._draft.config, self._tabs)
        self._runtime_page = BatchRuntimePage(self._draft.config, self._tabs)
        for page, title_text, description in (
            (self._basic_page, "基础配置", "模式、语言、范围与覆盖"),
            (self._terms_page, "术语库", "来源优先级、文件与动态术语"),
            (self._quality_page, "质量处理", "质量检查、修复、润色与裁决"),
            (self._runtime_page, "运行参数", "并发、Token 限制与执行顺序"),
        ):
            index = self._tabs.addTab(page, title_text)
            self._tabs.setTabToolTip(index, description)
        task_layout.addWidget(self._tabs, 1)

        service_bar = QFrame(task_surface)
        configure_task_service_bar(service_bar)
        service_layout = QHBoxLayout(service_bar)
        service_layout.setContentsMargins(12, 8, 12, 8)
        self._config_label = ElidedLabel(parent=service_bar)
        self._config_label.setAccessibleName("批量翻译 AI 服务状态")
        service_layout.addWidget(self._config_label, 1)
        self._settings_button = QPushButton("打开统一设置", service_bar)
        configure_task_button(self._settings_button)
        self._settings_button.setAccessibleDescription("打开统一设置的 AI 服务页面")
        self._settings_button.clicked.connect(self._open_settings)
        service_layout.addWidget(self._settings_button)
        task_layout.addWidget(service_bar)
        body.addWidget(task_surface, 1)
        body_scroll.setWidget(body_surface)
        root.addWidget(body_scroll, 1)

        footer_surface = QFrame(self)
        configure_task_footer(footer_surface)
        footer = QHBoxLayout(footer_surface)
        footer.setContentsMargins(8, 12, 0, 0)
        self._status_label = QLabel(self)
        self._status_label.setAccessibleName("批量翻译选择状态")
        self._status_label.setWordWrap(True)
        footer.addWidget(self._status_label, 1)
        self._cancel_btn = QPushButton("取消", footer_surface)
        self._save_preset_btn = QPushButton("保存为任务预设", footer_surface)
        self._ok_btn = QPushButton("开始批量翻译", footer_surface)
        for button in (self._cancel_btn, self._save_preset_btn, self._ok_btn):
            configure_task_button(button)
        configure_task_button(self._ok_btn, primary=True)
        self._ok_btn.setDefault(True)
        self._cancel_btn.clicked.connect(self.reject)
        self._save_preset_btn.clicked.connect(self._save_task_preset)
        self._ok_btn.clicked.connect(self._accept_if_ready)
        footer.addWidget(self._cancel_btn)
        footer.addWidget(self._save_preset_btn)
        footer.addWidget(self._ok_btn)
        root.addWidget(footer_surface)

        self._list = self._plugins.list
        self._btn_all = self._plugins.select_all_button
        self._btn_none = self._plugins.clear_button
        self._btn_untranslated = self._plugins.untranslated_button
        self._overwrite_check = self._basic_page.overwrite
        self._plugins.selection_changed.connect(self._update_status)
        self._basic_page.changed.connect(self._update_status)
        self.setTabOrder(self._list, self._btn_all)
        self.setTabOrder(self._btn_all, self._btn_none)
        self.setTabOrder(self._btn_none, self._btn_untranslated)
        self.setTabOrder(self._btn_untranslated, self._tabs)
        self.setTabOrder(self._tabs, self._settings_button)
        self.setTabOrder(self._settings_button, self._cancel_btn)
        self.setTabOrder(self._cancel_btn, self._save_preset_btn)
        self.setTabOrder(self._save_preset_btn, self._ok_btn)

    def get_selected_slots(self) -> list[CollectionSlot]:
        """Return selected slots in their visible drag order."""

        return self._plugins.selected_slots()

    def is_overwrite(self) -> bool:
        return self._basic_page.overwrite_enabled

    def get_llm_config(self) -> LLMConfig:
        """Map controls to a fresh execution copy without saving preferences."""

        self._sync_draft()
        return self._draft.execution_config()

    def refresh_service_summary(self, config: LLMConfig | None = None) -> None:
        """Refresh service-only fields while preserving task overrides."""

        if config is None:
            from transbridge.config.llm import LLMConfig

            config = LLMConfig.load_from_file()
        self._draft.refresh_service_from(config)
        self._llm_config = self._draft.config
        self._update_status()

    def _sync_draft(self) -> None:
        for page in (self._basic_page, self._terms_page, self._quality_page, self._runtime_page):
            page.apply_to(self._draft.config)
        self._draft.overwrite = self.is_overwrite()

    def _update_status(self, *_args) -> None:
        self._sync_draft()
        plugins, effective, untranslated = self._plugins.counts(overwrite=self.is_overwrite())
        action = "全部内容" if self.is_overwrite() else "未翻译内容"
        self._status_label.setText(f"将翻译 {plugins} 个插件，约 {effective} 条{action}")
        self._status_label.setAccessibleDescription(
            f"已选择 {plugins} 个插件；有效词条 {effective} 条；其中未翻译 {untranslated} 条"
        )
        reason = self._validation_reason(plugins, effective)
        self._ok_btn.setEnabled(reason is None)
        self._ok_btn.setToolTip(reason or "使用当前任务配置开始批量翻译")
        self._set_config_text(self._service_text())
        ComponentStyle.apply_state(
            self._config_label,
            SemanticState.SUCCESS if self._service_ready() else SemanticState.ERROR,
        )

    def _validation_reason(self, selected: int, effective: int) -> str | None:
        if selected == 0:
            return "请选择至少一个插件。"
        if effective == 0:
            return "所选插件没有符合当前范围的有效词条。"
        if any(not getattr(slot, "esp_path", None) for slot in self.get_selected_slots()):
            return "所选内容中有插件缺少 ESP/ESM/ESL 源文件。"
        if not str(self._draft.config.api_key or "").strip():
            return "请先在统一设置中配置 AI 服务 API Key。"
        if not str(self._draft.config.model or "").strip():
            return "请先在统一设置中配置 AI 模型。"
        return None

    def _service_ready(self) -> bool:
        return bool(str(self._draft.config.api_key or "").strip() and str(self._draft.config.model or "").strip())

    def _service_text(self) -> str:
        provider = "Anthropic" if self._draft.config.provider == "anthropic" else "OpenAI 兼容"
        model = str(self._draft.config.model or "未配置模型")
        state = "已配置" if self._service_ready() else "未配置完整"
        return f"AI 服务：{provider} · {model} · {state}"

    def _set_config_text(self, text: str) -> None:
        """Compatibility helper kept stable for layout regression tests."""

        self._config_label.set_full_text(text)
        self._config_label.setToolTip(text)
        self._config_label.setAccessibleDescription(text)

    def _open_settings(self) -> None:
        self.open_settings_requested.emit()
        try:
            self.refresh_service_summary()
        except Exception as exc:
            QMessageBox.warning(self, "无法刷新 AI 服务设置", str(exc))

    def _accept_if_ready(self) -> None:
        self._update_status()
        if self._ok_btn.isEnabled():
            super().accept()

    def _save_task_preset(self) -> None:
        name, accepted = QInputDialog.getText(self, "保存任务预设", "预设名称")
        if not accepted or not name.strip():
            return
        self._sync_draft()
        try:
            from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile

            profile = CustomWorkflowProfile.from_config(
                name.strip(),
                "translate",
                self._draft.execution_config(),
                description="批量 AI 翻译任务预设",
            )
            self._profile_repository.upsert(profile, select=True)
        except Exception as exc:
            QMessageBox.warning(self, "无法保存任务预设", str(exc))
            return
        self._active_preset_name = name.strip()
        self._preset_label.setText(self._preset_text())
        QMessageBox.information(self, "任务预设已保存", f"已保存“{name.strip()}”。")

    def _apply_selected_profile(self) -> str:
        selected = getattr(self._profile_repository, "selected", None)
        if not callable(selected):
            return ""
        try:
            profile = selected()
            if profile is None or getattr(profile, "base_mode", None) != "translate":
                return ""
            self._draft.config = profile.apply_to(self._draft.config)
            return str(profile.name)
        except Exception:
            _logger.warning("无法加载已选择的批量翻译任务预设", exc_info=True)
            return ""

    def _preset_text(self) -> str:
        return f"任务预设 · {self._active_preset_name or '使用全局默认'}"

    def _project_text(self) -> str:
        name = str(getattr(self._ctx, "project_name", "") or "").strip()
        if not name:
            current = getattr(self._ctx, "current_project", None)
            if isinstance(current, dict):
                name = str(current.get("name") or current.get("title") or "").strip()
        return f"当前工程 · {name or '未命名工程'}"

    def _apply_theme(self, binding: AiThemeBinding) -> None:
        set_widget_brush(self._config_label, binding.report("success" if self._service_ready() else "error"))

    @property
    def theme_revision(self) -> int:
        return self._theme_binding.revision

    def done(self, result: int) -> None:
        if hasattr(self, "_theme_binding"):
            self._theme_binding.close()
        super().done(result)


__all__ = ["_BatchTranslationDialog"]

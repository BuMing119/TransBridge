"""Qt widget construction for the AI translator facade.

The builder deliberately receives the facade instead of application services: it
only creates widgets and connects user intents.  Config/scope/run behaviour lives
in the corresponding presenters/controllers.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Protocol

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
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

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    reserve_text_width,
)
from transbridge.ui.tools.ai_translator.config_dialogs import render_paratranz_source
from transbridge.ui.tools.ai_translator.view_controls import TranslatorControls

logger = logging.getLogger(__name__)


class AITranslatorViewCallbacks(Protocol):
    def on_provider_changed(self) -> None: ...
    def on_embed_provider_changed(self) -> None: ...
    def on_test_connection(self) -> None: ...
    def browse_file(self, target: QLineEdit, file_filter: str) -> None: ...
    def on_view_terms(self) -> None: ...
    def on_open_history(self) -> None: ...
    def on_batch_start(self) -> None: ...
    def on_start(self) -> None: ...
    def on_mode_changed(self) -> None: ...
    def update_estimate(self) -> None: ...
    def update_quick_run(self) -> None: ...
    def on_pp_enable_changed(self) -> None: ...
    def on_polish_changed(self) -> None: ...


class AITranslatorView:
    """Owns all configuration-window widgets; callbacks carry user intents only."""

    def __init__(
        self,
        parent: QWidget,
        callbacks: AITranslatorViewCallbacks,
        *,
        theme_view: ThemeView | None = None,
    ) -> None:
        self.theme_view = theme_view
        self.controls = TranslatorControls(self)
        main_layout = QVBoxLayout(parent)
        main_layout.setSpacing(8)
        # ── 模式切换 ────────────────────────────────────────────────────────────
        mode_box = QHBoxLayout()
        mode_box.addWidget(QLabel("模式:"))
        self._mode_group = QButtonGroup(parent)
        self._mode_translate = QRadioButton("翻译")
        self._mode_polish = QRadioButton("润色")
        self._mode_mixed = QRadioButton("混合")
        self._mode_group.addButton(self._mode_translate)
        self._mode_group.addButton(self._mode_polish)
        self._mode_group.addButton(self._mode_mixed)
        self._mode_translate.setChecked(True)
        mode_box.addWidget(self._mode_translate)
        mode_box.addWidget(self._mode_polish)
        mode_box.addWidget(self._mode_mixed)
        mode_box.addStretch()
        main_layout.addLayout(mode_box)
        # ── LLM 配置区 ────────────────────────────────────────────────────────
        llm_box = QGroupBox("LLM 配置")
        llm_layout = QVBoxLayout(llm_box)
        llm_layout.setSpacing(4)

        def _row(label_text, widget):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(90)
            row.addWidget(lbl)
            row.addWidget(widget)
            return row

        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["OpenAI 兼容", "Anthropic"])
        self._provider_combo.currentIndexChanged.connect(callbacks.on_provider_changed)
        llm_layout.addLayout(_row("供应商:", self._provider_combo))
        self._target_lang_combo = QComboBox()
        self._target_lang_combo.addItems(["zh_CN"])
        self._target_lang_combo.setToolTip("目标语言配置，对应 data/prompts/langs/{lang}.toml")
        llm_layout.addLayout(_row("目标语言:", self._target_lang_combo))
        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("如 gpt-4o / deepseek-chat")
        llm_layout.addLayout(_row("模型名:", self._model_edit))
        self._apikey_edit = QLineEdit()
        self._apikey_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._apikey_edit.setPlaceholderText("API Key")
        llm_layout.addLayout(_row("API Key:", self._apikey_edit))
        self._baseurl_edit = QLineEdit()
        self._baseurl_edit.setPlaceholderText("https://api.openai.com/v1")
        llm_layout.addLayout(_row("Base URL:", self._baseurl_edit))
        self._concurrent_spin = QSpinBox()
        self._concurrent_spin.setRange(1, 50)
        self._concurrent_spin.setValue(20)
        llm_layout.addLayout(_row("并发数:", self._concurrent_spin))

        self._tokens_spin = QSpinBox()
        self._tokens_spin.setRange(200, 32000)
        self._tokens_spin.setSingleStep(200)
        self._tokens_spin.setValue(2500)
        llm_layout.addLayout(_row("拆批 Token:", self._tokens_spin))

        self._output_tokens_spin = QSpinBox()
        self._output_tokens_spin.setRange(0, 65536)
        self._output_tokens_spin.setSingleStep(256)
        self._output_tokens_spin.setValue(0)
        self._output_tokens_spin.setSpecialValueText("不限制（模型默认）")
        llm_layout.addLayout(_row("输出 Token:", self._output_tokens_spin))

        self._max_terms_spin = QSpinBox()
        self._max_terms_spin.setRange(10, 500)
        self._max_terms_spin.setValue(50)
        self._max_terms_spin.setToolTip("每批次发送给 LLM 的术语表上限，防止 token 超限")
        llm_layout.addLayout(_row("术语上限:", self._max_terms_spin))

        test_btn = QPushButton("测试连接")
        test_btn.setFixedWidth(100)
        test_btn.clicked.connect(callbacks.on_test_connection)
        test_row = QHBoxLayout()
        test_row.addStretch()
        test_row.addWidget(test_btn)
        llm_layout.addLayout(test_row)

        # 创建标签页
        self._tabs = QTabWidget()

        # Tab 1: LLM 与模型
        tab_llm = QWidget()
        tab_llm_layout = QVBoxLayout(tab_llm)
        tab_llm_layout.setSpacing(6)
        tab_llm_layout.addWidget(llm_box)

        # ── Embedding 配置区 ──────────────────────────────────────────────────
        embed_box = QGroupBox("语义检索配置（Embedding）")
        embed_layout = QVBoxLayout(embed_box)
        embed_layout.setSpacing(4)

        self._embed_provider_combo = QComboBox()
        self._embed_provider_combo.addItems(["本地模型（sentence-transformers）", "API 服务（OpenAI 兼容）"])
        self._embed_provider_combo.setToolTip(
            "本地模型：使用 sentence-transformers，无需网络，需要安装依赖\n"
            "API 服务：使用 OpenAI/DeepSeek/阿里云等兼容 embedding 接口"
        )
        self._embed_provider_combo.currentIndexChanged.connect(callbacks.on_embed_provider_changed)
        embed_layout.addLayout(_row("模式:", self._embed_provider_combo))

        # 本地模型配置
        self._embed_local_model_label = QLabel("模型名:")
        self._embed_local_model_label.setFixedWidth(90)
        self._embed_local_model_edit = QLineEdit()
        self._embed_local_model_edit.setPlaceholderText("paraphrase-multilingual-MiniLM-L12-v2")
        self._embed_local_model_edit.setToolTip("本地模型名称，首次使用会从 HuggingFace 下载")
        local_row = QHBoxLayout()
        local_row.addWidget(self._embed_local_model_label)
        local_row.addWidget(self._embed_local_model_edit)
        embed_layout.addLayout(local_row)

        # API 配置（OpenAI 兼容）
        self._embed_model_label = QLabel("API 模型:")
        self._embed_model_label.setFixedWidth(90)
        self._embed_model_edit = QLineEdit()
        self._embed_model_edit.setPlaceholderText("text-embedding-3-small")
        self._embed_model_edit.setToolTip("API embedding 模型名称")
        model_row = QHBoxLayout()
        model_row.addWidget(self._embed_model_label)
        model_row.addWidget(self._embed_model_edit)
        embed_layout.addLayout(model_row)

        self._embed_apikey_label = QLabel("API Key:")
        self._embed_apikey_label.setFixedWidth(90)
        self._embed_apikey_edit = QLineEdit()
        self._embed_apikey_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._embed_apikey_edit.setPlaceholderText("留空则复用上方 LLM 的 API Key")
        apikey_row = QHBoxLayout()
        apikey_row.addWidget(self._embed_apikey_label)
        apikey_row.addWidget(self._embed_apikey_edit)
        embed_layout.addLayout(apikey_row)

        self._embed_baseurl_label = QLabel("Base URL:")
        self._embed_baseurl_label.setFixedWidth(90)
        self._embed_baseurl_edit = QLineEdit()
        self._embed_baseurl_edit.setPlaceholderText("留空则复用上方 LLM 的 Base URL")
        baseurl_row = QHBoxLayout()
        baseurl_row.addWidget(self._embed_baseurl_label)
        baseurl_row.addWidget(self._embed_baseurl_edit)
        embed_layout.addLayout(baseurl_row)

        tab_llm_layout.addWidget(embed_box)
        self._batch_btn = QPushButton("批量翻译…")
        self._batch_btn.clicked.connect(callbacks.on_batch_start)
        tab_llm_layout.addWidget(self._batch_btn)
        tab_llm_layout.addStretch()
        self._tabs.addTab(tab_llm, "LLM 与模型")

        # ── 术语库配置区 ──────────────────────────────────────────────────────
        term_box = QGroupBox("术语库来源（上方优先级更高）")
        term_layout = QVBoxLayout(term_box)

        self._priority_list = QListWidget()
        self._priority_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._priority_list.setMaximumHeight(110)
        for source_name in [
            "dynamic（动态词库）",
            "paratranz（ParaTranz 术语）",
            "json（本地 JSON）",
            "csv（本地 CSV）",
            "excel（本地 Excel）",
        ]:
            self._priority_list.addItem(QListWidgetItem(source_name))
        term_layout.addWidget(self._priority_list)

        json_row = QHBoxLayout()
        json_row.addWidget(QLabel("本地 JSON:"))
        self._json_path_edit = QLineEdit()
        self._json_path_edit.setPlaceholderText("可选")
        json_row.addWidget(self._json_path_edit)
        json_browse = QPushButton("浏览")
        json_browse.setFixedWidth(52)
        json_browse.clicked.connect(lambda: callbacks.browse_file(self._json_path_edit, "JSON 文件 (*.json)"))
        json_row.addWidget(json_browse)
        term_layout.addLayout(json_row)

        csv_row = QHBoxLayout()
        csv_row.addWidget(QLabel("本地 CSV:"))
        self._csv_path_edit = QLineEdit()
        self._csv_path_edit.setPlaceholderText("可选")
        csv_row.addWidget(self._csv_path_edit)
        csv_browse = QPushButton("浏览")
        csv_browse.setFixedWidth(52)
        csv_browse.clicked.connect(lambda: callbacks.browse_file(self._csv_path_edit, "CSV 文件 (*.csv)"))
        csv_row.addWidget(csv_browse)
        term_layout.addLayout(csv_row)

        excel_row = QHBoxLayout()
        excel_row.addWidget(QLabel("本地 Excel:"))
        self._excel_path_edit = QLineEdit()
        self._excel_path_edit.setPlaceholderText("可选")
        excel_row.addWidget(self._excel_path_edit)
        excel_browse = QPushButton("浏览")
        excel_browse.setFixedWidth(52)
        excel_browse.clicked.connect(lambda: callbacks.browse_file(self._excel_path_edit, "Excel 文件 (*.xlsx *.xls)"))
        excel_row.addWidget(excel_browse)
        term_layout.addLayout(excel_row)

        excel_col_row = QHBoxLayout()
        excel_col_row.addWidget(QLabel("原文列:"))
        self._excel_orig_col_edit = QLineEdit("A")
        self._excel_orig_col_edit.setFixedWidth(40)
        excel_col_row.addWidget(self._excel_orig_col_edit)
        excel_col_row.addWidget(QLabel("译文列:"))
        self._excel_trans_col_edit = QLineEdit("B")
        self._excel_trans_col_edit.setFixedWidth(40)
        excel_col_row.addWidget(self._excel_trans_col_edit)
        excel_col_row.addStretch()
        term_layout.addLayout(excel_col_row)

        view_terms_btn = QPushButton("查看/编辑动态术语库")
        view_terms_btn.clicked.connect(callbacks.on_view_terms)
        term_layout.addWidget(view_terms_btn)

        # Tab 2: 术语库
        tab_terms = QWidget()
        tab_terms_layout = QVBoxLayout(tab_terms)
        tab_terms_layout.setSpacing(6)
        tab_terms_layout.addWidget(term_box)
        tab_terms_layout.addStretch()
        self._tabs.addTab(tab_terms, "术语库")

        from .scope_view import build_scope_view

        build_scope_view(self, callbacks)
        from .postprocess_view import build_postprocess_view

        build_postprocess_view(self)
        # ── 可滚动内容区 ──────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(6)
        scroll_layout.addWidget(self._scope_stack)
        self._advanced_btn = QPushButton("高级配置…")
        self._advanced_btn.setCheckable(True)
        self._advanced_btn.setObjectName("aiAdvancedSettingsToggle")
        self._advanced_btn.setToolTip("供应商、模型、Embedding、术语库和后处理设置")
        scroll_layout.addWidget(self._advanced_btn)
        scroll_layout.addWidget(self._tabs)
        self._tabs.setVisible(False)
        self._advanced_btn.toggled.connect(self._tabs.setVisible)
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll, stretch=1)

        # ── 底部按钮 ──────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._history_btn = QPushButton("历史报告")
        self._history_btn.clicked.connect(callbacks.on_open_history)
        btn_row.addWidget(self._history_btn)
        self._preflight_label = ElidedLabel("正在检查运行条件…")
        self._preflight_label.setObjectName("aiPreflightReason")
        preflight_font = self._preflight_label.font()
        preflight_font.setPointSize(9)
        self._preflight_label.setFont(preflight_font)
        self._preflight_label.setAccessibleName("AI 运行条件")
        ComponentStyle.apply_state(self._preflight_label, SemanticState.WARNING)
        self._preflight_label.setToolTip(self._preflight_label.full_text)
        self._preflight_label.setAccessibleDescription(self._preflight_label.full_text)
        btn_row.addWidget(self._preflight_label, 1)
        self._start_btn = QPushButton("▶ 开始翻译")
        start_font = self._start_btn.font()
        start_font.setBold(True)
        self._start_btn.setFont(start_font)
        self._start_btn.setAccessibleName("开始 AI 运行")
        ComponentStyle.apply_static(self._start_btn, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._start_btn, SemanticState.PRIMARY)
        reserve_text_width(self._start_btn, ("▶ 开始翻译", "▶ 开始润色", "▶ 开始执行"))
        self._start_btn.clicked.connect(callbacks.on_start)
        btn_row.addWidget(self._start_btn)
        main_layout.addLayout(btn_row)

        self._mode_translate.toggled.connect(callbacks.on_mode_changed)
        self._mode_polish.toggled.connect(callbacks.on_mode_changed)
        self._mode_mixed.toggled.connect(callbacks.on_mode_changed)
        self._overwrite_check.toggled.connect(callbacks.update_estimate)


class WindowConfigView:
    """Adapter exposing config fields without leaking widgets to the presenter."""

    def __init__(
        self,
        view: AITranslatorView,
        callbacks: AITranslatorViewCallbacks,
        current_project: Callable[[], object | None],
    ) -> None:
        self._view = view
        self._callbacks = callbacks
        self._current_project = current_project

    def render_config(self, cfg: object) -> None:
        h = self._view.controls
        h.provider_combo.setCurrentIndex(0 if cfg.provider != "anthropic" else 1)
        h.target_lang_combo.setCurrentText(cfg.target_lang)
        h.model_edit.setText(cfg.model)
        h.apikey_edit.setText(cfg.api_key)
        h.baseurl_edit.setText(cfg.base_url)
        h.concurrent_spin.setValue(cfg.max_concurrent)
        h.tokens_spin.setValue(cfg.max_tokens_per_batch)
        h.output_tokens_spin.setValue(cfg.max_output_tokens)
        h.max_terms_spin.setValue(cfg.max_terms_per_batch)
        h.json_path_edit.setText(cfg.local_json_path)
        h.csv_path_edit.setText(getattr(cfg, "local_csv_path", ""))
        h.excel_path_edit.setText(cfg.local_excel_path)
        h.excel_orig_col_edit.setText(cfg.excel_original_col)
        h.excel_trans_col_edit.setText(cfg.excel_translation_col)
        h.pp_enable_check.setChecked(cfg.enable_post_process)
        h.pp_consistency_check.setChecked(cfg.pp_enable_consistency_check)
        h.pp_format_check.setChecked(cfg.pp_enable_format_validation)
        h.pp_quality_gate_check.setChecked(cfg.pp_enable_quality_gate)
        h.pp_refinement_check.setChecked(cfg.pp_enable_refinement)
        h.pp_polish_check.setChecked(cfg.pp_enable_polish)
        h.pp_polish_scope_combo.setCurrentIndex({"all": 0, "passed": 1, "has_issues": 2}.get(cfg.pp_polish_scope, 0))
        h.pp_polish_level_combo.setCurrentIndex(
            {"light": 0, "moderate": 1, "aggressive": 2}.get(cfg.pp_polish_level, 1)
        )
        h.pp_arbitration_check.setChecked(cfg.pp_enable_arbitration)
        h.pp_strict_mode_check.setChecked(cfg.pp_strict_arbitration)
        h.polish_preview_check.setChecked(cfg.polish_preview_enabled)
        h.embed_provider_combo.setCurrentIndex(0 if cfg.embedding.provider == "local" else 1)
        h.embed_local_model_edit.setText(cfg.embedding.local_model_path)
        h.embed_model_edit.setText(cfg.embedding.model)
        h.embed_apikey_edit.setText(cfg.embedding.api_key)
        h.embed_baseurl_edit.setText(cfg.embedding.base_url)
        priority_map = {
            "dynamic": "dynamic（动态词库）",
            "paratranz": "paratranz（ParaTranz 术语）",
            "json": "json（本地 JSON）",
            "csv": "csv（本地 CSV）",
            "excel": "excel（本地 Excel）",
        }
        if cfg.term_priority:
            h.priority_list.clear()
            for key in cfg.term_priority:
                if key in priority_map:
                    h.priority_list.addItem(QListWidgetItem(priority_map[key]))
        self._callbacks.on_provider_changed()
        self._callbacks.on_embed_provider_changed()
        self._callbacks.on_pp_enable_changed()
        self._callbacks.update_estimate()
        render_paratranz_source(h.priority_list, self._current_project())

    def update_config(self, cfg: object) -> object:
        h = self._view.controls
        cfg.provider = "anthropic" if h.provider_combo.currentIndex() == 1 else "openai_compatible"
        cfg.target_lang = h.target_lang_combo.currentText()
        cfg.model = h.model_edit.text().strip()
        cfg.api_key = h.apikey_edit.text().strip()
        cfg.base_url = h.baseurl_edit.text().strip()
        cfg.max_concurrent = h.concurrent_spin.value()
        cfg.max_tokens_per_batch = h.tokens_spin.value()
        cfg.max_output_tokens = h.output_tokens_spin.value()
        cfg.max_terms_per_batch = h.max_terms_spin.value()
        cfg.local_json_path = h.json_path_edit.text().strip()
        cfg.local_csv_path = h.csv_path_edit.text().strip()
        cfg.local_excel_path = h.excel_path_edit.text().strip()
        cfg.excel_original_col = h.excel_orig_col_edit.text().strip() or "A"
        cfg.excel_translation_col = h.excel_trans_col_edit.text().strip() or "B"
        cfg.enable_post_process = h.pp_enable_check.isChecked()
        cfg.pp_enable_consistency_check = h.pp_consistency_check.isChecked()
        cfg.pp_enable_format_validation = h.pp_format_check.isChecked()
        cfg.pp_enable_quality_gate = h.pp_quality_gate_check.isChecked()
        cfg.pp_enable_refinement = h.pp_refinement_check.isChecked()
        cfg.pp_enable_polish = h.pp_polish_check.isChecked()
        cfg.pp_polish_scope = {0: "all", 1: "passed", 2: "has_issues"}.get(
            h.pp_polish_scope_combo.currentIndex(), "all"
        )
        cfg.pp_polish_level = {0: "light", 1: "moderate", 2: "aggressive"}.get(
            h.pp_polish_level_combo.currentIndex(), "moderate"
        )
        cfg.pp_enable_arbitration = h.pp_arbitration_check.isChecked()
        cfg.pp_strict_arbitration = h.pp_strict_mode_check.isChecked()
        cfg.polish_preview_enabled = h.polish_preview_check.isChecked()
        cfg.embedding.provider = "local" if h.embed_provider_combo.currentIndex() == 0 else "openai"
        cfg.embedding.local_model_path = h.embed_local_model_edit.text().strip()
        cfg.embedding.model = h.embed_model_edit.text().strip()
        cfg.embedding.api_key = h.embed_apikey_edit.text().strip()
        cfg.embedding.base_url = h.embed_baseurl_edit.text().strip()
        key_map = {
            "dynamic（动态词库）": "dynamic",
            "paratranz（ParaTranz 术语）": "paratranz",
            "json（本地 JSON）": "json",
            "csv（本地 CSV）": "csv",
            "excel（本地 Excel）": "excel",
        }
        cfg.term_priority = [
            key_map[h.priority_list.item(index).text()]
            for index in range(h.priority_list.count())
            if h.priority_list.item(index).text() in key_map
        ]
        return cfg


class ConfigAutosaveBinding:
    """Owns the debounced Qt connections and releases its timer on close."""

    def __init__(
        self,
        view: AITranslatorView,
        parent: QWidget,
        save_callback: Callable[[], object],
        callbacks: AITranslatorViewCallbacks,
    ) -> None:
        self._view = view
        self._callbacks = callbacks
        self._save_callback = save_callback
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._save_safely)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        h = self._view.controls
        for signal in (
            h.provider_combo.currentIndexChanged,
            h.target_lang_combo.currentIndexChanged,
            h.model_edit.textChanged,
            h.apikey_edit.textChanged,
            h.baseurl_edit.textChanged,
            h.concurrent_spin.valueChanged,
            h.tokens_spin.valueChanged,
            h.output_tokens_spin.valueChanged,
            h.max_terms_spin.valueChanged,
            h.json_path_edit.textChanged,
            h.csv_path_edit.textChanged,
            h.excel_path_edit.textChanged,
            h.excel_orig_col_edit.textChanged,
            h.excel_trans_col_edit.textChanged,
            h.priority_list.model().rowsMoved,
            h.pp_enable_check.toggled,
            h.pp_consistency_check.toggled,
            h.pp_format_check.toggled,
            h.pp_quality_gate_check.toggled,
            h.pp_refinement_check.toggled,
            h.pp_polish_check.toggled,
            h.pp_polish_scope_combo.currentIndexChanged,
            h.pp_polish_level_combo.currentIndexChanged,
            h.pp_arbitration_check.toggled,
            h.pp_strict_mode_check.toggled,
            h.polish_preview_check.toggled,
            h.embed_provider_combo.currentIndexChanged,
            h.embed_local_model_edit.textChanged,
            h.embed_model_edit.textChanged,
            h.embed_apikey_edit.textChanged,
            h.embed_baseurl_edit.textChanged,
        ):
            signal.connect(self.schedule)
        h.pp_enable_check.toggled.connect(self._callbacks.on_pp_enable_changed)
        h.pp_polish_check.toggled.connect(self._callbacks.on_polish_changed)

    def schedule(self, *_args: object) -> None:
        self._callbacks.update_quick_run()
        self._timer.start(2000)

    def close(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._save_safely()

    def _save_safely(self) -> None:
        try:
            self._save_callback()
        except Exception as exc:
            logger.warning("AI configuration autosave failed: %s", exc, exc_info=True)

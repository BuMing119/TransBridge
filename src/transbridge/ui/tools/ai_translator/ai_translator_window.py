from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QWidget

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.tools.ai_translator.config_dialogs import open_term_editor, show_connection_test
from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
from transbridge.ui.tools.ai_translator.config_view import (
    AITranslatorView,
    ConfigAutosaveBinding,
    WindowConfigView,
)
from transbridge.ui.tools.ai_translator.custom_profile_controller import CustomProfileController
from transbridge.ui.tools.ai_translator.embedding_connection_controller import EmbeddingConnectionController
from transbridge.ui.tools.ai_translator.embedding_model_controller import (
    EmbeddingModelController,
    EmbeddingWindowCallbacks,
)
from transbridge.ui.tools.ai_translator.llm_connection_controller import LlmConnectionController
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.scope_presenter import ScopePresenter, Step2ScopeAdapter
from transbridge.ui.tools.ai_translator.task_scope import TaskScope
from transbridge.ui.tools.ai_translator.view_state import TranslatorViewPort
from transbridge.ui.windowing import show_and_activate
from transbridge.ui.workbench.filters_presenter import ALL_CATEGORIES, entry_category

from ._theme_support import AiThemeBinding
from ._window_actions import (
    apply_window_theme,
    open_settings_from_window,
    update_window_quick_run,
)

if TYPE_CHECKING:
    from transbridge.application.tasks import TaskRuntime
    from transbridge.ui.context import AppContext
    from transbridge.ui.workbench.step2 import Step2PreviewWidget


class AITranslatorWindow(QWidget):
    """AI 翻译配置窗口。点击「开始翻译」后关闭自身并弹出进度窗口。"""

    progress_window_created = pyqtSignal(object)

    def __init__(
        self,
        ctx: AppContext,
        step2: Step2PreviewWidget,
        parent=None,
        *,
        task_runtime: TaskRuntime | None = None,
        theme_view: ThemeView | None = None,
        settings_requested: Callable[[], None] | None = None,
        terminology_profile_controller=None,
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self._ctx = ctx
        self._step2 = step2
        self._task_runtime = task_runtime
        self._theme_view = theme_view
        self._settings_requested = settings_requested
        self.on_open_settings = lambda: open_settings_from_window(self)
        self._scope_port = Step2ScopeAdapter(step2)
        self._task_scope = TaskScope(ctx, self._scope_port, entry_category)
        self._scope_presenter = ScopePresenter(
            collection_provider=lambda: (
                [entry for slot in self._view.sources_panel.selected_slots() for entry in (slot.collection or ())]
                if hasattr(self, "_view")
                else list(self._ctx.collection or ())
            ),
            label_projection_provider=lambda: self._ctx.entry_labels,
            category_of=entry_category,
            workbench=self._scope_port,
        )
        self.setWindowTitle("AI 翻译任务")
        self.setMinimumSize(720, 480)
        self.resize(1120, 760)
        self._view_callbacks = EmbeddingWindowCallbacks(self)
        self._view = AITranslatorView(self, self._view_callbacks, theme_view=theme_view)
        self._view_port = TranslatorViewPort(self._view)
        from transbridge.ui.tools.ai_translator.naming_scheme_controller import AiNamingSchemeBinding

        self._naming_schemes = AiNamingSchemeBinding(
            terminology_profile_controller,
            self._view.controls,
            self.request_task_refresh,
            self,
        )
        self._task_refresh_timer = QTimer(self)
        self._task_refresh_timer.setSingleShot(True)
        self._task_refresh_timer.timeout.connect(self.update_estimate)
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        config_view = WindowConfigView(self._view, self._view_callbacks, lambda: bound_paratranz_project(self._ctx))
        self._config_presenter = ConfigPresenter(config_view, task_draft=True)
        self._embedding_models = EmbeddingModelController(
            self,
            self._view,
            self._view_port,
            self._config_presenter,
            self.update_quick_run,
        )
        self._embedding_connection = EmbeddingConnectionController(
            self._view,
            self._config_presenter,
            lambda result: show_connection_test(self, result),
        )
        self._llm_connection = LlmConnectionController(
            self._view, self._config_presenter, lambda result: show_connection_test(self, result)
        )
        self._config_binding = ConfigAutosaveBinding(
            self._view,
            self,
            self._config_presenter.save,
            self,
            refresh_callback=self.request_task_refresh,
        )
        self._run_controller = RunController(task_runtime=task_runtime)
        self._theme_binding = AiThemeBinding(self, theme_view, lambda binding: apply_window_theme(self, binding))
        self._config_presenter.load()
        from transbridge.ai_translator.term_source_reader import ConfiguredTermSourceReader
        from transbridge.ui.tools.ai_translator.terminology_source_import_controller import (
            TerminologySourceImportController,
        )

        self._term_source_imports = TerminologySourceImportController(
            self,
            self._view.controls.save_term_source_as_scheme_btn,
            terminology_profile_controller,
            ConfiguredTermSourceReader,
        )
        self._view.controls.save_term_source_as_scheme_btn.setEnabled(terminology_profile_controller is not None)
        self._embedding_models.restore_managed_path()
        self._custom_profiles = CustomProfileController(
            self, self._view, self._view_port, self._config_presenter, self.on_mode_changed
        )
        self._config_binding.start()
        if self._scope_port.selected_entry_ids():
            self.on_preset("selection")
        else:
            self._reset_scope_to_default(False)
            self.update_estimate()

    @classmethod
    def open_for_translation(
        cls,
        ctx: AppContext,
        step2: Step2PreviewWidget,
        parent=None,
        *,
        task_runtime: TaskRuntime | None = None,
        theme_view: ThemeView | None = None,
        settings_requested: Callable[[], None] | None = None,
        terminology_profile_controller=None,
    ) -> QWidget | None:
        """打开统一任务，默认勾选当前内容，可扩展到多个插件。"""
        if not ctx.slots:
            QMessageBox.warning(parent, "AI 翻译", "请先加载插件。")
            return None
        window = cls(
            ctx,
            step2,
            parent,
            task_runtime=task_runtime,
            theme_view=theme_view,
            settings_requested=settings_requested,
            terminology_profile_controller=terminology_profile_controller,
        )
        show_and_activate(window)
        return window

    @classmethod
    def open_for_batch_translation(
        cls,
        ctx: AppContext,
        step2: Step2PreviewWidget,
        parent=None,
        *,
        task_runtime: TaskRuntime | None = None,
        theme_view: ThemeView | None = None,
        settings_requested=None,
        terminology_profile_controller=None,
    ) -> QWidget | None:
        """旧调用方的兼容别名；不再创建独立批量窗口。"""
        return cls.open_for_translation(
            ctx,
            step2,
            parent,
            task_runtime=task_runtime,
            theme_view=theme_view,
            settings_requested=settings_requested,
            terminology_profile_controller=terminology_profile_controller,
        )

    def on_provider_changed(self):
        self._view_port.update_provider_controls()

    def on_mode_changed(self):
        if self._view_port.selected_mode == "custom":
            self._custom_profiles.activate_selected()
        else:
            self._config_presenter.switch_preset(self._view_port.mode)
        mode = self._view_port.mode
        self._view_port.update_mode_controls()
        if mode != "mixed":
            self._reset_scope_to_default(mode == "polish")
        else:
            from .scope_presenter import TranslationScope

            self._scope_presenter.restore(TranslationScope())
            self._rebuild_scope_tags()
        self._view.controls.tabs.widget(0).verticalScrollBar().setValue(0)
        self.request_task_refresh()

    def _reset_scope_to_default(self, is_polish: bool):
        self._scope_presenter.reset_default(polish=is_polish)
        self._rebuild_scope_tags()

    def on_preset(self, preset: str):
        self._scope_presenter.select_preset(preset)
        self._rebuild_scope_tags()
        self.update_estimate()
        self.update_quick_run()

    def on_scope_stage_clicked(self, stage: int | None):
        self._scope_presenter.toggle_stage(stage)
        self._rebuild_scope_tags()
        self.update_estimate()
        self.update_quick_run()

    def on_scope_label_clicked(self, mark: str | None):
        self._scope_presenter.toggle_label(mark)
        self._rebuild_scope_tags()
        self.update_estimate()
        self.update_quick_run()

    def on_scope_category_clicked(self, category: str | None):
        self._scope_presenter.toggle_category(category)
        self._rebuild_scope_tags()
        self.update_estimate()
        self.update_quick_run()

    def _rebuild_scope_tags(self):
        from .scope_view import render_scope_tags

        collection = [e for slot in self._view.sources_panel.selected_slots() for e in (slot.collection or ())]
        render_scope_tags(
            self._view,
            self,
            entries=list(collection) if collection else [],
            state=self._scope_presenter.state,
            label_library=self._ctx.label_library,
            entry_labels=self._ctx.entry_labels,
            categories=ALL_CATEGORIES,
            category_of=entry_category,
        )

    def _build_scope_candidates(self) -> list:
        return [entry for task in self._task_sources() for entry in task.entries]

    def on_pp_enable_changed(self):
        self._view_port.update_post_process_controls()
        self.request_task_refresh()

    def on_polish_changed(self):
        self._view_port.update_polish_controls()

    def update_estimate(self):
        from .task_scope import estimate_tasks

        if not hasattr(self, "_custom_profiles"):
            return
        self._task_refresh_timer.stop()
        config = self._config_presenter.build()
        try:
            all_tasks = self._task_sources(config=config, all_sources=True)
            by_key = {task.key: task for task in all_tasks}
            by_slot = {id(slot): str(key) for key, slot in self._ctx.slots.items()}
            selected = self._view.sources_panel.selected_slots()
            if any(id(slot) not in by_slot for slot in selected):
                raise ValueError("处理来源已变化，请重新打开 AI 翻译任务。")
            tasks = tuple(by_key[by_slot[id(slot)]] for slot in selected)
            counts = {task.key: len(task.entries) for task in all_tasks}
            text = estimate_tasks(tasks, config)
        except ValueError as exc:
            text, counts = str(exc), {}
            self._view.controls.start_btn.setEnabled(False)
            self._view.controls.preflight_label.set_full_text(text)
        else:
            update_window_quick_run(self, config=config, tasks=tasks)
        self._view.controls.estimate_lbl.setText(text)
        self._view.controls.mixed_estimate_lbl.setText(text)
        self._view.sources_panel.set_counts(counts)

    def request_task_refresh(self):
        """Coalesce edits and mode hydration; compute only the final UI state on the next event turn."""
        if not hasattr(self, "_custom_profiles"):
            return
        controls = self._view.controls
        controls.start_btn.setEnabled(False)
        controls.start_btn.setText("开始 AI 翻译" if self._view_port.mode == "translate" else "开始 AI 任务")
        if not self._custom_profiles.block_unavailable_start():
            controls.preflight_label.set_full_text("正在更新本次任务范围…")
        self._task_refresh_timer.start(0)

    def update_quick_run(self):
        update_window_quick_run(self)

    def on_test_connection(self, target: str = "llm"):
        if target == "embedding":
            self._embedding_connection.start()
            return
        self._llm_connection.start()

    def browse_file(self, target_edit: QLineEdit, file_filter: str):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", file_filter)
        if path:
            target_edit.setText(path)

    def on_view_terms(self):
        open_term_editor(self, self._ctx.esp_path)

    def on_save_term_source_as_scheme(self) -> None:
        from pathlib import Path

        from transbridge.ai_translator.term_source_reader import ConfiguredTermSourceReader, TermSourceReadRequest
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        item = self._view.controls.priority_list.currentItem()
        if item is None:
            QMessageBox.information(self, "选择术语来源", "请先在列表中单击要保存的术语来源。")
            return
        source_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        config = self._config_presenter.build()
        file_path = {
            "json": config.local_json_path,
            "csv": getattr(config, "local_csv_path", ""),
            "excel": config.local_excel_path,
        }.get(source_id)
        esp_path = None
        source_label = item.text()
        if source_id == "dynamic":
            slots = [slot for slot in self._view.sources_panel.selected_slots() if getattr(slot, "esp_path", None)]
            if not slots:
                QMessageBox.warning(self, "选择动态词库", "请先在左侧勾选一个带动态词库的插件。")
                return
            slot = slots[0]
            if len(slots) > 1:
                labels = [getattr(candidate, "label", None) or Path(candidate.esp_path).stem for candidate in slots]
                choice, accepted = QInputDialog.getItem(self, "选择动态词库", "插件", labels, 0, False)
                if not accepted:
                    return
                slot = slots[labels.index(choice)]
            esp_path = slot.esp_path
            source_label = f"动态词库 · {getattr(slot, 'label', None) or Path(esp_path).stem}"
        paratranz = bound_paratranz_project(self._ctx) if source_id == "paratranz" else None
        if source_id == "paratranz" and paratranz is not None:
            source_label = f"ParaTranz 术语 · {paratranz.get('name') or paratranz['id']}"
        elif file_path:
            local_label = {"json": "本地 JSON", "csv": "本地 CSV", "excel": "本地 Excel"}[source_id]
            source_label = f"{local_label} · {Path(file_path).name}"
        request = TermSourceReadRequest(
            source_id=source_id,
            source_label=source_label,
            file_path=file_path,
            esp_path=esp_path,
            excel_original_column=config.excel_original_col,
            excel_translation_column=config.excel_translation_col,
            paratranz_project_id=None if paratranz is None else int(paratranz["id"]),
        )
        reader_factory = None
        if source_id == "paratranz":
            from transbridge.paratranz.config_manager import ParatranzConfig
            from transbridge.paratranz.terms_service import ParaTranzTermsService

            live = self._ctx.config
            frozen = ParatranzConfig(
                token=live.token,
                user_id=live.user_id,
                base_url=live.base_url,
                timeout=live.timeout,
                extra_headers=dict(live.headers),
            )

            def frozen_paratranz_reader():
                return ConfiguredTermSourceReader(lambda: ParaTranzTermsService.from_config(frozen))

            reader_factory = frozen_paratranz_reader
        self._term_source_imports.start_with_reader(
            request,
            default_name=f"{source_label} 方案",
            reader_factory=reader_factory,
        )

    def on_start(self):
        from .task_runtime import start_task

        start_task(self)

    def _on_mixed_start(self):
        self.on_start()

    def _on_polish_start(self):
        self.on_start()

    def _task_sources(self, *, config=None, all_sources=False):
        config = config or self._config_presenter.build()
        slots = list(self._ctx.slots.values()) if all_sources else self._view.sources_panel.selected_slots()
        return self._task_scope.build(
            slots,
            self._scope_presenter.state,
            mode=self._view_port.mode,
            config=config,
            overwrite=self._view_port.overwrite,
        )

    def on_sources_changed(self):
        if hasattr(self, "_custom_profiles"):
            self._rebuild_scope_tags()
            self.update_estimate()
            self.update_quick_run()

    def on_save_task_preset(self):
        from PyQt6.QtWidgets import QInputDialog

        from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile
        from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository

        name, accepted = QInputDialog.getText(self, "保存任务预设", "预设名称")
        if not accepted or not name.strip():
            return
        try:
            profile = CustomWorkflowProfile.from_config(
                name.strip(),
                self._view_port.mode,
                self._config_presenter.build(),
            )
            AiWorkflowProfileRepository().upsert(profile, select=True)
        except Exception as exc:
            QMessageBox.warning(self, "预设保存失败", str(exc))
            return
        QMessageBox.information(self, "任务预设已保存", f"已保存“{name.strip()}”。")

    def on_open_history(self):
        from .result_view import open_report_history

        open_report_history(self, self._theme_view)

    def closeEvent(self, event):
        self._task_refresh_timer.stop()
        self._config_binding.close()
        self._embedding_connection.close()
        self._llm_connection.close()
        session = getattr(self, "_version_snapshot_session", None)
        if session is not None:
            session.rollback_uncommitted()
        self._run_controller.close()
        self._theme_binding.close()
        super().closeEvent(event)

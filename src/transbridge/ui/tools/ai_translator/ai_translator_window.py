from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QLineEdit, QMessageBox, QWidget

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.tools.ai_translator.batch_runtime import TermSourceInspector, open_batch_translation
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
from transbridge.ui.tools.ai_translator.legacy_checkpoint import check_translation_checkpoint
from transbridge.ui.tools.ai_translator.llm_connection_controller import LlmConnectionController
from transbridge.ui.tools.ai_translator.quick_run_presenter import AiQuickRunPresenter
from transbridge.ui.tools.ai_translator.result_presenter import ResultPresenter
from transbridge.ui.tools.ai_translator.run_controller import RunController, try_begin_run
from transbridge.ui.tools.ai_translator.scope_presenter import ScopePresenter, Step2ScopeAdapter
from transbridge.ui.tools.ai_translator.versioned_run import (
    start_versioned_mixed,
    start_versioned_polish,
    start_versioned_translation,
)
from transbridge.ui.tools.ai_translator.view_state import TranslatorViewPort
from transbridge.ui.windowing import show_and_activate
from transbridge.ui.workbench.filters_presenter import ALL_CATEGORIES, entry_category

from ._theme_support import AiThemeBinding
from ._window_actions import (
    apply_window_theme,
    open_batch_from_window,
    open_settings_from_window,
    require_ready,
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
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self._ctx = ctx
        self._step2 = step2
        self._task_runtime = task_runtime
        self._theme_view = theme_view
        self._settings_requested = settings_requested
        self.on_open_settings = lambda: open_settings_from_window(self)
        self.on_batch_start = lambda: open_batch_from_window(self)
        self._scope_port = Step2ScopeAdapter(step2)
        self._scope_presenter = ScopePresenter(
            collection_provider=lambda: self._ctx.collection,
            label_projection_provider=lambda: self._ctx.entry_labels,
            category_of=entry_category,
            workbench=self._scope_port,
        )
        self.setWindowTitle("AI 翻译任务 · 当前内容")
        self.setMinimumSize(720, 480)
        self.resize(980, 700)
        self._view_callbacks = EmbeddingWindowCallbacks(self)
        self._view = AITranslatorView(self, self._view_callbacks, theme_view=theme_view)
        self._view_port = TranslatorViewPort(self._view)
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        config_view = WindowConfigView(self._view, self._view_callbacks, lambda: bound_paratranz_project(self._ctx))
        self._config_presenter = ConfigPresenter(config_view)
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
        )
        self._run_controller = RunController(task_runtime=task_runtime)
        self._quick_run_presenter = AiQuickRunPresenter()
        self._result_presenter = ResultPresenter()
        self._theme_binding = AiThemeBinding(self, theme_view, lambda binding: apply_window_theme(self, binding))
        self._config_presenter.load()
        self._embedding_models.restore_managed_path()
        self._custom_profiles = CustomProfileController(
            self, self._view, self._view_port, self._config_presenter, self.on_mode_changed
        )
        self._config_binding.start()
        if self._scope_port.selected_entry_ids():
            self.on_preset("selection")
        else:
            self.update_quick_run()

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
    ) -> QWidget | None:
        """直接打开当前翻译内容的 AI 快速运行页。"""
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
    ) -> QWidget | None:
        """显式打开批量翻译计划；普通 AI intent 不调用此路径。"""
        return open_batch_translation(
            ctx,
            parent,
            Step2ScopeAdapter(step2).locate_entry,
            task_runtime=task_runtime,
            theme_view=theme_view,
            settings_requested=settings_requested,
        )

    def on_provider_changed(self):
        self._view_port.update_provider_controls()

    def on_mode_changed(self):
        mode = self._view_port.mode
        if self._view_port.selected_mode == "custom":
            self._custom_profiles.activate_selected()
        else:
            self._config_presenter.switch_preset(mode)
        self._view_port.update_mode_controls()
        if mode != "mixed":
            self._reset_scope_to_default(mode == "polish")
        self.update_estimate()
        self.update_quick_run()

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

        collection = self._ctx.collection
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
        return self._scope_presenter.candidates()

    def on_pp_enable_changed(self):
        self._view_port.update_post_process_controls()

    def on_polish_changed(self):
        self._view_port.update_polish_controls()

    def update_estimate(self):
        from .scope_view import refresh_scope_estimate

        refresh_scope_estimate(self._view, self._scope_presenter, self._view_port.scope_options())

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

    def on_start(self):
        if self._view_port.mode == "mixed":
            self._on_mixed_start()
            return
        if self._view_port.mode == "polish":
            self._on_polish_start()
            return
        collection = self._ctx.collection
        if not collection:
            QMessageBox.warning(self, "翻译", "请先加载词条集合。")
            return
        cfg = self._config_presenter.build()
        candidates = self._build_scope_candidates()
        if not require_ready(self, "translate", cfg, candidates):
            return
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        if not bound_paratranz_project(self._ctx) and TermSourceInspector.all_empty(cfg, self._ctx.esp_path):
            reply = QMessageBox.question(
                self,
                "术语库为空",
                "当前未选择 ParaTranz 项目，且所有术语来源均为空。\n\n没有术语库辅助，翻译质量可能下降。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        cfg = self._config_presenter.save()
        checkpoint_run_id = check_translation_checkpoint(
            self,
            self._ctx.esp_path,
            entries=tuple(candidates),
            overwrite=self._view_port.overwrite,
        )
        checkpoint_terminology_ref = None
        if checkpoint_run_id:
            from transbridge.ai_translator.translator import ProgressCheckpoint
            from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotRef

            checkpoint = ProgressCheckpoint.load(self._ctx.esp_path)
            if checkpoint is not None and checkpoint.terminology_snapshot is not None:
                try:
                    checkpoint_terminology_ref = TerminologyRunSnapshotRef.from_dict(checkpoint.terminology_snapshot)
                except (KeyError, TypeError, ValueError) as exc:
                    QMessageBox.warning(self, "翻译断点不可恢复", f"项目术语快照身份无效：{exc}")
                    return
        request = try_begin_run(
            self._run_controller,
            "translate",
            cfg,
            candidates,
            lambda: QMessageBox.warning(self, "翻译", "已有任务正在运行，请等待完成或关闭窗口取消。"),
            on_error=lambda message: QMessageBox.warning(self, "项目术语不可用", message),
            overwrite=self._view_port.overwrite,
            esp_path=self._ctx.esp_path,
            project_id=getattr(self._ctx, "active_project_id", None),
            variant_id=getattr(self._ctx, "active_variant_id", None),
            run_id=checkpoint_run_id,
            terminology_owner=self._ctx,
            terminology_snapshot_ref=checkpoint_terminology_ref,
        )
        if request is None:
            return
        start_versioned_translation(self, request)

    def _on_mixed_start(self):
        collection = self._ctx.collection
        if not collection:
            QMessageBox.warning(self, "混合模式", "请先加载词条集合。")
            return
        rules = self._view_port.rules
        entries = list(collection)
        mixed_scope = self._scope_presenter.partition_mixed(rules, entries)
        translate_entries = list(mixed_scope.translate_entries)
        polish_entries = list(mixed_scope.polish_entries)
        if not translate_entries and not polish_entries:
            QMessageBox.warning(self, "混合模式", "当前筛选条件下无匹配条目，请调整规则。")
            return
        cfg = self._config_presenter.build()
        cfg.mixed_execution_order = self._view_port.execution_order
        cfg.action_rules = rules
        if not self._config_presenter.execution_profile().has_proofread_work:
            polish_entries = []
        if not translate_entries and not polish_entries:
            QMessageBox.warning(self, "混合模式", "当前预设未启用可执行的处理阶段。")
            return
        run_entries = []
        seen_entry_ids: set[str] = set()
        for entry in translate_entries + polish_entries:
            entry_id = str(getattr(entry, "id", getattr(entry, "key", "")))
            if entry_id not in seen_entry_ids:
                seen_entry_ids.add(entry_id)
                run_entries.append(entry)
        if not require_ready(self, "mixed", cfg, run_entries, mixed_has_translation=bool(translate_entries)):
            return
        cfg = self._config_presenter.save()
        cfg.mixed_execution_order = self._view_port.execution_order
        cfg.action_rules = rules
        self._active_mixed_polish_entries = polish_entries
        request = try_begin_run(
            self._run_controller,
            "mixed",
            cfg,
            run_entries,
            lambda: QMessageBox.warning(self, "混合模式", "已有任务正在运行，请等待完成或关闭窗口取消。"),
            on_error=lambda message: QMessageBox.warning(self, "项目术语不可用", message),
            esp_path=self._ctx.esp_path,
            project_id=getattr(self._ctx, "active_project_id", None),
            variant_id=getattr(self._ctx, "active_variant_id", None),
            terminology_owner=self._ctx,
        )
        if request is None:
            return
        start_versioned_mixed(self, request, translate_entries, polish_entries)

    def _on_mixed_finished(self, result: dict):
        from .result_view import complete_window_mixed_result

        complete_window_mixed_result(self, result)

    def _on_polish_start(self):
        collection = self._ctx.collection
        if not collection:
            QMessageBox.warning(self, "润色", "请先加载词条集合。")
            return
        cfg = self._config_presenter.build()
        candidates = self._build_scope_candidates()
        entries_with_translation = [e for e in candidates if e.translation]
        if not require_ready(self, "polish", cfg, entries_with_translation):
            return
        cfg = self._config_presenter.save()
        request = try_begin_run(
            self._run_controller,
            "polish",
            cfg,
            entries_with_translation,
            lambda: QMessageBox.warning(self, "润色", "已有任务正在运行，请等待完成或关闭窗口取消。"),
            on_error=lambda message: QMessageBox.warning(self, "项目术语不可用", message),
            esp_path=self._ctx.esp_path,
            project_id=getattr(self._ctx, "active_project_id", None),
            variant_id=getattr(self._ctx, "active_variant_id", None),
            terminology_owner=self._ctx,
        )
        if request is None:
            return
        start_versioned_polish(self, request, entries_with_translation, collection)

    def _on_polish_preview_ready(self, payload, entries, collection):
        results, decisions = payload
        self._last_polish_results = results
        self._apply_polish_results(entries, decisions, collection)

    def _on_polish_finished_direct(self, results, entries, collection):
        summary = self._result_presenter.apply_direct(collection, entries, results)
        if not self._commit_completed_ai_result():
            return
        self._ctx.collection_changed.emit(collection)
        self._show_polish_report(results, entries, summary)
        self.close()

    def _apply_polish_results(self, entries, polish_decisions, collection):
        results = self._last_polish_results if hasattr(self, "_last_polish_results") else {}
        summary = self._result_presenter.apply_decisions(
            collection,
            entries,
            polish_decisions,
            results=results,
        )
        if not self._commit_completed_ai_result():
            return
        self._ctx.collection_changed.emit(collection)
        self._show_polish_report(results, entries, summary)
        self.close()

    def _commit_completed_ai_result(self) -> bool:
        if self._version_snapshot_session is None:
            return True
        try:
            self._version_snapshot_session.mark_completed()
        except Exception as exc:
            QMessageBox.critical(self, "AI 结果提交失败", f"{exc}\n\n本次界面修改已回滚。")
            return False
        return True

    def _show_polish_report(self, results, entries, summary):
        from .result_view import show_window_polish_report

        self._report_dialog = show_window_polish_report(self, results, entries, summary)

    def on_open_history(self):
        from .result_view import open_report_history

        open_report_history(self, self._theme_view)

    def closeEvent(self, event):
        self._config_binding.close()
        self._embedding_connection.close()
        self._llm_connection.close()
        session = getattr(self, "_version_snapshot_session", None)
        if session is not None:
            session.rollback_uncommitted()
        self._run_controller.close()
        self._theme_binding.close()
        super().closeEvent(event)

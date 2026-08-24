from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFileDialog, QLineEdit, QMessageBox, QWidget

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentStyle, SemanticState
from transbridge.ui.tools.ai_translator.batch_runtime import TermSourceInspector, open_batch_translation
from transbridge.ui.tools.ai_translator.config_dialogs import open_term_editor, show_connection_test
from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
from transbridge.ui.tools.ai_translator.config_view import (
    AITranslatorView,
    ConfigAutosaveBinding,
    WindowConfigView,
)
from transbridge.ui.tools.ai_translator.legacy_checkpoint import check_translation_checkpoint
from transbridge.ui.tools.ai_translator.quick_run_presenter import AiQuickRunPresenter
from transbridge.ui.tools.ai_translator.result_presenter import ResultPresenter
from transbridge.ui.tools.ai_translator.run_controller import (
    RunController,
    create_polish_worker,
    show_polish_progress,
    start_mixed_run,
    start_translation_run,
    try_begin_run,
)
from transbridge.ui.tools.ai_translator.run_spec import preflight_ai_run
from transbridge.ui.tools.ai_translator.scope_presenter import ScopePresenter, Step2ScopeAdapter
from transbridge.ui.tools.ai_translator.view_state import TranslatorViewPort
from transbridge.ui.windowing import show_and_activate
from transbridge.ui.workbench.filters_presenter import ALL_CATEGORIES, entry_category

from ._theme_support import AiThemeBinding, set_widget_brush
from ._window_actions import apply_window_theme, open_batch_from_window, preflight_candidates, require_ready

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
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self._ctx = ctx
        self._step2 = step2
        self._task_runtime = task_runtime
        self._theme_view = theme_view
        self.on_batch_start = lambda: open_batch_from_window(self)
        self._scope_port = Step2ScopeAdapter(step2)
        self._scope_presenter = ScopePresenter(
            collection_provider=lambda: self._ctx.collection,
            label_projection_provider=lambda: self._ctx.entry_labels,
            category_of=entry_category,
            workbench=self._scope_port,
        )
        self.setWindowTitle("AI 自动翻译")
        self.resize(680, 520)
        self._view = AITranslatorView(self, self, theme_view=theme_view)
        self._view_port = TranslatorViewPort(self._view)
        from transbridge.ui.paratranz.target_context import bound_paratranz_project

        self._config_presenter = ConfigPresenter(
            WindowConfigView(self._view, self, lambda: bound_paratranz_project(self._ctx))
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
        self._config_binding.start()
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
    ) -> QWidget | None:
        """直接打开当前翻译内容的 AI 快速运行页。"""
        if not ctx.slots:
            QMessageBox.warning(parent, "AI 翻译", "请先加载插件。")
            return None
        window = cls(ctx, step2, parent, task_runtime=task_runtime, theme_view=theme_view)
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
    ) -> QWidget | None:
        """显式打开批量翻译计划；普通 AI intent 不调用此路径。"""
        return open_batch_translation(
            ctx,
            parent,
            Step2ScopeAdapter(step2).locate_entry,
            task_runtime=task_runtime,
            theme_view=theme_view,
        )

    def on_provider_changed(self):
        self._view_port.update_provider_controls()

    def on_mode_changed(self):
        """模式切换时调整UI。"""
        mode = self._view_port.mode
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

    def on_embed_provider_changed(self):
        self._view_port.update_embedding_controls()

    def on_pp_enable_changed(self):
        self._view_port.update_post_process_controls()

    def on_polish_changed(self):
        self._view_port.update_polish_controls()

    def update_estimate(self):
        from .scope_view import render_scope_estimate

        options = self._view_port.scope_options()
        estimate = self._scope_presenter.estimate(
            mode=options.mode,
            rules=options.rules,
            overwrite=options.overwrite,
            max_tokens=options.max_tokens,
        )
        render_scope_estimate(self._view, estimate)

    def update_quick_run(self):
        mode = self._view_port.mode
        cfg = self._config_presenter.build()
        candidates = preflight_candidates(self, mode)
        preflight = preflight_ai_run(
            mode,
            cfg,
            candidates,
            esp_path=self._ctx.esp_path,
        )
        estimate = (
            self._view.controls.mixed_estimate_lbl.text()
            if mode == "mixed"
            else self._view.controls.estimate_lbl.text()
        )
        active = self._run_controller.active_request
        state = self._quick_run_presenter.present(
            mode=mode,
            entry_count=len(candidates),
            estimate_text=estimate,
            overwrite=self._view_port.overwrite,
            preflight=preflight,
            active_run_id=None if active is None else active.run_id,
        )
        self._view.controls.start_btn.setEnabled(state.enabled)
        preflight_text = state.scope_summary if state.enabled else state.enabled_reason or "暂不可运行"
        self._view.controls.preflight_label.set_full_text(preflight_text)
        self._view.controls.preflight_label.setToolTip(preflight_text)
        self._view.controls.preflight_label.setAccessibleDescription(
            state.enabled_reason or state.scope_summary or "运行条件已满足"
        )
        ComponentStyle.apply_state(
            self._view.controls.preflight_label,
            SemanticState.SUCCESS if state.enabled else SemanticState.WARNING,
        )
        set_widget_brush(
            self._view.controls.preflight_label,
            self._theme_binding.report("success" if state.enabled else "warning"),
        )

    def on_test_connection(self):
        show_connection_test(self, self._config_presenter.test_connection())

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
        request = try_begin_run(
            self._run_controller,
            "translate",
            cfg,
            candidates,
            lambda: QMessageBox.warning(self, "翻译", "已有任务正在运行，请等待完成或关闭窗口取消。"),
            overwrite=self._view_port.overwrite,
            esp_path=self._ctx.esp_path,
            project_id=getattr(self._ctx, "active_project_id", None),
            variant_id=getattr(self._ctx, "active_variant_id", None),
            run_id=checkpoint_run_id,
        )
        if request is None:
            return

        try:
            start_translation_run(
                self._run_controller,
                self._ctx,
                request,
                progress_created=self.progress_window_created.emit,
                entry_activated=self._scope_presenter.locate_entry,
                theme_view=self._theme_view,
            )
        except Exception as exc:
            self._run_controller.create_activity(request).fail(str(exc))
            raise
        finally:
            self._run_controller.finish(request.run_id)
        self.close()

    def _on_mixed_start(self):
        """混合模式：规则匹配 → 拆分为翻译/润色条目 → MixedWorker（占位，S12实现）。"""
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
        run_entries = []
        seen_entry_ids: set[str] = set()
        for entry in translate_entries + polish_entries:
            entry_id = str(getattr(entry, "id", getattr(entry, "key", "")))
            if entry_id not in seen_entry_ids:
                seen_entry_ids.add(entry_id)
                run_entries.append(entry)
        if not require_ready(self, "mixed", cfg, run_entries):
            return
        cfg = self._config_presenter.save()
        cfg.mixed_execution_order = self._view_port.execution_order
        cfg.action_rules = rules
        request = try_begin_run(
            self._run_controller,
            "mixed",
            cfg,
            run_entries,
            lambda: QMessageBox.warning(self, "混合模式", "已有任务正在运行，请等待完成或关闭窗口取消。"),
            esp_path=self._ctx.esp_path,
            project_id=getattr(self._ctx, "active_project_id", None),
            variant_id=getattr(self._ctx, "active_variant_id", None),
        )
        if request is None:
            return

        start_mixed_run(
            self._run_controller,
            request,
            self._ctx,
            request.config,
            translate_entries,
            polish_entries,
            finished=self._on_mixed_finished,
            error=lambda message: QMessageBox.warning(self, "混合模式错误", message),
            progress_created=self.progress_window_created.emit,
            theme_view=self._theme_view,
        )
        started_text = f"已启动：翻译 {len(translate_entries)} 条，润色 {len(polish_entries)} 条"
        self._view.controls.preflight_label.set_full_text(started_text)
        self._view.controls.preflight_label.setToolTip(started_text)
        self._view.controls.preflight_label.setAccessibleDescription(started_text)

    def _on_mixed_finished(self, result: dict):
        QMessageBox.information(self, "混合模式", self._result_presenter.mixed_summary(result))

    def _on_polish_start(self):
        """润色模式：选中已翻译词条 → LLMPolisher → 可选预览 → 写入。"""
        collection = self._ctx.collection
        if not collection:
            QMessageBox.warning(self, "润色", "请先加载词条集合。")
            return

        cfg = self._config_presenter.build()

        # 按作用域获取有译文的条目
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
            esp_path=self._ctx.esp_path,
            project_id=getattr(self._ctx, "active_project_id", None),
            variant_id=getattr(self._ctx, "active_variant_id", None),
        )
        if request is None:
            return

        try:
            worker = create_polish_worker(self._ctx, request.config, entries_with_translation)
        except Exception as exc:
            self._run_controller.create_activity(request).fail(str(exc))
            self._run_controller.finish(request.run_id)
            raise
        preview_enabled = self._view_port.polish_preview_enabled
        self._active_polish_config = request.config
        self._active_polish_spec = request.spec
        if preview_enabled:

            def on_results(payload):
                self._on_polish_preview_ready(payload, entries_with_translation, collection)

        else:

            def on_results(results):
                self._on_polish_finished_direct(results, entries_with_translation, collection)

        show_polish_progress(
            self._run_controller,
            request,
            self,
            worker,
            entries_with_translation,
            on_results=on_results,
            preview=preview_enabled,
            theme_view=self._theme_view,
        )

    def _on_polish_preview_ready(self, payload, entries, collection):
        results, decisions = payload
        self._last_polish_results = results
        self._apply_polish_results(entries, decisions, collection)

    def _on_polish_finished_direct(self, results, entries, collection):
        summary = self._result_presenter.apply_direct(collection, entries, results)
        self._ctx.collection_changed.emit(collection)
        self._show_polish_report(
            results,
            entries,
            summary.accepted,
            summary.rejected,
            summary.failed,
        )
        self.close()

    def _apply_polish_results(self, entries, polish_decisions, collection):
        summary = self._result_presenter.apply_decisions(collection, entries, polish_decisions)
        self._ctx.collection_changed.emit(collection)
        self._show_polish_report(
            self._last_polish_results if hasattr(self, "_last_polish_results") else {},
            entries,
            summary.accepted,
            summary.rejected,
            summary.failed,
        )
        self.close()

    def _show_polish_report(self, results, entries, accepted, rejected, failed):
        from .result_view import show_window_polish_report

        self._report_dialog = show_window_polish_report(self, results, entries, accepted, rejected, failed)

    def on_open_history(self):
        from .result_view import open_report_history

        open_report_history(self, self._theme_view)

    def closeEvent(self, event):
        self._config_binding.close()
        self._run_controller.close()
        self._theme_binding.close()
        super().closeEvent(event)

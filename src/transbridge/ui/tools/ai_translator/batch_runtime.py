"""Batch AI preflight and legacy progress composition."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.windowing import show_and_activate

from .run_spec import preflight_ai_run

logger = logging.getLogger(__name__)


def open_batch_translation(
    ctx: object,
    parent: object,
    entry_activated: Callable[[str], None],
    *,
    task_runtime=None,
    theme_view: ThemeView | None = None,
):
    from PyQt6.QtWidgets import QMessageBox

    from ._batch_translation_dialog import _BatchTranslationDialog
    from ._batch_translation_progress_window import _BatchTranslationProgressWindow
    from ._batch_translation_worker import _BatchTranslationWorker
    from .run_controller import RunController

    dialog = _BatchTranslationDialog(ctx, parent, theme_view=theme_view)
    if dialog.exec() != dialog.DialogCode.Accepted:
        return None
    slots = dialog.get_selected_slots()
    config = dialog.get_llm_config()
    if not slots or not config:
        QMessageBox.warning(parent, "批量翻译", "请选择插件并配置 AI 服务。")
        return None
    entries = [entry for slot in slots for entry in (slot.collection or ())]
    esp_path = slots[0].esp_path if all(slot.esp_path for slot in slots) else None
    preflight = preflight_ai_run("batch", config, entries, esp_path=esp_path)
    if not preflight.ready:
        QMessageBox.warning(parent, "批量翻译", preflight.reason or "运行条件未满足。")
        return None
    from transbridge.ui.paratranz.target_context import bound_paratranz_project

    remote_project = bound_paratranz_project(ctx)
    if not remote_project and TermSourceInspector.all_empty(config, esp_path):
        answer = QMessageBox.question(
            parent,
            "术语库为空",
            "所有术语来源均为空，翻译质量可能下降。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
    project_id = None if remote_project is None else remote_project["id"]
    client = None
    if project_id is not None:
        from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI

        client = ParatranzTermsAPI(ctx.config)
    controller = RunController(task_runtime=task_runtime)
    request = controller.begin(
        "batch",
        config,
        entries,
        overwrite=dialog.is_overwrite(),
        esp_path=esp_path,
        project_id=None if project_id is None else str(project_id),
    )
    activity = controller.create_activity(request)
    try:
        worker = _BatchTranslationWorker(
            slots=slots,
            llm_config=request.config,
            overwrite=dialog.is_overwrite(),
            paratranz_client=client,
            project_id=project_id,
            run_id=request.run_id,
        )
        activity.bind_worker(worker)
        progress = _BatchTranslationProgressWindow(
            worker,
            ctx,
            entry_activated=entry_activated,
            activity=activity,
            theme_view=theme_view,
        )
    except Exception as exc:
        activity.fail(str(exc))
        controller.finish(request.run_id)
        raise
    controller.finish(request.run_id)
    worker.start()
    show_and_activate(progress, deferred=True)
    return progress


class TermSourceInspector:
    @staticmethod
    def all_empty(config: object, esp_path: str | None) -> bool:
        if esp_path:
            from transbridge.ai_translator.term_database import DynamicTermDatabase

            database = DynamicTermDatabase(esp_path)
            database.load()
            if database.as_list():
                return False
        from transbridge.ai_translator.term_formats import load_terms_csv, load_terms_excel, load_terms_json

        sources = (
            ("JSON", getattr(config, "local_json_path", ""), load_terms_json),
            ("CSV", getattr(config, "local_csv_path", ""), load_terms_csv),
            (
                "Excel",
                getattr(config, "local_excel_path", ""),
                lambda path: load_terms_excel(
                    path,
                    original_column=getattr(config, "excel_original_col", "A") or "A",
                    translation_column=getattr(config, "excel_translation_col", "B") or "B",
                ),
            ),
        )
        for source_name, path, loader in sources:
            if not path or not os.path.exists(path):
                continue
            try:
                if loader(path):
                    return False
            except Exception as exc:
                logger.warning("检查%s术语来源失败 %s: %s", source_name, path, exc)
        return True

    @staticmethod
    def column_index(letter: str) -> int:
        result = 0
        for character in letter.upper().strip():
            result = result * 26 + ord(character) - ord("A") + 1
        return result - 1


__all__ = ["TermSourceInspector", "open_batch_translation"]

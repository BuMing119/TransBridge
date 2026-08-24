"""Batch AI preflight and legacy progress composition."""

from __future__ import annotations

from collections.abc import Callable
import json
import os

from transbridge.ui.windowing import show_and_activate

from .run_spec import preflight_ai_run


def open_batch_translation(
    ctx: object,
    parent: object,
    entry_activated: Callable[[str], None],
    *,
    task_runtime=None,
):
    from PyQt6.QtWidgets import QMessageBox

    from ._batch_translation_dialog import _BatchTranslationDialog
    from ._batch_translation_progress_window import _BatchTranslationProgressWindow
    from ._batch_translation_worker import _BatchTranslationWorker
    from .run_controller import RunController

    dialog = _BatchTranslationDialog(ctx, parent)
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
    if not ctx.current_project and TermSourceInspector.all_empty(config, esp_path):
        answer = QMessageBox.question(
            parent,
            "术语库为空",
            "所有术语来源均为空，翻译质量可能下降。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
    project_id = ctx.current_project.get("id") if ctx.current_project else None
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
        if config.local_json_path and os.path.exists(config.local_json_path):
            try:
                with open(config.local_json_path, encoding="utf-8") as stream:
                    if json.load(stream):
                        return False
            except Exception:
                pass
        if config.local_excel_path and os.path.exists(config.local_excel_path):
            try:
                import openpyxl

                workbook = openpyxl.load_workbook(config.local_excel_path, read_only=True, data_only=True)
                sheet = workbook.active
                original = TermSourceInspector.column_index(config.excel_original_col or "A")
                translation = TermSourceInspector.column_index(config.excel_translation_col or "B")
                for row in sheet.iter_rows(min_row=2, values_only=True):
                    source = row[original] if original < len(row) else None
                    target = row[translation] if translation < len(row) else None
                    if source and target:
                        return False
            except Exception:
                pass
        return True

    @staticmethod
    def column_index(letter: str) -> int:
        result = 0
        for character in letter.upper().strip():
            result = result * 26 + ord(character) - ord("A") + 1
        return result - 1


__all__ = ["TermSourceInspector", "open_batch_translation"]

"""Batch AI preflight and legacy progress composition."""

from __future__ import annotations

from collections.abc import Callable
from copy import copy
from dataclasses import replace
import logging
import os

from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.version_persistence import VersionPersistence
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
    settings_requested: Callable[[], None] | None = None,
):
    from PyQt6.QtWidgets import QMessageBox

    from ._batch_translation_dialog import _BatchTranslationDialog
    from ._batch_translation_progress_window import _BatchTranslationProgressWindow
    from ._batch_translation_worker import _BatchTranslationWorker
    from .run_controller import RunController

    dialog = _BatchTranslationDialog(ctx, parent, theme_view=theme_view)
    if settings_requested is not None:
        dialog.open_settings_requested.connect(settings_requested)
    if dialog.exec() != dialog.DialogCode.Accepted:
        return None
    slots = dialog.get_selected_slots()
    config = dialog.get_llm_config()
    if not slots or not config:
        QMessageBox.warning(parent, "批量翻译", "请选择插件并配置 AI 服务。")
        return None
    authoritative_publisher = None
    if bool(getattr(ctx, "uses_authoritative_projection", False)):
        try:
            slots, authoritative_publisher = _prepare_authoritative_batch(ctx, slots)
        except Exception as exc:
            QMessageBox.warning(parent, "批量翻译", str(exc))
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
    try:
        request = controller.begin(
            "batch",
            config,
            entries,
            overwrite=dialog.is_overwrite(),
            esp_path=esp_path,
            project_id=None if project_id is None else str(project_id),
            terminology_owner=ctx,
        )
    except Exception as exc:
        from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotError

        if not isinstance(exc, TerminologyRunSnapshotError):
            raise
        QMessageBox.warning(parent, "项目术语不可用", str(exc))
        return None
    activity = controller.create_activity(request)
    try:
        worker = _BatchTranslationWorker(
            slots=slots,
            llm_config=request.config,
            overwrite=dialog.is_overwrite(),
            paratranz_client=client,
            project_id=project_id,
            run_id=request.run_id,
            request_budget=request.request_budget,
            terminology_binding=request.terminology_binding,
        )
        activity.bind_worker(worker)
        progress = _BatchTranslationProgressWindow(
            worker,
            ctx,
            entry_activated=entry_activated,
            activity=activity,
            theme_view=theme_view,
            completion_publisher=authoritative_publisher,
        )
    except Exception as exc:
        activity.fail(str(exc))
        controller.finish(request.run_id)
        raise
    controller.finish(request.run_id)
    worker.start()
    show_and_activate(progress, deferred=True)
    return progress


def _prepare_authoritative_batch(ctx, slots):
    """Run AI against detached projections and publish one CAS-guarded Variant change set."""

    identity = getattr(ctx, "active_version_identity", None)
    if identity is None:
        raise RuntimeError("请先打开一个工程版本。")
    if not slots:
        raise RuntimeError("请选择至少一个翻译来源。")
    originals = tuple(slots)
    seen = set()
    detached = []
    for slot in originals:
        collection = getattr(slot, "collection", None)
        if collection is None:
            raise RuntimeError(f"来源 {getattr(slot, 'label', '?')} 没有可翻译内容。")
        entries = []
        for entry in collection:
            if entry.identity in seen:
                raise RuntimeError(f"来源映射包含重复 EntryKey：{entry.identity.serialize()}")
            seen.add(entry.identity)
            entries.append(copy(entry))
        detached.append(replace(slot, collection=TranslationEntryCollection(entries)))
    persistence = VersionPersistence(ctx, identity)

    def publish(summary, *, cancelled: bool = False) -> bool:
        if cancelled or int(getattr(summary, "failed_plugins", 0)):
            return False
        if getattr(ctx, "active_version_identity", None) != identity:
            raise RuntimeError("活动工程或版本已变化，批量翻译结果未提交。")
        entries = tuple(entry for slot in detached for entry in slot.collection)
        if {entry.identity for entry in entries} != seen:
            raise RuntimeError("批量翻译结果的 EntryKey 映射已改变，结果未提交。")
        result = persistence.commit_translation(entries)
        if result is not None and not bool(getattr(result, "is_success", True)):
            diagnostics = tuple(getattr(result, "diagnostics", ()))
            message = diagnostics[0].message if diagnostics else "权威 Variant 写入失败"
            raise RuntimeError(f"批量翻译提交失败：{message}")

        states = {entry.identity: (entry.translation, entry.stage) for entry in entries}
        for slot in originals:
            projected = []
            for entry in slot.collection:
                state = states.get(entry.identity)
                if state is None:
                    raise RuntimeError("权威提交完成后，工作台来源映射已改变；请重新打开工程。")
                if (entry.translation, entry.stage) != state:
                    entry = replace(
                        entry,
                        translation=state[0],
                        stage=state[1],
                        revision=entry.revision.next(),
                    )
                projected.append(entry)
            slot.collection = TranslationEntryCollection(projected)
        return True

    return detached, publish


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

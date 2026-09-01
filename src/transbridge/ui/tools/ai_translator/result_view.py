"""Qt rendering for AI translator result reports."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import weakref

from PyQt6 import sip

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.windowing import show_and_activate

from .result_presenter import PolishApplySummary, ResultPresenter


def _qt_object_deleted(value: object) -> bool:
    try:
        return sip.isdeleted(value)
    except TypeError:
        return False


def apply_window_mixed_result(window: object, result: Mapping[str, object]) -> bool:
    """Apply mixed polish candidates, honoring the frozen preview preference."""

    entries = getattr(window, "_active_mixed_polish_entries", [])
    polish = result.get("polish")
    if polish is None:
        return False
    if not getattr(window, "_active_mixed_preview", False):
        return window._result_presenter.apply_mixed_polish(window._ctx.collection, entries, result)

    from PyQt6.QtWidgets import QDialog

    from ._polish_preview_dialog import _PolishPreviewDialog

    dialog = _PolishPreviewDialog(entries, polish.candidates, parent=window, theme_view=window._theme_view)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        summary = PolishApplySummary(
            0,
            len(entries),
            0,
            rejected_entry_ids=tuple(str(entry.id) for entry in entries),
        )
        _render_mixed_preview_report(window, result, entries, summary)
        return False
    summary = window._result_presenter.apply_decisions(
        window._ctx.collection,
        entries,
        dialog.get_results(),
        results=polish.candidates,
    )
    _render_mixed_preview_report(window, result, entries, summary)
    return summary.accepted > 0


def complete_window_mixed_result(window: object, result: dict) -> None:
    """Commit completed translation independently of the user's polish decisions."""
    from PyQt6.QtWidgets import QMessageBox

    polish_applied = apply_window_mixed_result(window, result)
    if result.get("translate") is not None or polish_applied:
        if window._version_snapshot_session is not None:
            try:
                window._version_snapshot_session.mark_completed()
            except Exception as exc:
                QMessageBox.critical(window, "AI 结果提交失败", f"{exc}\n\n本次界面修改已回滚。")
                return
        window._ctx.collection_changed.emit(window._ctx.collection)
    window._report_dialog = show_window_mixed_report(window, result)


def _render_mixed_preview_report(
    window: object,
    result: Mapping[str, object],
    entries: list,
    summary: PolishApplySummary,
) -> None:
    """Render a preview run only after the user's decisions become authoritative."""
    from transbridge.application.translation.mixed_report import build_mixed_report_snapshot
    from transbridge.application.translation.polish_report import build_polish_report_snapshot

    if not isinstance(result, dict):
        return
    spec = getattr(window, "_active_mixed_spec", None)
    if spec is None:
        return
    polish = result.get("polish")
    if polish is None:
        return
    config = getattr(window, "_active_mixed_config", None)
    run_summary = {
        "run_mode": "mixed",
        "input_fingerprint": str(getattr(spec, "input_fingerprint", "")),
        "config_digest": str(getattr(spec, "config_digest", "")),
    }
    polish_snapshot = build_polish_report_snapshot(
        polish.candidates,
        entries,
        accepted_entry_ids=summary.accepted_entry_ids,
        rejected_entry_ids=summary.rejected_entry_ids,
        failed_entry_ids=summary.failed_entry_ids,
        run_id=spec.run_id,
        polish_level=getattr(config, "pp_polish_level", None),
        run_spec_summary=run_summary,
    )
    translate = result.get("translate")
    translation_snapshot = getattr(translate, "post_process_result", None)
    snapshot = build_mixed_report_snapshot(
        translation_snapshot,
        polish_snapshot,
        run_id=spec.run_id,
        execution_order=str(getattr(config, "mixed_execution_order", "serial")),
        run_spec_summary=run_summary,
    )
    result["snapshot"] = snapshot
    result["artifacts"] = None


def show_window_mixed_report(window: object, result: dict[str, object]) -> object:
    """Open the canonical mixed report and render preview reports in the background."""

    from ._translation_report_dialog import _TranslationReportDialog

    snapshot = result.get("snapshot")
    artifacts = result.get("artifacts")
    report_error = str(result.get("report_error") or "")
    excel_path = getattr(artifacts, "excel_path", None)
    pending = snapshot is not None and artifacts is None and not report_error
    dialog = _TranslationReportDialog(
        snapshot=snapshot,
        report_path=excel_path,
        report_pending=pending,
        save_translation=_save_translation_action(window),
        theme_view=window._theme_view,
    )
    dialog.entry_activated.connect(window._scope_presenter.locate_entry)
    if artifacts is not None:
        dialog.set_report_render_result(artifacts)
    elif report_error:
        dialog.set_report_render_error(report_error)
    elif snapshot is not None:
        dialog_ref = weakref.ref(dialog)
        progress = getattr(window, "_active_mixed_progress", None)
        progress_ref = weakref.ref(progress) if progress is not None else lambda: None
        spec = getattr(window, "_active_mixed_spec", None)

        def on_rendered(rendered: object) -> None:
            result["artifacts"] = rendered
            target = dialog_ref()
            if target is not None and not _qt_object_deleted(target):
                target.set_report_render_result(rendered)
            progress_target = progress_ref()
            if progress_target is not None and not _qt_object_deleted(progress_target):
                if spec is not None:
                    from .run_controller import register_mixed_result_actions

                    register_mixed_result_actions(progress_target, spec, result)
                diagnostics = tuple(getattr(rendered, "diagnostics", ()))
                if diagnostics:
                    progress_target.set_report_diagnostics(diagnostics)

        def on_failed(message: str) -> None:
            result["report_error"] = message
            target = dialog_ref()
            if target is not None and not _qt_object_deleted(target):
                target.set_report_render_error(message)
            progress_target = progress_ref()
            if progress_target is not None and not _qt_object_deleted(progress_target):
                progress_target.set_report_diagnostics((message,))

        from ._report_render_worker import start_report_render

        dialog._report_render_worker = start_report_render(
            snapshot,
            Path(window._ctx.esp_path).stem if window._ctx.esp_path else "unknown",
            on_completed=on_rendered,
            on_failed=on_failed,
        )
    show_and_activate(dialog, deferred=True)
    return dialog


def show_polish_report(
    presenter: ResultPresenter,
    results: Mapping,
    entries: list,
    summary: PolishApplySummary,
    *,
    config: object,
    esp_path: str | None,
    entry_activated: Callable[[str], None],
    run_spec: object | None = None,
    save_translation: Callable[..., None] | None = None,
    theme_view: ThemeView | None = None,
) -> object:
    from ._translation_report_dialog import _TranslationReportDialog

    report = presenter.build_polish_report(
        results,
        entries,
        summary,
        polish_level=config.pp_polish_level or "moderate",
        esp_path=esp_path,
        run_spec=run_spec,
    )
    dialog = _TranslationReportDialog(
        snapshot=report.snapshot,
        report_pending=True,
        save_translation=save_translation,
        theme_view=theme_view,
    )
    dialog.entry_activated.connect(entry_activated)
    navigator = None
    if run_spec is not None:
        from types import SimpleNamespace

        from .result_actions import AiResultNavigator, result_action_state

        failed = tuple(
            str(entry_id) for entry_id, result in results.items() if getattr(result, "confidence", 0.0) <= 0.0
        )
        navigator = AiResultNavigator()
        dialog.result_actions = result_action_state(
            run_spec,
            result=SimpleNamespace(failed_entries=failed),
            report=None,
        )

    dialog_ref = weakref.ref(dialog)

    def on_rendered(artifacts) -> None:
        target = dialog_ref()
        if target is None or sip.isdeleted(target):
            return
        target.set_report_render_result(artifacts)
        if run_spec is not None and navigator is not None:
            artifact = navigator.register_report(run_spec, artifacts.excel_path)
            target.result_navigator = navigator
            target.result_actions = result_action_state(
                run_spec,
                result=SimpleNamespace(failed_entries=failed),
                report=artifact,
            )

    from ._report_render_worker import start_report_render

    dialog._report_render_worker = start_report_render(
        report.snapshot,
        Path(esp_path).stem if esp_path else "unknown",
        on_completed=on_rendered,
        on_failed=dialog.set_report_render_error,
    )
    # The configuration window closes immediately after this function returns.
    show_and_activate(dialog, deferred=True)
    return dialog


def show_window_polish_report(window: object, results: Mapping, entries: list, summary: PolishApplySummary):
    return show_polish_report(
        window._result_presenter,
        results,
        entries,
        summary,
        config=getattr(window, "_active_polish_config", window._config_presenter.build()),
        esp_path=window._ctx.esp_path,
        entry_activated=window._scope_presenter.locate_entry,
        run_spec=getattr(window, "_active_polish_spec", None),
        save_translation=_save_translation_action(window),
        theme_view=window._theme_view,
    )


def _save_translation_action(window: object):
    session = getattr(window, "_version_snapshot_session", None)
    return getattr(session, "save_translation", None) if bool(getattr(session, "can_save", False)) else None


def open_report_history(parent: object, theme_view: ThemeView | None) -> object:
    from ._report_history_dialog import _ReportHistoryDialog

    dialog = _ReportHistoryDialog(parent=parent, theme_view=theme_view)
    show_and_activate(dialog)
    return dialog

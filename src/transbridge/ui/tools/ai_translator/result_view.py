"""Qt rendering for AI translator result reports."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.windowing import show_and_activate

from .result_presenter import PolishApplySummary, ResultPresenter


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
    theme_view: ThemeView | None = None,
) -> object:
    from ._translation_report_dialog import _TranslationReportDialog

    report = presenter.build_polish_report(
        results,
        entries,
        summary,
        polish_level=config.pp_polish_level or "moderate",
        esp_path=esp_path,
    )
    dialog = _TranslationReportDialog(
        polish_entries=entries,
        polish_results_dict=results,
        polish_stats=report.stats,
        report_path=report.report_path,
        theme_view=theme_view,
    )
    dialog.entry_activated.connect(entry_activated)
    if run_spec is not None:
        from types import SimpleNamespace

        from .result_actions import AiResultNavigator, result_action_state

        failed = tuple(
            str(entry_id) for entry_id, result in results.items() if getattr(result, "confidence", 0.0) <= 0.0
        )
        navigator = AiResultNavigator()
        artifact = navigator.register_report(run_spec, report.report_path)
        dialog.result_navigator = navigator
        dialog.result_actions = result_action_state(
            run_spec,
            result=SimpleNamespace(failed_entries=failed),
            report=artifact,
        )
    # The configuration window closes immediately after this function returns.
    show_and_activate(dialog, deferred=True)
    return dialog


def show_window_polish_report(
    window: object, results: Mapping, entries: list, accepted: int, rejected: int, failed: int
):
    return show_polish_report(
        window._result_presenter,
        results,
        entries,
        PolishApplySummary(accepted, rejected, failed),
        config=getattr(window, "_active_polish_config", window._config_presenter.build()),
        esp_path=window._ctx.esp_path,
        entry_activated=window._scope_presenter.locate_entry,
        run_spec=getattr(window, "_active_polish_spec", None),
        theme_view=window._theme_view,
    )


def open_report_history(parent: object, theme_view: ThemeView | None) -> object:
    from ._report_history_dialog import _ReportHistoryDialog

    dialog = _ReportHistoryDialog(parent=parent, theme_view=theme_view)
    show_and_activate(dialog)
    return dialog

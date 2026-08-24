"""Qt rendering for AI translator result reports."""

from __future__ import annotations

from collections.abc import Callable, Mapping

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

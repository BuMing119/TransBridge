# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Snapshot the export selection; compare and save outside the Qt event loop."""

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from transbridge.application.terminology.query_control import QueryCancellation
from transbridge.application.terminology_profiles.comparison import compare_scheme_translations
from transbridge.infra.terminology_export import write_terminology_export
from transbridge.ui.tools.terminology.async_actions import AsyncActionRunner

from .scheme_comparison_dialog import SchemeComparisonDialog
from .write_schemes import SchemeExportRequest, comparison_sources


def install_scheme_comparison(host, edit_draft):
    def open_comparison(fields):
        if host._execution_running or host._preflight_running:
            return
        try:
            request = edit_draft(fields).request
            if not isinstance(request, SchemeExportRequest) or len(request.selected) < 2:
                raise ValueError("请至少勾选两个术语方案。")
            sources = comparison_sources(request.native.request)
        except (ValueError, RuntimeError, TypeError) as exc:
            QMessageBox.warning(host, "无法比较", str(exc))
            return
        dialog = SchemeComparisonDialog(host)
        token = QueryCancellation()
        dialog.destroyed.connect(lambda *_args: token.cancel())

        def failed(error):
            dialog.status.setText(f"操作未完成：{error}")
            dialog.export_button.setEnabled(dialog.report is not None)

        runner = AsyncActionRunner(dialog, on_error=failed)
        dialog.finished.connect(lambda: (token.cancel(), runner.close()))

        def compare(cancellation):
            cancellation.raise_if_cancelled()
            return compare_scheme_translations(
                sources, request.source.resolve(request.selected), cancellation=cancellation
            )

        def export():
            path, _ = QFileDialog.getSaveFileName(
                dialog, "导出译文差异", "术语方案译文差异.xlsx", "Excel 工作簿 (*.xlsx)"
            )
            if not path:
                return
            target = Path(path)
            if not target.suffix:
                target = target.with_suffix(".xlsx")
            report = dialog.report
            dialog.export_button.setEnabled(False)
            dialog.status.setText("正在导出完整差异工作簿…")

            def saved(count):
                dialog.export_button.setEnabled(True)
                dialog.status.setText(f"已导出 {count} 条差异：{target}")

            runner.run(lambda: write_terminology_export(target, report, cancellation=token), saved)

        dialog.export_requested.connect(export)
        dialog.show()
        runner.run_read(compare, dialog.set_report)
        dialog._runner = runner

    host.scheme_comparison_requested.connect(open_comparison)

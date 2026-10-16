"""Apply shared workbench assets once, then each retained task completion."""

from dataclasses import replace

from transbridge.application.terminology.workloads import TerminologyWorkloadType

from .view_models import business_diagnostic


class WorkbenchAssetPresentation:
    def __init__(self, window):
        self.window = window

    def apply_refresh(self, refresh):
        for workload, result in refresh.completions:
            if workload is TerminologyWorkloadType.BUILD:
                self.apply_completion(replace(refresh.assets, task_result=result), workload)
        self.apply_shared(refresh.assets)
        for workload, result in refresh.completions:
            if workload is not TerminologyWorkloadType.BUILD:
                self.apply_completion(replace(refresh.assets, task_result=result), workload)

    def apply(self, assets, workload=None):
        if workload is TerminologyWorkloadType.BUILD:
            self.apply_completion(assets, workload)
        self.apply_shared(assets)
        if workload is not TerminologyWorkloadType.BUILD:
            self.apply_completion(assets, workload)

    def apply_shared(self, assets):
        view = self.window
        context = view.presenter.context
        if context.project_id is None or context.variant_id is None:
            return
        view.bind_page("history", (context.project_id, context.variant_id))
        if assets.build_ref is not None:
            view.bind_page("conflicts", assets.build_ref)
        view._browsing.refresh()
        if assets.browse_context is not None:
            view.terms_view.source_browser.render_context(assets.browse_context)
        version_id = "" if assets.version_ref is None else assets.version_ref.version_id
        view.versions_view.set_context(version_id)
        view.workspace.set_terminology_version(version_id)
        if assets.review_counts is not None:
            count = assets.review_counts
            view.terms_view.attention_button.setText(f"异译审核（{count.unresolved_conflicts}）")
            view.terms_view.review_button.setText(f"待复核（{count.review_required_terms}）")
            view.build_view.attention_metric.setText(f"{count.total} 项")
            view.build_view.decisions.setText(
                f"{count.review_required_terms} 条术语待复核，{count.unresolved_conflicts} 组异译待决定。"
            )
            view.build_view.decisions.setVisible(count.total > 0)
            if assets.draft is not None:
                view.build_view.result_title.setText("待审核" if count.total else "草稿已确认 · 待发布")

    def apply_completion(self, assets, workload=None):
        view = self.window
        if assets.build is not None and workload is TerminologyWorkloadType.BUILD:
            view.build_view.set_summary(view.presenter.project_build(assets.build))
        result = assets.task_result
        if result is not None and str(result.freshness) == "stale":
            view.operation_notice.show_error(
                business_diagnostic("TERMINOLOGY_BUILD_STALE", "; ".join(result.diagnostics))
            )
            return
        if workload is TerminologyWorkloadType.PUBLISH:
            if view._profile_controller is not None:
                view._profile_controller.refresh()
            view.operation_notice.show_message(
                "新术语版本已发布，后续翻译将采用已发布内容。"
                if result is not None and result.committed
                else "未确认发布成功，请重新检查当前版本和任务详情。"
            )
        if workload is TerminologyWorkloadType.HISTORY_COMPARE and assets.comparison is not None:
            view.history_view.set_comparison(assets.comparison)
        if workload in (TerminologyWorkloadType.REPORT_RENDER, TerminologyWorkloadType.CHANGELOG_RENDER):
            if result is not None and result.committed and result.output_ref:
                view.reports_view.set_artifact(result.output_ref)
                view.operation_notice.show_message(f"文件已生成：{result.output_ref}")

"""Connect user tasks across browsing, editing, scheme management and publication."""

from transbridge.application.terminology.models import TermScope

from .workflow_state import TerminologyWorkflowState


class TerminologyWorkflowController:
    def __init__(self, window):
        self.window = window
        self.state = None
        self.follow_build = False
        terms = window.terms_view
        terms.workflow.edit_requested.connect(window._draft_actions.continue_editing)
        terms.workflow.publish_requested.connect(window._publish)
        terms.all_terms_button.clicked.connect(self.edit_page)
        window.draft_view.batch_requested.connect(window._draft_actions.batch)
        window.effective_view.edit_requested.connect(window._draft_actions.edit_effective)
        window.effective_view.scheme_edit_requested.connect(self.edit_scheme)
        window.effective_view.start_requested.connect(self.start)
        terms.source_browser.edit_requested.connect(self.edit_scheme)
        terms.source_browser.draft_requested.connect(self.edit_from_mapping)
        window._draft_writes.pending_changed.connect(self.saving_changed)
        window._draft_writes.drained.connect(self.save_finished)

    def render(self, assets):
        self.state = TerminologyWorkflowState.from_assets(assets)
        view = self.window
        view.terms_view.render_workflow(self.state)
        context = assets.browse_context
        view.effective_view.set_workflow(self.state, has_profile=bool(context and context.profile_id))
        view.versions_view.discard_button.setEnabled(self.state.has_draft)
        self.saving_changed(view._draft_writes.pending_count)

    def prepared(self, _draft):
        original = self.window._draft_actions.focus_original
        if original:
            filters = self.window.draft_view.filters
            filters.search.setText(original)
            filters.scope.setCurrentIndex(0)
            filters.status.setCurrentIndex(0)
            filters.apply()
        self.window.terms_view.show_draft()
        self.window._bind_existing_assets()

    def build_required(self):
        view = self.window
        view.workspace.set_current_index(0)
        view.terms_view.build_details_button.setChecked(True)
        view.operation_notice.show_message("请先点击“从来源生成草稿”。整理完成后即可新增和编辑术语，再检查影响并发布。")

    def start(self):
        if self.state is not None and self.state.has_draft:
            self.window.terms_view.show_draft()
        elif self.state is not None and self.state.has_version:
            self.window._draft_actions.continue_editing()
        elif self.window.build_view.build_button.isEnabled():
            self.window._start_build()
        else:
            self.build_required()

    def edit_page(self):
        if self.state is not None and not self.state.has_draft:
            self.window._draft_actions.continue_editing()

    def edit_scheme(self, item):
        profiles = self.window._profile_controller
        if profiles is not None:
            scope = getattr(item, "scope", None)
            if scope is None:
                scope = TermScope(item.scope_kind, item.plugin_id)
            profiles.open_manager(original=item.original, scope=scope)

    def edit_from_mapping(self):
        browser = self.window.terms_view.source_browser
        index = browser.table.currentIndex()
        original = browser.filtered.index(index.row(), 0).data() if index.isValid() else None
        self.window._draft_actions.continue_editing(original=original)

    def saving_changed(self, pending):
        ready = self.state is not None and self.state.can_publish and not pending
        self.window.terms_view.workflow.publish_button.setEnabled(ready)
        self.window.versions_view.publish_button.setEnabled(ready)
        self.window.versions_view.discard_button.setEnabled(bool(self.state and self.state.has_draft and not pending))

    def build_completed(self):
        if self.follow_build:
            self.follow_build = False
            self.window.terms_view.show_draft()

    def save_finished(self, succeeded):
        if not succeeded:
            queue = self.window._draft_writes
            self.window.operation_notice.show_message(
                f"有 {queue.failure_count} 项修改未保存：{queue.last_error}。"
                "其他修改已处理，请在草稿中重新检查失败的词条后再发布。"
            )

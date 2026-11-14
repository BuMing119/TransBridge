# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Connect user tasks across browsing, editing, scheme management and publication."""

from transbridge.application.terminology.models import TermScope

from .workflow_state import TerminologyWorkflowState


class TerminologyWorkflowController:
    def __init__(self, window):
        self.window = window
        self.state = None
        self.follow_build = False
        self.task_busy = False
        terms = window.terms_view
        terms.workflow.edit_requested.connect(window._draft_actions.continue_editing)
        terms.workflow.publish_requested.connect(window._publish)
        terms.workflow.build_requested.connect(self.open_build)
        terms.discard_button.clicked.connect(window._version_actions.discard)
        window.versions_view.workflow_requested.connect(self.continue_workflow)
        window.schemes_view.edit_requested.connect(self.edit_current)
        window.schemes_view.source_requested.connect(self.show_source)
        window.schemes_view.extract_requested.connect(self.open_build)
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
        view.schemes_view.set_project_editable(self.state.has_draft or self.state.has_version)
        context = assets.browse_context
        profiles = view._profile_controller
        selected_profile = None if profiles is None else profiles.state.selected_profile_id
        view.effective_view.set_workflow(
            self.state, has_profile=bool(context.profile_id if context is not None else selected_profile)
        )
        view.versions_view.discard_button.setEnabled(self.state.has_draft)
        self.saving_changed(view._draft_writes.pending_count)
        if self.follow_build:
            self.follow_build = False
            if self.state.has_draft and self.state.conflict_count:
                view.terms_view.show_attention()

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
        view.operation_notice.show_message(
            "编辑项目术语前需先提取并建立草稿；也可使用上方“导入术语…”创建独立译名方案。"
        )

    def edit_current(self):
        profiles = self.window._profile_controller
        if profiles is not None and profiles.state.selected_profile_id is not None:
            profiles.open_manager()
        elif self.state is not None and (self.state.has_draft or self.state.has_version):
            self.window.workspace.set_current_index(0)
            self.window._draft_actions.continue_editing()
        else:
            self.open_build()

    def show_source(self):
        self.window.workspace.set_current_index(0)
        self.window.terms_view.show_source()

    def open_build(self):
        self.window.workspace.set_current_index(0)
        self.window.terms_view.open_build()

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

    def continue_workflow(self):
        if self.state is None:
            return
        view = self.window
        view.workspace.set_current_index(0)
        action = self.state.next_action
        if action == "build":
            view.terms_view.build_details_button.setChecked(True)
            view.build_view.build_button.setFocus()
        elif action == "conflicts":
            view.terms_view.attention_button.click()
        elif action == "review":
            view.terms_view.review_button.click()
        elif action == "edit":
            view.terms_view.workflow.edit_button.click()

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
        ready = self.state is not None and self.state.can_publish and not pending and not self.task_busy
        self.window.terms_view.workflow.set_pending(pending)
        if pending:
            self.window.terms_view.workflow.show()
        if self.state is not None:
            self.window.versions_view.render_workflow(self.state, pending)
        self.window.terms_view.workflow.publish_button.setEnabled(ready)
        self.window.versions_view.publish_button.setEnabled(ready)
        can_discard = bool(self.state and self.state.has_draft and not pending and not self.task_busy)
        self.window.versions_view.discard_button.setEnabled(can_discard)
        self.window.terms_view.discard_button.setEnabled(can_discard)

    def set_task_busy(self, busy):
        self.task_busy = busy
        self.saving_changed(self.window._draft_writes.pending_count)

    def build_completed(self):
        self.follow_build = True

    def save_finished(self, succeeded):
        if not succeeded:
            queue = self.window._draft_writes
            self.window.operation_notice.show_message(
                f"有 {queue.failure_count} 项修改未保存：{queue.last_error}。"
                "其他修改已处理，请在草稿中重新检查失败的词条后再发布。"
            )

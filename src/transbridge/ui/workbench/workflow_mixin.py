"""Small composition mixin for Story-07 Workbench workflow presentation."""

from __future__ import annotations

from transbridge.ui.workbench.workflow_presenter import WorkbenchContentKind


class WorkflowPresentationMixin:
    """Wire explicit Workbench views without adding business rules to Step2."""

    def _on_summary_filter_requested(self, key: str) -> None:
        state = self._summary.filter_state(key, self._filters_view.state())
        self._filters_view.apply_state(state)
        self._focus_labeled = self._filters_view.focus_labeled
        self._build_category_tags()
        self._build_stage_tags()
        self._build_label_tags()
        self._populate_table()

    def _update_workflow_actions(self) -> None:
        has_context = bool(getattr(self._ctx, "project_name", None) or self._entries)
        content_kind = self._content_kind()
        states = self._workflow_presenter.actions(
            has_context=has_context,
            visible_entries=self._filtered_total,
            needs_review=self._summary.needs_review,
            write_supported=self._workflow_presenter.supports_write(content_kind),
        )
        selected_count = len(self.selected_row_entry_ids())
        self._workflow_actions.set_scope_count(selected_count or self._filtered_total)
        self._workflow_actions.set_actions(states)

    def _on_table_selection_changed(self, *_args) -> None:
        self._update_workflow_actions()

    def _content_kind(self) -> WorkbenchContentKind:
        sources = getattr(self._ctx, "project_sources", ())
        if sources:
            source = next((item for item in sources if item.get("role") == "primary"), sources[0])
            format_id = str(source.get("format_id") or "").lower()
            return self._workflow_presenter.content_kind(format_id)
        slot = getattr(self._ctx, "active_slot", None)
        if slot is not None:
            if getattr(slot, "plugin", None) is not None or getattr(slot, "esp_path", None):
                return WorkbenchContentKind.PLUGIN
            if getattr(slot, "strings_path", None):
                return WorkbenchContentKind.LOCALIZED_STRINGS
            if any(getattr(slot, name, None) for name in ("eet_path", "xt_path", "sst_path")):
                return WorkbenchContentKind.TRANSLATION_FILE
        return WorkbenchContentKind.GENERIC


__all__ = ["WorkflowPresentationMixin"]

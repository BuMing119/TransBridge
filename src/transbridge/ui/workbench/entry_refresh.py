"""Apply small editor changes without discarding the Workbench table's view state."""

from dataclasses import fields

from PyQt6.QtCore import QItemSelectionModel, Qt

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.project_labels import entry_label_key

from .translation_table_columns import COL_KEY
from .workflow_presenter import StatisticsSummary

_STABLE_FIELDS = tuple(
    field.name
    for field in fields(TranslationEntry)
    if field.compare and field.name not in {"translation", "stage", "revision", "provenance"}
)


class EntryRefresh:
    """Own the editor-to-preview update and return boundary.

    Full filtering and sorting are checked before choosing the bounded row patch.
    Changes to membership, ordering, source data or derived terminology retain the
    existing full render path. Pending batches read the replacement session.
    """

    def __init__(self, preview):
        self.preview = preview

    def try_refresh(self, collection) -> bool:
        view = self.preview
        if collection is None or view._terminology_profile is not None:
            return False
        entries = list(collection)
        if not entries or len(entries) != len(view._entries):
            return False
        changes = []
        for before, after in zip(view._entries, entries, strict=True):
            if before == after:
                continue
            if any(getattr(before, name) != getattr(after, name) for name in _STABLE_FIELDS):
                return False
            changes.append((before, after))
            if len(changes) > 64:
                return False
        # Legacy in-place edits cannot be distinguished from unchanged objects.
        # They retain the original refresh, which also covers non-entry UI state.
        if not changes:
            return False
        view._filters_presenter.update(view._filters_view.state())
        filtered = tuple(view._filters_presenter.apply(entries, view._entry_labels))
        table = view._table
        if table._closed:
            return False
        if tuple(entry.identity for entry in filtered) != tuple(entry.identity for entry in view._render_entries):
            return False
        if table._sorting.order(filtered, view._entry_labels) != table._display_order:
            return False
        # Ambiguous legacy IDs still require a full render.
        ids = [entry_label_key(entry) for entry in filtered]
        if len(set(ids)) != len(ids):
            return False
        projection = getattr(view._ctx, "project_projection", None)
        snapshot = projection.snapshot() if projection is not None else None
        session = view._table_presenter.replace_entries(
            filtered, projection_revision=getattr(snapshot, "revision", None)
        )
        table._session = session
        view._entries = entries
        view._render_entries = filtered
        view._summary = StatisticsSummary.from_entries(entries)
        view._summary_view.set_summary(view._summary)
        if any(before.stage != after.stage for before, after in changes):
            view._build_stage_tags()
        visible_keys = {entry.identity for entry in filtered}
        for _, entry in changes:
            if entry.identity in visible_keys:
                table.update_rendered_entry(entry)
        view._update_workflow_actions()
        return True

    def navigation_keys(self):
        table = self.preview._table
        return tuple(table.render_session.entries[row].identity for row in table._display_order)

    def return_to_preview(self, key) -> None:
        """Restore keyboard focus without replacing filters, selection or scroll."""
        table = self.preview._table
        for row, source_row in enumerate(table._display_order):
            if table.render_session.entries[source_row].identity == key:
                if row < table.rowCount():
                    auto_scroll = table.hasAutoScroll()
                    table.setAutoScroll(False)
                    try:
                        table.selectionModel().setCurrentIndex(
                            table.model().index(row, COL_KEY), QItemSelectionModel.SelectionFlag.NoUpdate
                        )
                    finally:
                        table.setAutoScroll(auto_scroll)
                else:
                    table._pending_current_entry_id = table.render_session.entries[source_row].id
                break
        table.setFocus(Qt.FocusReason.OtherFocusReason)

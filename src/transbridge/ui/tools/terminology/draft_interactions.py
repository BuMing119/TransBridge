"""User decisions for terminology drafts, separate from window lifecycle."""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from transbridge.application.terminology.identity import normalize_original
from transbridge.application.terminology.models import DecisionStatus, TermScope

from .conflict_review_dialog import ConflictReviewDialog
from .term_edit_dialog import TermEditDialog


class DraftInteractionController:
    def __init__(
        self, parent, presenter, apply_command, run_read=None, *, on_prepared=None, on_build_required=None
    ) -> None:
        self._parent = parent
        self.presenter = presenter
        self._run_draft_command = apply_command
        self._run_read = run_read
        self._on_prepared = on_prepared
        self._on_build_required = on_build_required
        self.focus_original: str | None = None

    def add(self) -> None:
        self.focus_original = None
        self._prepare(lambda draft: self._open_editor(draft))

    def continue_editing(self, original: str | None = None) -> None:
        self.focus_original = original
        self._prepare(lambda _draft: None)

    def edit(self, decision: object) -> None:
        if not getattr(decision, "term_id", ""):
            return
        self.focus_original = getattr(decision, "original", None)
        self._prepare(lambda draft: self._edit_ready(draft, decision))

    def edit_effective(self, decision: object) -> None:
        self.focus_original = getattr(decision, "original", None)
        self._prepare(lambda draft: self._edit_ready(draft, decision, follow_replacements=True))

    def _prepare(self, next_step) -> None:
        if self._run_read is None:
            raise RuntimeError("术语编辑需要异步准备草稿")

        def ready(draft):
            if draft is None:
                QMessageBox.information(
                    self._parent,
                    "先创建术语草稿",
                    "当前还没有术语草稿或已发布版本。请先从已登记的来源生成草稿，再新增或编辑术语。",
                )
                if self._on_build_required is not None:
                    self._on_build_required()
                return
            if self._on_prepared is not None:
                self._on_prepared(draft)
            next_step(draft)

        self._run_read(self.presenter.term_editing.prepare_draft, ready, "正在准备可编辑的术语草稿…")

    def _edit_ready(self, draft, decision, *, follow_replacements=False) -> None:
        current = _find_current(draft, decision, follow_replacements=follow_replacements)
        if current is None:
            QMessageBox.information(self._parent, "词条已变化", "草稿中已找不到此词条，请刷新草稿后重新选择。")
            return
        if getattr(current, "status", None) is DecisionStatus.UNRESOLVED:
            QMessageBox.information(
                self._parent, "先审核异译", "此词条尚未决定采用哪种译名，请先在“异译审核”中查看来源并作出选择。"
            )
            return
        self._open_editor(draft, current)

    def _open_editor(self, draft, decision=None) -> None:
        plugins = {item.scope.plugin_id for item in draft.decisions if item.scope.plugin_id}
        snapshot = getattr(self.presenter, "snapshot", None)
        if snapshot is not None:
            plugins.update(
                source.registration.plugin_scope for source in snapshot.sources if source.registration.plugin_scope
            )
        dialog = TermEditDialog(decision, self._parent, plugin_ids=plugins)

        def save(values):
            fields = dict(
                original=values.original,
                translation=values.translation,
                scope=values.scope,
                variants=values.variants,
                notes=values.notes,
                expected_ref=draft.ref,
            )
            command = (
                (lambda: self.presenter.term_editing.add_term(**fields))
                if decision is None
                else (lambda: self.presenter.term_editing.edit_term(decision.term_id, **fields))
            )

            def saved(result):
                self.focus_original = values.original
                if self._on_prepared is not None:
                    self._on_prepared(result)
                dialog.saved(result)

            self._run_draft_command(command, on_saved=saved, on_error=dialog.save_failed)

        dialog.save_requested.connect(save)
        dialog.exec()

    def toggle_suppression(self, decision: object) -> None:
        operation = "reenable" if getattr(decision, "suppressed", False) else "suppress"
        self.batch((decision,), operation)

    def batch(self, decisions: object, operation: str) -> None:
        items = tuple(decisions)
        if not items or operation not in {"confirm", "suppress", "reenable"}:
            return
        self.focus_original = None
        self._prepare(lambda draft: self._batch_ready(draft, items, operation))

    def _batch_ready(self, draft, selected, operation) -> None:
        identities = tuple(item.term_id for item in selected)
        lookup = {item.term_id: item for item in draft.decisions}
        if any(identity not in lookup for identity in identities):
            QMessageBox.information(self._parent, "词条已变化", "部分选中词条已变化，请刷新草稿后重新选择。")
            return
        items = tuple(lookup[identity] for identity in identities)
        if any(before != current for before, current in zip(selected, items, strict=True)):
            QMessageBox.information(
                self._parent, "词条已变化", "部分词条在选择后发生了变化。请查看最新草稿后重新选择。"
            )
            return
        if operation == "confirm" and any(item.suppressed for item in items):
            QMessageBox.information(
                self._parent, "先启用术语", "所选词条包含已停用术语。请先使用“启用”，再确认其译名。"
            )
            return
        if operation != "suppress" and any(item.status is DecisionStatus.UNRESOLVED for item in items):
            QMessageBox.information(
                self._parent,
                "先审核异译",
                "所选词条包含尚未决定的异译。请先在“异译审核”中决定译名；批量操作不会替你选择。",
            )
            return
        action = {"confirm": "确认当前译名", "suppress": "停用", "reenable": "启用"}[operation]
        effect = {
            "confirm": "将保留每条词条当前填写的译名并完成复核。",
            "suppress": "所选词条在草稿发布后将不再用于翻译。",
            "reenable": "所选词条将采用草稿中现有译名，并在发布后用于翻译。",
        }[operation]
        examples = "、".join(f"“{item.original}”" for item in items[:5])
        if len(items) > 5:
            examples += "等"
        answer = QMessageBox.question(
            self._parent,
            action,
            f"确定对选中的 {len(items)} 条术语{action}吗？\n{examples}\n\n{effect}\n所有修改均需发布后生效。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No if operation == "suppress" else QMessageBox.StandardButton.NoButton,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._run_draft_command(
                lambda: self.presenter.term_editing.batch_terms(identities, operation, expected_ref=draft.ref)
            )

    def review(self, conflict: object) -> None:
        if self._run_read is None:
            raise RuntimeError("冲突审核需要异步证据查询")
        self._run_read(
            lambda: self.presenter.conflict_details(conflict), self._review_ready, "正在载入全部候选和来源证据…"
        )

    def _review_ready(self, details) -> None:
        dialog = ConflictReviewDialog(details, self._parent)

        def save(choice):
            self._run_draft_command(
                lambda: self.presenter.resolve_review(
                    details, choice.operation, translation=choice.translation, plugin_id=choice.plugin_id
                ),
                on_saved=dialog.saved,
                on_error=dialog.save_failed,
            )

        dialog.save_requested.connect(save)
        dialog.exec()


def _find_current(draft, selected, *, follow_replacements=False):
    identities = {item.term_id: item for item in draft.decisions}
    current = identities.get(getattr(selected, "term_id", ""))
    if not follow_replacements:
        return current
    if current is None:
        original = normalize_original(getattr(selected, "original", ""))
        scope = getattr(selected, "scope", TermScope.project())
        current = next(
            (item for item in draft.decisions if normalize_original(item.original) == original and item.scope == scope),
            None,
        )
    if current is None:
        return None
    visited = {current.term_id}
    while replacement := next(
        (item for item in draft.decisions if item.replacement_of == current.term_id and item.term_id not in visited),
        None,
    ):
        current = replacement
        visited.add(current.term_id)
    return current


__all__ = ["DraftInteractionController"]

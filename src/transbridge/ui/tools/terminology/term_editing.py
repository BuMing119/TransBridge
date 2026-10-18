"""Qt-free command boundary for complete, atomic terminology edits."""

from __future__ import annotations

from transbridge.application.terminology.decisions import DecisionOperation
from transbridge.application.terminology.models import TermScope


class TermEditingCommands:
    def __init__(self, command, context, config) -> None:
        self._command = command
        self._context = context
        self._config = config

    def prepare_draft(self):
        return self._command("prepare_draft", self._context, config=self._config())

    def add_term(self, original, translation, *, scope=None, variants=(), notes="", expected_ref=None):
        return self._command(
            "apply_decision",
            DecisionOperation.ADD,
            self._context,
            original=original,
            translation=translation,
            scope=scope or TermScope.project(),
            variants=tuple(variants),
            notes=notes,
            expected_ref=expected_ref,
        )

    def edit_term(self, term_id, *, original, translation, scope, variants, notes, expected_ref=None):
        return self._command(
            "edit_term",
            term_id,
            self._context,
            original=original,
            translation=translation,
            scope=scope,
            variants=tuple(variants),
            notes=notes,
            expected_ref=expected_ref,
        )

    def change_translation(self, term_id, translation):
        return self._command(
            "apply_decision",
            DecisionOperation.CHANGE_TRANSLATION,
            self._context,
            term_id=term_id,
            translation=translation,
        )

    def set_suppressed(self, term_id, *, suppressed):
        operation = DecisionOperation.SUPPRESS if suppressed else DecisionOperation.REENABLE
        return self._command("apply_decision", operation, self._context, term_id=term_id)

    def batch_terms(self, term_ids, operation, *, expected_ref=None):
        return self._command("batch_terms", tuple(term_ids), operation, self._context, expected_ref=expected_ref)

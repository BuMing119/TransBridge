# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Keep incomplete inbound-review edits across page changes until preview."""

from dataclasses import dataclass

from transbridge.application.terminology_sync.draft_import_models import DraftImportChoice
from transbridge.application.terminology_sync.inbound import InboundReviewDecision, InboundTerminologyChange


@dataclass(frozen=True, slots=True)
class InboundReviewEdit:
    item: InboundTerminologyChange
    decision: InboundReviewDecision
    translation: str

    @classmethod
    def initial(cls, item: InboundTerminologyChange, choice: DraftImportChoice | None = None):
        content = item.remote or item.local
        if choice is not None and choice.edited is not None:
            content = choice.edited
        return cls(
            item,
            InboundReviewDecision.ACCEPT if choice is None else choice.decision,
            "" if content is None else content.translation,
        )

    def to_choice(self) -> DraftImportChoice:
        return DraftImportChoice.from_review(self.item, self.decision, self.translation)


class InboundReviewDrafts:
    """Store raw edits, including temporarily empty translations, outside Qt items."""

    def __init__(self) -> None:
        self._sets: dict[str, dict[str, InboundReviewEdit]] = {}

    def row(
        self, change_set_id: str, item: InboundTerminologyChange, choice: DraftImportChoice | None = None
    ) -> InboundReviewEdit:
        return self._sets.get(change_set_id, {}).get(item.item_id) or InboundReviewEdit.initial(item, choice)

    def remember(self, change_set_id: str, edits: tuple[InboundReviewEdit, ...]) -> None:
        self._sets.setdefault(change_set_id, {}).update((edit.item.item_id, edit) for edit in edits)

    def choices(self, change_set_id: str) -> tuple[DraftImportChoice, ...]:
        return choices_from_edits(self.snapshot(change_set_id))

    def snapshot(self, change_set_id: str) -> tuple[InboundReviewEdit, ...]:
        """Freeze raw values on Qt; validation, ordering and hashing run in the worker."""
        return tuple(self._sets.get(change_set_id, {}).values())


def choices_from_edits(edits):
    return tuple(edit.to_choice() for edit in sorted(edits, key=lambda edit: edit.item.item_id))

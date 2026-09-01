"""Keep incomplete inbound-review edits across page changes until preview."""

from dataclasses import dataclass, replace

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
        edited = None
        if self.decision is InboundReviewDecision.EDIT:
            source = self.item.remote or self.item.local
            if source is None:
                raise ValueError("没有可编辑的术语内容")
            edited = replace(source, translation=self.translation.strip(), digest="")
        return DraftImportChoice(self.item.item_id, self.decision, edited)


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
        edits = self._sets.get(change_set_id, {})
        return tuple(edits[item_id].to_choice() for item_id in sorted(edits))

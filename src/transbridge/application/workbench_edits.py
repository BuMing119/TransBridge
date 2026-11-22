# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Prepare workbench translation and stage edits from captured entry values."""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.mutation import VALID_STAGES, EntrySnapshot
from transbridge.converter.translation_entry import STAGE_TRANSLATED, STAGE_UNTRANSLATED


@dataclass(frozen=True, slots=True)
class EntryEdit:
    before: EntrySnapshot
    translation: str
    stage: int

    @property
    def changed(self) -> bool:
        return (self.translation, self.stage) != (self.before.translation, self.before.stage)

    @property
    def after(self) -> EntrySnapshot:
        if not self.changed:
            return self.before
        return replace(
            self.before, translation=self.translation, stage=self.stage, revision=self.before.revision.next()
        )


def validate_stage(stage: int) -> None:
    if type(stage) is not int or stage not in VALID_STAGES:
        raise ValueError(f"无效的 stage 值: {stage}，合法值: {sorted(VALID_STAGES)}")


def translation_edit(before: EntrySnapshot, text: str, stage: int | None = None) -> EntryEdit:
    translation = text.strip()
    if translation == "（无译文）":
        translation = ""
    if stage is None:
        stage = STAGE_TRANSLATED if translation and before.stage == STAGE_UNTRANSLATED else before.stage
    else:
        validate_stage(stage)
    return EntryEdit(before, translation, stage)


def stage_edit(before: EntrySnapshot, stage: int) -> EntryEdit:
    validate_stage(stage)
    return EntryEdit(before, before.translation, stage)


def changed_entry_states(edits: Iterable[EntryEdit]) -> dict[EntryKey, tuple[str, int]]:
    return {edit.before.entry_key: (edit.translation, edit.stage) for edit in edits if edit.changed}

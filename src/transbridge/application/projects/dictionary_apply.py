# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Prepare and commit the workbench's reviewed dictionary translations."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.io.identity import EntryKey
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.persistence.v2.ids import VariantRef
from transbridge.translation_memory.manager import ApplyResult, QueryContext, TranslationMemoryManager

if TYPE_CHECKING:
    from transbridge.application.projects.gui_facade import GuiProjectCommandFacade


def dictionary_source_path(source) -> str:
    """The workbench prefers a plugin path, followed by EET and XTranslator."""
    return str(
        getattr(source, "esp_path", "") or getattr(source, "eet_path", "") or getattr(source, "xt_path", "") or ""
    )


def dictionary_mod_id(source) -> str:
    return Path(dictionary_source_path(source)).stem


@dataclass
class DictionaryApplication:
    result: ApplyResult
    choices: list[dict]
    states: dict[EntryKey, tuple[str, int]]

    @property
    def conflict_count(self) -> int:
        return len({choice["entry_id"] for choice in self.result.conflicts})

    def apply_choices(self, selections: Iterable[tuple[str, str]]) -> None:
        """An empty review keeps every automatic winner."""
        offered = {(choice["entry_id"], choice["translation"]) for choice in self.choices}
        reviewed = {}
        for entry_id, translation in selections:
            if (entry_id, translation) not in offered:
                raise ValueError("所选译文不在该词条的词典候选中。")
            identity = EntryKey.deserialize(entry_id)
            reviewed[identity] = (translation, self.states[identity][1])
        self.states.update(reviewed)

    def commit(
        self,
        commands: "GuiProjectCommandFacade",
        runtime_context: RequestContext,
        *,
        expected_variant_ref: VariantRef,
        expected_project_revision: int,
        expected_variant_revision: int,
    ) -> OperationResult[dict]:
        return commands.replace_entry_states(
            self.states,
            runtime_context,
            expected_variant_ref=expected_variant_ref,
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
        )


def prepare_dictionary(
    collection: Iterable[TranslationEntry], manager: TranslationMemoryManager, *, mod_file_id: str
) -> DictionaryApplication:
    """Match on detached entries. Only the reviewed patch reaches the Variant."""
    manager.load()
    candidates = [replace(entry) for entry in collection]
    previous = {entry.identity: (entry.translation, entry.stage) for entry in candidates}
    result = manager.apply_to_collection(candidates, context=QueryContext(mod_file_id=mod_file_id))
    states = {
        entry.identity: (entry.translation, entry.stage)
        for entry in candidates
        if (entry.translation, entry.stage) != previous[entry.identity]
    }
    winners = {}
    for choice in result.conflicts:
        entry_id = choice["entry_id"]
        if entry_id not in winners:
            translation, _stage = states[EntryKey.deserialize(entry_id)]
            winners[entry_id] = {
                "entry_id": entry_id,
                "entry_label": choice["entry_label"],
                "translation": translation,
                "wins": True,
            }
    return DictionaryApplication(result, [*winners.values(), *result.conflicts], states)

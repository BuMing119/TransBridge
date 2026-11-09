# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read naming-scheme inputs from the project's stored translation rows."""

from __future__ import annotations

from transbridge.application.terminology_profiles.entry_projection import ProfileEntryInput
from transbridge.converter.translation_entry import TranslationEntry


class ProfileEntryInputReader:
    def __init__(self, projects):
        self._projects = projects

    def read(self, project, snapshot, context):
        states = {state.entry_key: state for state in snapshot.entries if not state.tombstone}
        store = self._projects.store_for(snapshot.ref.project_id)
        stored = store.read_entries(snapshot.ref.identity, states)
        result = {}
        for item in stored:
            parsed = item.snapshot
            state = states[parsed.entry_key]
            entry = TranslationEntry(
                id=parsed.legacy_id,
                key=parsed.entry_key.local_key,
                original=parsed.original,
                translation=state.translation,
                stage=state.stage.value,
                context=parsed.context,
                entry_key=parsed.entry_key,
                metadata=parsed.metadata,
                string_id=parsed.string_id,
            )
            result[parsed.entry_key] = ProfileEntryInput.capture(entry)
        if result.keys() != states.keys():
            raise ValueError("当前项目版本的来源已变化，请重新打开后再转换译名方案。")
        return result

from dataclasses import replace

import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects.dictionary_apply import prepare_dictionary
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.translation_memory import TranslationMemoryManager


@pytest.mark.parametrize("qualified", [False, True], ids=["distinct_namespaces", "distinct_originals"])
def test_review_tracks_each_conflict_by_complete_entry_identity(tmp_path, qualified):
    first_key = EntryKey(SourceNamespace("mod-a"), "same", "First" if qualified else None)
    second_key = EntryKey(SourceNamespace("mod-a" if qualified else "mod-b"), "same", "Second" if qualified else None)
    first = TranslationEntry("1", "same", "First", "", 0, None, entry_key=first_key)
    second = TranslationEntry("2", "same", "Second", "", 0, None, entry_key=second_key)
    untouched = replace(first, id="3", key="other", entry_key=None, translation="Existing", stage=1)
    manager = TranslationMemoryManager(base_dir=tmp_path)
    for mod, scope in [("winner", "project"), ("alternative", "global")]:
        for entry in (first, second) if qualified else (first,):
            key = entry.identity.serialize() if qualified else entry.key
            manager.add(key, entry.original, mod, scope=scope, mod_file_id=mod)
    manager.save()

    plan = prepare_dictionary([first, second, untouched], manager, mod_file_id="")
    assert plan.conflict_count == 2
    assert {c["entry_id"] for c in plan.choices if c["wins"]} == {first_key.serialize(), second_key.serialize()}
    plan.apply_choices([(second_key.serialize(), "alternative")])
    assert plan.states == {first_key: ("winner", 0), second_key: ("alternative", 0)}
    assert [entry.translation for entry in (first, second, untouched)] == ["", "", "Existing"]
    with pytest.raises(ValueError, match="词典候选"):
        plan.apply_choices([(first_key.serialize(), "alternative"), (second_key.serialize(), "never offered")])
    assert plan.states[first_key] == ("winner", 0)

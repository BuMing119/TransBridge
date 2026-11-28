from dataclasses import replace
from types import SimpleNamespace

from transbridge.application.io.open_translation_file import apply_open_file_dictionary
from transbridge.application.projects.dictionary_apply import (
    dictionary_library,
    dictionary_mod_id,
    prepare_library_application,
)
from transbridge.config.text_encoding import TextEncodings
from transbridge.converter.translation_entry import TranslationEntry


def test_application_preserves_input_and_full_entry_identity(tmp_path):
    ctx = SimpleNamespace(active_slot=SimpleNamespace(esp_path="Base.esp"))
    library = dictionary_library(ctx, tmp_path, encodings=TextEncodings())
    source = TranslationEntry("a", "a", "Open", "开启", 1, "ACTI:FULL")
    other = TranslationEntry("b", "b", "Open", "营业", 1, "MESG:FULL")
    assert library.save([source, other], dictionary_mod_id(ctx)) == 2
    empty = [replace(row, translation="", stage=0) for row in (source, other)]
    locked = TranslationEntry("lock", "lock", "Open", "", 9, None)
    hidden = TranslationEntry("hide", "hide", "Open", "", -1, None)
    existing = TranslationEntry("keep", "keep", "Open", "现有", 1, None)
    result = prepare_library_application([*empty, locked, hidden, existing], library, mod_file_id="Base")
    assert result.states == {source.identity: ("开启", 0), other.identity: ("营业", 0)}
    assert result.result.applied == 2
    assert all(row.translation == "" for row in empty)
    assert existing.translation == "现有"


def test_stale_key_does_not_fill_but_unchanged_text_does(tmp_path):
    library = dictionary_library(None, tmp_path, encodings=TextEncodings())
    library.save([TranslationEntry("a", "a", "Hello", "你好", 1, None)], "Base")
    changed = TranslationEntry("a", "a", "Hello again", "", 0, None)
    fresh = TranslationEntry("b", "b", "Hello", "", 0, None)
    result = prepare_library_application([changed, fresh], library, mod_file_id="Base")
    assert result.states == {fresh.identity: ("你好", 0)}


def test_open_and_manual_apply_fill_only_empty_untranslated_entries(tmp_path):
    library = dictionary_library(None, tmp_path, encodings=TextEncodings())
    library.save([TranslationEntry("base", "base", "Wait", "等等", 1, None)], "Base")
    rows = [
        TranslationEntry(str(i), str(i), "Wait", text, stage, None)
        for i, (text, stage) in enumerate([("", s) for s in (0, 1, 2, 3, 5, 9, -1)] + [("草稿", 0)])
    ]
    manual = prepare_library_application(rows, library, mod_file_id="Patch")
    assert manual.states == {rows[0].identity: ("等等", 0)}
    opened, count = apply_open_file_dictionary(rows, library, "Patch")
    assert count == 1
    assert [(row.translation, row.stage) for row in opened] == [
        ("等等", 0),
        *((row.translation, row.stage) for row in rows[1:]),
    ]

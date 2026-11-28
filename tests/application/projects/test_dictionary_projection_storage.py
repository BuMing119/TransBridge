"""Use the SQLite dictionary, actual profile conversion and both apply entry points."""

from contextlib import closing
from dataclasses import replace
from functools import partial
import sqlite3

from transbridge.application.io.open_translation_file import apply_open_file_dictionary
from transbridge.application.projects.dictionary_apply import prepare_library_application
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology_profiles.dictionary_projection import DictionaryTextProjection
from transbridge.application.terminology_profiles.models import (
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.translation_memory.library import DictionaryLibrary


def _library(path, default_name="雪漫城"):
    content = TerminologyProfileContent((
        ProfileTermMapping("Whiterun", "白漫城", default_name),
        ProfileTermMapping("Solitude", "孤城"),
    ))
    profile = PublishedTerminologyProfile("names", "project", 1, "B", content.content_digest, content, "now")
    base = EffectiveTerminologySnapshot("project", "variant", EffectiveSnapshotStatus.NO_PROJECT_VERSION)
    return DictionaryLibrary(path, projection_factory=partial(DictionaryTextProjection, profile, base))


def test_dictionary_writes_default_sentences_and_applies_to_actual_scheme(tmp_path):
    default = DictionaryLibrary(tmp_path)
    library = _library(tmp_path)
    entries = [
        TranslationEntry("1", "1", "Go to Whiterun", "动身去白漫城", 1, None),
        TranslationEntry("2", "2", "Open the gate", "打开大门", 1, None),
        TranslationEntry("3", "3", "Go to Solitude", "去孤城", 1, None),
    ]
    assert default.save([replace(entries[2], translation="旧有的译文")], "Mod") == 1
    assert library.save(entries, "Mod") == 2
    assert sorted(library.lookup_rows()) == [
        ("Go to Solitude", "旧有的译文", "Mod"),
        ("Go to Whiterun", "动身去雪漫城", "Mod"),
        ("Open the gate", "打开大门", "Mod"),
    ]
    empty = [replace(row, stage=0, translation="") for row in entries]
    plan = prepare_library_application(empty, library, mod_file_id="Mod")
    assert plan.states == {row.identity: (row.translation, 0) for row in entries[:2]}
    opened, count = apply_open_file_dictionary(empty, library, "Mod")
    assert count == 2
    assert [row.translation for row in opened] == ["动身去白漫城", "打开大门", ""]
    assert entries[0].translation == "动身去白漫城"


def test_legacy_checkpoint_and_profile_changes_refresh_only_the_current_source(tmp_path):
    entry = TranslationEntry("key", "key", "Go to Whiterun", "动身去白漫城", 1, None)
    read_calls = []

    def read(keys):
        read_calls.append(keys)
        return [entry]

    options = dict(
        project_id="project",
        variant_id="variant",
        source_id="src",
        mod_id="Mod",
        revision=1,
        project_revision=1,
        changes_since=lambda _: (),
        read=read,
    )
    legacy = DictionaryLibrary(tmp_path)
    assert legacy.save_revision(**options) == 1
    path = tmp_path / "libraries.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("ALTER TABLE saved_source_checkpoints DROP COLUMN projection_identity")
        db.execute("PRAGMA user_version = 2")
    assert list(legacy.lookup_rows()) == [(entry.original, entry.translation, "Mod")]

    for library, count in ((_library(tmp_path), 1), (_library(tmp_path), 0), (_library(tmp_path, "雪漫"), 1)):
        assert library.save_revision(**options) == count
    assert read_calls == [None, None, None]
    assert list(legacy.lookup_rows()) == [(entry.original, "动身去雪漫", "Mod")]
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)


def test_unresolved_last_alias_keeps_previously_saved_sentence(tmp_path):
    safe = TranslationEntry("key", "key", "Go to Whiterun", "动身去白漫城", 1, None)
    ambiguous = replace(safe, translation="白漫城和白漫城")
    default = DictionaryLibrary(tmp_path)
    assert default.save((replace(safe, translation="旧译"),), "Mod") == 1
    assert _library(tmp_path).save((safe, ambiguous), "Mod") == 0
    assert list(default.lookup_rows()) == [(safe.original, "旧译", "Mod")]
    assert _library(tmp_path).save((ambiguous, safe), "Mod") == 1
    assert list(default.lookup_rows()) == [(safe.original, "动身去雪漫城", "Mod")]

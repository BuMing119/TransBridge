# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Regression coverage for the workbench translation libraries."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import sqlite3
from threading import Barrier

import pytest

from transbridge.application.io.dsd_projection import DSD_LOCATOR_METADATA
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.translation_memory.library import DictionaryLibrary, DictionaryLibraryError, iter_library_history


def _entry(
    form="00000001",
    original="Common name",
    translation="普通名称",
    *,
    shape="NPC_:FULL",
    owner="Original.esm",
    editor="Edid",
    index=1,
    qualified=False,
):
    key = f"{editor}:{form}|{index}~{shape}"
    return TranslationEntry(
        key,
        key,
        original,
        translation,
        1 if translation else 0,
        shape,
        form_id_with_plugin=f"{form}|{owner}" if owner else None,
        requires_original_match=qualified,
    )


def test_persistent_main_selection_and_isolated_library_partitions(tmp_path):
    library = DictionaryLibrary(tmp_path)
    entry = _entry()
    assert library.list_mains() == []
    assert library.active_main() == ""
    assert list(iter_library_history(tmp_path)) == []
    assert list(tmp_path.iterdir()) == []

    assert library.save([entry], "Original") == 1
    unique = _entry("00000002", "Unique", "唯一")
    assert library.save([unique], "Original", main=" 主库 ") == 1
    assert library.save([_entry("00000003", "Inactive")], "Original", main="备用库") == 1
    assert library.list_mains() == ["主库", "备用库"]
    assert library.matcher().lookup(unique, "Original") == ("", "")
    library.select_main("主库")

    restored = DictionaryLibrary(tmp_path, game=" SkyrimSE ", source_locale="EN", target_locale="zh")
    assert restored.active_main() == "主库"
    assert restored.matcher().lookup(unique, "Original") == ("唯一", "key")
    assert list(restored.browse()) == [
        ("Common name", "普通名称", "Original"),
        ("Unique", "唯一", "Original"),
    ]
    with pytest.raises(ValueError, match="missing"):
        restored.select_main("missing")
    assert restored.active_main() == "主库"
    restored.select_main("", commit=lambda _publish: None)
    assert restored.active_main() == "主库"
    restored.select_main("")
    assert library.active_main() == ""
    assert library.matcher().lookup(unique, "Original") == ("", "")

    other_game = DictionaryLibrary(tmp_path, game="fallout4")
    other_language = DictionaryLibrary(tmp_path, target_locale="ja")
    assert other_game.list_mains() == other_language.list_mains() == []
    assert list(other_game.browse()) == list(other_language.browse()) == []
    assert other_game.matcher().lookup(entry, "Original") == ("", "")
    assert other_language.matcher().lookup(entry, "Original") == ("", "")
    other_game.save([entry], "Original", main="主库")
    other_game.select_main("主库")
    assert other_game.active_main() == "主库"
    assert library.active_main() == ""
    assert {
        (game, source, target, name, mod) for game, source, target, name, _, mod, _, _ in iter_library_history(tmp_path)
    } == {
        ("skyrimse", "en", "zh", "", "Original"),
        ("skyrimse", "en", "zh", "主库", "Original"),
        ("skyrimse", "en", "zh", "备用库", "Original"),
        ("fallout4", "en", "zh", "主库", "Original"),
    }


def test_matching_uses_specificity_before_personal_priority(tmp_path):
    library = DictionaryLibrary(tmp_path)
    library.save(
        [
            _entry("00000001", translation="类别", shape="NPC_:SHRT"),
            _entry("00000002", translation="普通", shape="WEAP:FULL"),
            _entry("00000003", translation="个人字段", shape="NPC_:DESC"),
        ],
        "Original",
    )
    library.save(
        [
            _entry("00000004", translation="主库字段"),
            _entry("00000005", translation="主库同级", shape="NPC_:DESC"),
        ],
        "Original",
        main="Main",
    )
    library.select_main("Main")
    matcher = library.matcher()

    assert matcher.lookup(_entry("00000004"), "OtherPatch") == ("主库字段", "key")
    assert matcher.lookup(_entry("00000ABC"), "OtherPatch") == ("主库字段", "text")
    assert matcher.lookup(_entry("00000ABC", shape="NPC_:DESC"), "OtherPatch") == ("个人字段", "text")
    assert matcher.lookup(_entry("00000ABC", shape="NPC_:EDID"), "OtherPatch") == ("类别", "text")
    assert matcher.lookup(_entry("00000ABC", shape="ARMO:EDID"), "OtherPatch") == ("类别", "text")
    assert matcher.lookup(_entry("00000004", original="Rewritten source"), "OtherPatch") == ("", "")
    assert matcher.lookup(_entry("00000ABC", original=" Common name\r\n"), "OtherPatch") == ("主库字段", "text")


def test_distinct_records_fields_and_indices_stay_distinct_during_revision(tmp_path):
    library = DictionaryLibrary(tmp_path)
    first = _entry("00000001", translation="第一")
    second = _entry("00000002", translation="第二")
    field = _entry("00000001", translation="说明", shape="NPC_:DESC")
    index = _entry("00000001", translation="重复字段", index=2)
    assert library.save([first, second, field, index], "Original") == 4
    assert library.save([first, second, field, index], "Original") == 0
    assert (
        library.save(
            [
                replace(first, translation="修订"),
                replace(second, translation="  "),
                replace(field, translation="锁定", stage=9),
                replace(index, translation="隐藏", stage=-1),
            ],
            "Original",
        )
        == 1
    )
    assert library.save([replace(first, original="Revised source", translation="新原文")], "Original") == 1
    matcher = library.matcher()
    assert matcher.lookup(first, "Original")[1] == "text"
    assert matcher.lookup(replace(first, original="Revised source"), "Original") == ("新原文", "key")
    for entry in (second, field, index):
        assert matcher.lookup(entry, "Original") == (entry.translation, "key")
    assert len({row[4] for row in iter_library_history(tmp_path)}) == 4


_SAVED_SOURCE = {"project_id": "Project", "variant_id": "Variant", "source_id": "Source", "mod_id": "Original"}


def _unexpected_callback(*_args):
    pytest.fail("the saved-source callback was not expected")


def test_saved_source_reads_full_then_only_changed_keys_and_advances_on_empty_delta(tmp_path):
    library = DictionaryLibrary(tmp_path)
    first, second = _entry(), _entry("00000002", original="Second", translation="第二")

    def read_all(keys):
        assert keys is None
        return first, second

    assert library.save_revision(revision=0, changes_since=_unexpected_callback, read=read_all, **_SAVED_SOURCE) == 2

    library = DictionaryLibrary(tmp_path)
    assert (
        library.save_revision(
            revision=0, changes_since=_unexpected_callback, read=_unexpected_callback, **_SAVED_SOURCE
        )
        == 0
    )

    def empty_delta(previous):
        assert previous == 0
        return ()

    assert library.save_revision(revision=1, changes_since=empty_delta, read=_unexpected_callback, **_SAVED_SOURCE) == 0

    def one_change(previous):
        assert previous == 1
        return (second.identity,)

    def read_one(keys):
        assert keys == (second.identity,)
        return (replace(second, translation="新译文"),)

    assert library.save_revision(revision=2, changes_since=one_change, read=read_one, **_SAVED_SOURCE) == 1
    assert library.matcher().lookup(first, "Original") == (first.translation, "key")
    assert library.matcher().lookup(second, "Original") == ("新译文", "key")

    assert library.save_revision(revision=4, changes_since=lambda _revision: None, read=read_all, **_SAVED_SOURCE) == 1
    assert library.matcher().lookup(second, "Original") == (second.translation, "key")


def test_same_saved_revision_cannot_overwrite_another_projects_newer_example(tmp_path):
    library = DictionaryLibrary(tmp_path)
    entry = _entry()
    scope = {**_SAVED_SOURCE, "project_id": "Another Project"}
    assert (
        library.save_revision(
            revision=1, changes_since=_unexpected_callback, read=lambda _keys: (entry,), **_SAVED_SOURCE
        )
        == 1
    )
    assert (
        library.save_revision(
            revision=1,
            changes_since=_unexpected_callback,
            read=lambda _keys: (replace(entry, translation="其他项目"),),
            **scope,
        )
        == 1
    )
    assert (
        library.save_revision(
            revision=1, changes_since=_unexpected_callback, read=_unexpected_callback, **_SAVED_SOURCE
        )
        == 0
    )
    assert library.matcher().lookup(entry, "Original") == ("其他项目", "key")
    assert library.save((entry,), "Original") == 1


@pytest.mark.parametrize("field", ["project_id", "variant_id", "source_id", "mod_id"])
def test_checkpoint_partitions_project_variant_source_and_mod(tmp_path, field):
    library = DictionaryLibrary(tmp_path)
    scope = {**_SAVED_SOURCE, field: "Other"}
    calls = []

    def read(keys):
        calls.append(keys)
        return ()

    assert library.save_revision(revision=0, changes_since=_unexpected_callback, read=read, **_SAVED_SOURCE) == 0
    assert library.save_revision(revision=0, changes_since=_unexpected_callback, read=read, **scope) == 0
    assert calls == [None, None]


def test_saved_source_rebuilds_after_a_different_project_revision_or_lower_variant_revision(tmp_path):
    library = DictionaryLibrary(tmp_path)
    calls = []

    def read(keys):
        calls.append(keys)
        return ()

    for project_revision, revision in ((1, 4), (2, 4), (2, 2)):
        assert (
            library.save_revision(
                project_revision=project_revision,
                revision=revision,
                changes_since=_unexpected_callback,
                read=read,
                **_SAVED_SOURCE,
            )
            == 0
        )
    assert calls == [None, None, None]


def test_saved_source_keeps_full_pass_winner_for_aliases_even_when_one_was_ineligible(tmp_path):
    library = DictionaryLibrary(tmp_path)
    early = _entry("01000001", translation="", editor="Early")
    late = _entry("02000001", translation="最后一个", editor="Late")
    entries = (early, late)

    def read(keys):
        assert keys is None
        return entries

    assert library.save_revision(revision=1, changes_since=_unexpected_callback, read=read, **_SAVED_SOURCE) == 1
    early = replace(early, translation="第一个", stage=1)
    entries = (early, late)
    assert library.save_revision(revision=2, changes_since=lambda _: (early.identity,), read=read, **_SAVED_SOURCE) == 0
    assert library.matcher().lookup(early, "Original") == (late.translation, "key")

    entries = (early, replace(late, stage=-1))
    assert library.save_revision(revision=3, changes_since=lambda _: (late.identity,), read=read, **_SAVED_SOURCE) == 1
    assert library.matcher().lookup(early, "Original") == (early.translation, "key")


def test_failed_saved_source_checkpoint_rolls_back_records_and_retry_uses_previous_revision(tmp_path):
    library = DictionaryLibrary(tmp_path)
    entry = _entry()
    assert (
        library.save_revision(revision=1, changes_since=_unexpected_callback, read=lambda _: (entry,), **_SAVED_SOURCE)
        == 1
    )
    path = tmp_path / "libraries.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""
            CREATE TRIGGER fail_checkpoint BEFORE UPDATE ON saved_source_checkpoints
            BEGIN SELECT RAISE(ABORT, 'checkpoint failure'); END
        """)

    def changes_since(previous):
        assert previous == 1
        return (entry.identity,)

    def read(keys):
        assert keys == (entry.identity,)
        return (replace(entry, translation="重试"),)

    with pytest.raises(DictionaryLibraryError, match="checkpoint failure"):
        library.save_revision(revision=2, changes_since=changes_since, read=read, **_SAVED_SOURCE)
    assert library.matcher().lookup(entry, "Original") == (entry.translation, "key")
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TRIGGER fail_checkpoint")
    assert library.save_revision(revision=2, changes_since=changes_since, read=read, **_SAVED_SOURCE) == 1
    assert library.matcher().lookup(entry, "Original") == ("重试", "key")


def test_concurrent_saved_source_publish_uses_one_checkpoint_across_connections(tmp_path):
    started = Barrier(2, timeout=5)
    entry = _entry()
    reads = []

    def publish(_worker):
        started.wait()

        def read(keys):
            reads.append(keys)
            return (entry,)

        return DictionaryLibrary(tmp_path).save_revision(
            revision=3, changes_since=_unexpected_callback, read=read, **_SAVED_SOURCE
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish, range(2))) == [0, 1]
    assert reads == [None]


def test_version_one_database_is_readable_and_migrates_on_save(tmp_path):
    library = DictionaryLibrary(tmp_path)
    personal = _entry()
    main = _entry("00000002", original="Other", translation="主库")
    assert library.save((personal,), "Original") == 1
    assert library.save((main,), "Original", main="Main") == 1
    library.select_main("Main")
    path = tmp_path / "libraries.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE saved_source_checkpoints")
        db.execute("PRAGMA user_version = 1")

    assert library.active_main() == "Main"
    assert len(list(iter_library_history(tmp_path))) == 2
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (1,)

    assert (
        library.save_revision(
            revision=0, changes_since=_unexpected_callback, read=lambda _: (personal,), **_SAVED_SOURCE
        )
        == 0
    )
    assert library.active_main() == "Main"
    assert library.matcher().lookup(main, "Original") == (main.translation, "key")
    assert len(list(iter_library_history(tmp_path))) == 2
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (3,)


def test_update_retains_latest_raw_source_locator_and_editor(tmp_path):
    library = DictionaryLibrary(tmp_path)
    entry = _entry("01000123", editor="Old")
    library.save([entry], "Original")
    revised = _entry("05000123", editor="Revised")
    assert library.save([revised], "Original") == 1
    with closing(sqlite3.connect(tmp_path / "libraries.sqlite3")) as db:
        assert db.execute("SELECT source_key, native_form_id, editor_id FROM library_records").fetchall() == [
            (revised.key, revised.form_id_with_plugin, "Revised")
        ]


def test_plugin_override_preference_and_hydrated_repeat_fields(tmp_path):
    library = DictionaryLibrary(tmp_path)
    original = _entry("02000001", translation="原插件", index=3)
    replacement = _entry("07000001", "Patch original", "补丁修订", editor="ChangedEditor", index=3)
    assert library.save([original], "Original") == 1
    assert library.save([replacement], "Patch") == 1
    assert library.matcher().lookup(original, "Original") == ("原插件", "key")
    assert library.matcher().lookup(replacement, "Patch") == ("补丁修订", "key")

    replacement = replace(replacement, original=original.original, translation="补丁覆盖")
    assert library.save([replacement], "AAAPatch") == 1
    assert library.save([replace(replacement, translation="主库覆盖")], "AnotherPatch", main="Main") == 1
    library.select_main("Main")
    matcher = library.matcher()

    def hydrated(entry):
        return replace(
            entry,
            form_id_with_plugin=None,
            dsd_index=1,
            metadata=((DSD_LOCATOR_METADATA, (entry.form_id_with_plugin, "NPC_ FULL", 3, "ChangedEditor")),),
        )

    assert matcher.lookup(hydrated(replacement), "AAAPatch.esp") == ("补丁覆盖", "key")
    assert matcher.lookup(hydrated(replacement), "AnotherPatch") == ("原插件", "key")
    assert matcher.lookup(hydrated(replacement), "Unrelated") == ("原插件", "key")
    assert matcher.lookup(_entry("07000001", index=2), "AAAPatch")[1] == "text"
    assert len({row[4] for row in iter_library_history(tmp_path)}) == 4


def test_qualified_locators_require_exact_original_and_do_not_create_text_hits(tmp_path):
    library = DictionaryLibrary(tmp_path)
    early = _entry(original="Original", translation="原本", qualified=True)
    late = _entry(original="Original ", translation="变体", qualified=True)
    assert library.save([early, late], "Original") == 2
    matcher = library.matcher()
    assert matcher.lookup(early, "Original") == ("原本", "key")
    assert matcher.lookup(late, "Original") == ("变体", "key")
    assert matcher.lookup(_entry(original=" Original", qualified=True), "Original") == ("", "")
    assert matcher.lookup(_entry(form="00000002", original="Original", qualified=True), "Original") == ("", "")
    assert matcher.lookup(_entry(form="00000002", original="Original"), "Original") == ("", "")


def test_sqlite_source_key_still_matches_when_target_has_an_owner(tmp_path):
    library = DictionaryLibrary(tmp_path)
    saved = _entry(owner="")
    library.save([saved], "Original")
    matcher = library.matcher()
    assert matcher.lookup(_entry(), "Original") == (saved.translation, "key")
    assert matcher.lookup(_entry(), "Other") == (saved.translation, "text")


def test_query_and_apply_ignore_retired_files_even_alongside_current_records(tmp_path):
    entry = _entry()
    old = _entry("00000002", original="Only in file", translation="旧译")
    path = tmp_path / "Original.tbdict"
    data = json.dumps({
        "mod_file_id": "Original",
        "entries": {"old": {"original": old.original, "translation": old.translation}},
        "text_index": {old.original.casefold(): {"entry_id": "old"}},
    }).encode()
    path.write_bytes(data)
    library = DictionaryLibrary(tmp_path)
    assert list(library.lookup_rows()) == []
    assert library.matcher().lookup(old, "Original") == ("", "")
    assert list(tmp_path.iterdir()) == [path]

    library.save([entry], "Original")
    assert list(library.lookup_rows()) == list(library.browse()) == [(entry.original, entry.translation, "Original")]
    matcher = library.matcher()
    assert matcher.lookup(entry, "Original") == (entry.translation, "key")
    assert matcher.lookup(old, "Original") == ("", "")
    assert path.read_bytes() == data


def test_cancel_after_staging_preserves_prior_library_and_rolls_back_main(tmp_path):
    library = DictionaryLibrary(tmp_path)
    entry = _entry()
    library.save([entry], "Original")
    revised = replace(entry, translation="取消的改动")
    cancelled = False

    def checkpoint():
        if cancelled:
            raise InterruptedError("cancelled")

    def cancel_at_commit(publish):
        nonlocal cancelled
        cancelled = True
        publish()

    with pytest.raises(InterruptedError, match="cancelled"):
        library.save([revised], "Original", main="Cancelled", checkpoint=checkpoint, commit=cancel_at_commit)
    assert library.list_mains() == []
    assert library.save([revised], "Original", commit=lambda _publish: None) == 0
    assert library.matcher().lookup(entry, "Original") == (entry.translation, "key")


@pytest.mark.parametrize("create_main", [False, True])
def test_concurrent_saves_merge_without_overwriting_an_exclusive_main(tmp_path, create_main):
    started = Barrier(2, timeout=5)

    def save(form):
        def entries():
            started.wait()
            yield _entry(form)

        try:
            return DictionaryLibrary(tmp_path).save(entries(), "Original", main="Shared", create_main=create_main)
        except ValueError as exc:
            assert "Shared" in str(exc)
            return 0

    with ThreadPoolExecutor(max_workers=2) as pool:
        changed = list(pool.map(save, ["00000001", "00000002"]))
    expected = [0, 1] if create_main else [1, 1]
    assert sorted(changed) == expected
    library = DictionaryLibrary(tmp_path)
    assert library.list_mains() == ["Shared"]
    library.select_main("Shared")
    assert len(list(library.browse())) == sum(expected)


def test_user_can_create_an_empty_main_without_selecting_it(tmp_path):
    library = DictionaryLibrary(tmp_path)
    assert library.save([], "Original", main="Empty", create_main=True) == 0
    assert library.list_mains() == ["Empty"]
    assert library.active_main() == ""


def test_corrupt_database_has_a_specific_error_without_mutating_the_file(tmp_path):
    path = tmp_path / "libraries.sqlite3"
    original = b"not a SQLite database"
    path.write_bytes(original)
    with pytest.raises(DictionaryLibraryError, match="libraries.sqlite3"):
        list(iter_library_history(tmp_path))
    assert path.read_bytes() == original

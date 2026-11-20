"""Pure upload planning preserves cloud identity, content and status boundaries."""

from copy import deepcopy

import pytest

from transbridge.application.file_upload_diff import IncompleteUploadSnapshot, plan_file_changes
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.paratranz_keys import encode_key, export_key


def record(key="name", *, order=0, **fields):
    return {"key": export_key(key, order), "original": "Original", "translation": "译文", "stage": 3, **fields}


def cloud(key="name", **fields):
    return {
        "id": 100,
        "fileId": 20,
        "key": key,
        "original": "Original",
        "translation": "译文",
        "stage": 3,
        "context": "",
        **fields,
    }


def plan(records, rows, mode="orig_only", **kwargs):
    return plan_file_changes(records, rows, file_id=20, mode=mode, **kwargs)


@pytest.mark.parametrize("stage", [5, 9])
def test_rename_only_preserves_remote_id_status_and_cleans_old_structured_context(stage):
    local = [record(context="NPC_:FULL")]
    remote = [cloud(context="00000017|NPC_:FULL", stage=stage)]
    result = plan(local, remote)
    assert result.renames == ({"id": 100, "key": local[0]["key"], "context": "NPC_:FULL", "stage": stage},)
    assert not result.source_changed
    assert not result.translations


def test_clean_context_with_current_key_still_produces_an_id_based_cleanup():
    local = [record(context="NPC_:FULL|0000000A")]
    remote = [cloud(local[0]["key"], context="00000017|NPC_:FULL|0000000A")]
    result = plan(local, remote)
    assert result.renames == ({"id": 100, "key": local[0]["key"], "stage": 3, "context": "NPC_:FULL|0000000A"},)
    assert not result.source_changed


def test_mixed_cloud_key_generations_rename_to_complete_local_keys():
    special = encode_key(EntryKey(SourceNamespace.legacy(), "dialogue", "Original"), "Original")
    keys = ["old", "numbered", special]
    local = [record(key, order=index) for index, key in enumerate(keys)]
    remote = [cloud(key, id=100 + index) for index, key in enumerate([keys[0], export_key(keys[1], 99), keys[2]])]
    result = plan(local, remote)
    assert result.renames == tuple(
        {"id": 100 + index, "key": row["key"], "stage": 3} for index, row in enumerate(local)
    )
    assert not result.source_changed


@pytest.mark.parametrize("changes", [{"original": "Changed"}, {"context": "NPC_:FULL"}])
def test_original_or_base_context_change_requests_source_update(changes):
    local = [record(**changes)]
    result = plan(local, [cloud(local[0]["key"])])
    assert result.source_changed
    assert not result.renames


def test_new_local_entry_updates_sources_while_remote_extras_are_retained():
    local = [record("new")]
    result = plan(local, [cloud("remote-only")])
    assert result.source_changed
    assert not result.renames


def test_free_form_context_that_resembles_old_numbering_is_not_stripped():
    local = [record(context="00000001|free form")]
    assert not plan(local, [cloud(local[0]["key"], context="00000001|free form")]).source_changed


@pytest.mark.parametrize("mode", ["trans_safe", "trans_force"])
def test_translation_only_uses_full_cloud_key_without_renames_or_new_entries(mode):
    local = [record("existing", order=2), record("new", order=3)]
    key = export_key("existing", 99)
    result = plan(local, [cloud(key, translation="old")], mode)
    assert result.translations == ({"key": key, "translation": "译文", "stage": 3},)
    assert not result.renames
    assert not result.source_changed


@pytest.mark.parametrize("field,value", [("translation", "old"), ("stage", 5), ("translation", None), ("stage", None)])
def test_safe_import_compares_translation_and_stage_without_collapsing_null(field, value):
    local = [record(translation="")]
    remote = [cloud(translation="", **{field: value})] if field != "translation" else [cloud(translation=value)]
    result = plan(local, remote, "trans_safe")
    assert result.translations == ({"key": "name", "translation": "", "stage": 3},)


@pytest.mark.parametrize("translation", ["", " \n\t", "译文"])
def test_force_import_skips_blank_and_identical_text_even_if_status_differs(translation):
    assert not plan([record(translation=translation)], [cloud(stage=9)], "trans_force").translations


def test_force_import_preserves_nonblank_whitespace_and_compares_null_as_different():
    result = plan([record(translation=" 译文 ")], [cloud(translation=None)], "trans_force")
    assert result.translations == ({"key": "name", "translation": " 译文 ", "stage": 3},)


def test_both_uses_new_keys_and_restores_only_affected_rows_after_source_change():
    local = [record("changed", original="Changed"), record("same", order=1), record("new", order=2)]
    remote = [cloud("changed"), cloud(local[1]["key"], id=101)]
    result = plan(local, remote, "both")
    assert result.source_changed
    assert result.renames == ({"id": 100, "key": local[0]["key"], "stage": 3},)
    assert result.translations == tuple(
        {"key": row["key"], "translation": "译文", "stage": 3} for row in (local[0], local[2])
    )


def test_both_rename_only_does_not_repeat_identical_translation():
    result = plan([record()], [cloud()], "both")
    assert result.renames
    assert not result.source_changed
    assert not result.translations


@pytest.mark.parametrize("literal", ["00000001|TB2|literal", "__transbridge_key__:literal"])
def test_literal_escaped_keys_match_previous_unescaped_cloud_keys(literal):
    transport = encode_key(EntryKey(SourceNamespace.legacy(), literal), "Original")
    local = [record(transport)]
    result = plan(local, [cloud(literal)])
    assert result.renames == ({"id": 100, "key": local[0]["key"], "stage": 3},)
    assert not result.source_changed


def test_escaped_literal_remains_distinct_from_a_regular_numbered_cloud_key():
    literal = "00000001|TB2|literal"
    local = [record(literal), record("literal", order=1)]
    remote = [cloud(local[0]["key"]), cloud(export_key("literal", 2), id=101)]
    result = plan(local, remote)
    assert result.renames == ({"id": 101, "key": local[1]["key"], "stage": 3},)
    assert not result.source_changed


def test_ambiguous_unescaped_literal_and_ordered_identity_is_rejected():
    local = [record("00000001|TB2|name"), record("name", order=1)]
    with pytest.raises(ValueError, match="歧义"):
        plan(local, [cloud("00000001|TB2|name")])


@pytest.mark.parametrize(
    "rows",
    [
        [cloud("name"), cloud("name", id=101)],
        [cloud("name"), cloud(export_key("name", 9), id=101)],
        [cloud("name"), cloud("other")],
    ],
)
def test_duplicate_cloud_keys_identities_or_ids_are_rejected(rows):
    with pytest.raises(ValueError, match="重复"):
        plan([record()], rows)


def test_distinct_cloud_rows_may_not_resolve_to_one_literal_identity():
    local = [record("00000001|TB2|literal")]
    with pytest.raises(ValueError, match="歧义"):
        plan(local, [cloud(local[0]["key"]), cloud("00000001|TB2|literal", id=101)])


def test_duplicate_local_transport_identity_is_rejected():
    with pytest.raises(ValueError, match="重复传输身份"):
        plan([record(order=1), record(order=2)], [])


@pytest.mark.parametrize("mode", ["orig_only", "trans_safe", "trans_force", "both"])
def test_original_qualified_key_with_changed_cloud_original_is_rejected(mode):
    key = encode_key(EntryKey(SourceNamespace.legacy(), "same", "Original"), "Original")
    with pytest.raises(ValueError, match="原文哈希不匹配"):
        plan([record(key)], [cloud(key, original="Changed")], mode)


def test_original_qualified_key_requires_original_even_for_archive_translation_comparison():
    key = encode_key(EntryKey(SourceNamespace.legacy(), "same", "Original"), "Original")
    remote = cloud(key)
    remote.pop("original")
    with pytest.raises(IncompleteUploadSnapshot, match="original"):
        plan([record(key)], [remote], "trans_force", from_archive=True)


@pytest.mark.parametrize("from_archive", [False, True])
@pytest.mark.parametrize(
    "mode,field,value",
    [
        ("orig_only", "original", None),
        ("orig_only", "context", []),
        ("trans_safe", "translation", 1),
        ("trans_safe", "stage", True),
    ],
)
def test_invalid_comparison_field_types_fail_before_any_plan_is_returned(from_archive, mode, field, value):
    error = IncompleteUploadSnapshot if from_archive else ValueError
    with pytest.raises(error, match=field):
        plan([record()], [cloud(**{field: value})], mode, from_archive=from_archive)


def test_original_qualified_local_record_cannot_disagree_with_its_encoded_identity():
    key = encode_key(EntryKey(SourceNamespace.legacy(), "same", "Original"), "Original")
    with pytest.raises(ValueError, match="原文哈希不匹配"):
        plan([record(key, original="Changed")], [])


def test_different_original_qualified_identities_with_same_bare_key_remain_distinct():
    namespace = SourceNamespace.legacy()
    first = encode_key(EntryKey(namespace, "same", "Original"), "Original")
    second = encode_key(EntryKey(namespace, "same", "Second"), "Second")
    local = [record(first), record(second, order=1, original="Second")]
    result = plan(local, [cloud(first)], "both")
    assert result.source_changed
    assert result.translations == ({"key": local[1]["key"], "translation": "译文", "stage": 3},)


def test_literal_key_that_looks_like_original_qualified_encoding_remains_literal():
    namespace = SourceNamespace.legacy()
    literal = encode_key(EntryKey(namespace, "same", "Other"), "Other")
    local = [record(encode_key(EntryKey(namespace, literal), "Original"))]
    assert not plan(local, [cloud(literal)]).source_changed


@pytest.mark.parametrize("mode", ["orig_only", "trans_safe", "trans_force", "both"])
def test_complete_equal_snapshot_has_no_writes(mode):
    local = [record()]
    result = plan(local, [cloud(local[0]["key"])], mode)
    assert not result.renames
    assert not result.source_changed
    assert not result.translations


@pytest.mark.parametrize(
    "mode,missing",
    [
        ("orig_only", "original"),
        ("orig_only", "context"),
        ("orig_only", "id"),
        ("orig_only", "stage"),
        ("trans_safe", "stage"),
        ("trans_safe", "translation"),
        ("trans_force", "translation"),
    ],
)
def test_archive_with_missing_comparison_or_rename_fields_requests_file_read(mode, missing):
    remote = cloud()
    remote.pop(missing)
    with pytest.raises(IncompleteUploadSnapshot, match=missing):
        plan([record()], [remote], mode, from_archive=True)


def test_online_missing_context_is_empty_but_missing_rename_id_fails():
    remote = cloud()
    remote.pop("context")
    assert not plan([record()], [remote]).source_changed
    remote.pop("id")
    with pytest.raises(ValueError, match="id") as error:
        plan([record()], [remote])
    assert not isinstance(error.value, IncompleteUploadSnapshot)


def test_unchanged_complete_key_does_not_require_id_for_translation_only_or_sources():
    local = [record()]
    remote = cloud(local[0]["key"])
    remote.pop("id")
    assert not plan(local, [remote]).renames
    assert not plan(local, [remote], "trans_safe").translations


@pytest.mark.parametrize("foreign", [21, {"id": 21}, True])
def test_foreign_file_rows_are_rejected(foreign):
    with pytest.raises(ValueError, match="目标文件"):
        plan([record()], [cloud(fileId=foreign)])


def test_planning_does_not_mutate_local_or_cloud_data():
    local, remote = [record(context="NPC_:FULL")], [cloud(context="00000001|NPC_:FULL")]
    before = deepcopy((local, remote))
    plan(local, remote, "both")
    assert (local, remote) == before

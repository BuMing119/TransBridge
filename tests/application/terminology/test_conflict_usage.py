from types import SimpleNamespace

from transbridge.application.terminology.conflict_usage import conflict_usage


def evidence(identity, key, source="one"):
    return SimpleNamespace(evidence_id=identity, entry_key=key, source_chain=(source,), namespace="plugin")


def conflict(*groups):
    return SimpleNamespace(
        variants=tuple(SimpleNamespace(normalized_translation=name, evidence_ids=ids) for name, ids in groups)
    )


def test_unique_record_counts_ignore_appended_original_but_keep_distinct_sources():
    items = (
        evidence("a", '["source", "record", "old text"]'),
        evidence("b", '["source", "record", "new text"]'),
        evidence("c", '["source", "record"]', "two"),
        evidence("d", "other record"),
    )
    result = conflict_usage(conflict(("地区", ("a", "b", "c")), ("语言", ("d", "missing"))), items)
    assert [(item.count, item.minority) for item in result] == [(2, False), (1, True)]
    assert result[0].evidence == (items[0], items[2])


def test_ties_show_all_and_mapping_input_is_supported():
    items = {item.evidence_id: item for item in (evidence("a", "one"), evidence("b", "two"))}
    result = conflict_usage(conflict(("甲", ("a",)), ("乙", ("b",))), items)
    assert all(item.minority and item.count == 1 for item in result)


def test_missing_records_are_not_counted_or_fabricated():
    result = conflict_usage(conflict(("甲", ("missing",)), ("乙", ("missing2",))), ())
    assert all(item.count == 0 and item.evidence == () for item in result)

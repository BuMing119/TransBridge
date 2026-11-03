"""Plugin-only reading order contracts use synthetic graphs and no XML data."""

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from transbridge.application.dialogue_order import Branch, FieldOrder, Item, Link, Topic, plugin_only_layout
from transbridge.application.io.identity import EntryKey, SourceNamespace

SOURCE, QUEST = "source", "quest"


def data(baseline, edges=(), zero=(), keys=None):
    topics = [Topic(node, SOURCE, QUEST, index) for index, node in enumerate([*baseline, *zero])]
    items = [Item(key, node, SOURCE, QUEST) for node in baseline for key in (keys or {}).get(node, [node])]
    links = [Link(left, right, SOURCE, QUEST, info) for left, right, info in edges]
    return topics, items, links


def run(dataset, branches=(), fields=(), **kwargs):
    return plugin_only_layout(
        *dataset, source_id=SOURCE, quest_id=QUEST, branches=branches, field_orders=fields, **kwargs
    )


def assert_invariants(result, dataset):
    topics, items, links = dataset
    order = result["ordered_topic_ids"]
    assert Counter(result["ordered_keys"]) == Counter(item.key for item in items)
    assert len(order) == len(topics) and set(order) == {topic.topic_id for topic in topics}
    owner = {node: scc["id"] for scc in result["sccs"] for node in scc["topic_ids"]}
    position = {node: index for index, node in enumerate(order)}
    for edge in links:
        if owner[edge.source_topic] != owner[edge.target_topic]:
            assert position[edge.source_topic] < position[edge.target_topic]
    emitted = {key: index for index, key in enumerate(result["ordered_keys"])}
    for keys in result["blocks"].values():
        assert [emitted[key] for key in keys] == sorted(emitted[key] for key in keys)
    for scc in result["sccs"]:
        members = set(scc["topic_ids"])
        assert [node for node in order if node in members] == [
            node for node in result["baseline_topic_ids"] if node in members
        ]


def test_pure_layout_reads_no_files(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Pure algorithm must not read any file")

    monkeypatch.setattr(Path, "read_bytes", deny)
    monkeypatch.setattr("builtins.open", deny)
    result = run(data(["B", "A"], [("A", "B", "info")]))
    assert result["ordered_keys"] == ["A", "B"]
    assert result["reference_assisted"] is False and result["xml_used_for_ordering"] is False


def test_soft_branch_entry_groups_related_blocks_without_adding_edges():
    branch = Branch("branch", SOURCE, QUEST, "A", ("A", "B"))
    result = run(data(["B", "X", "A"]), [branch])
    assert result["ordered_keys"] == ["A", "B", "X"]
    assert result["selection_reason"]["selected_candidate"] == "D"
    assert not result["edges_satisfied"] and not result["interior_cycle_edges"]


def test_hard_predecessor_wins_over_branch_start():
    branch = Branch("branch", SOURCE, QUEST, "A", ("A", "B"))
    dataset = data(["A", "B"], [("B", "A", "info")])
    result = run(dataset, [branch])
    assert result["ordered_keys"] == ["B", "A"]
    assert result["candidate_metrics"]["D"]["avoidable_branch_start_late_pairs"] == 0
    assert_invariants(result, dataset)


def test_cross_branch_links_and_multiple_entries_keep_only_real_constraints():
    branches = [Branch("one", SOURCE, QUEST, "A", ("A", "C")), Branch("two", SOURCE, QUEST, "B", ("B", "D"))]
    dataset = data(["C", "D", "B", "A"], [("A", "B", "i1"), ("B", "C", "i2"), ("D", "C", "i3")])
    result = run(dataset, branches)
    assert len(result["edges_satisfied"]) == 3 and not result["edges_unsatisfied"]
    assert_invariants(result, dataset)


def test_single_info_field_order_is_a_soft_frontier_preference():
    dataset = data(["S", "C", "D"], [("S", "C", "info"), ("S", "D", "info")])
    fields = [FieldOrder("S", "info", "D", 10, 2), FieldOrder("S", "info", "C", 10, 3)]
    result = run(dataset, fields=fields)
    assert result["candidate_D"]["ordered_topic_ids"] == ["S", "D", "C"]
    # Frozen metrics tie on adjacency/gap, so A wins its smaller baseline change.
    assert result["ordered_keys"] == ["S", "C", "D"]
    assert result["selection_reason"]["selected_candidate"] == "pure_A"
    assert_invariants(result, dataset)


def test_conflicting_multi_info_exits_use_baseline_without_reordering_info_items():
    dataset = data(
        ["S", "C", "D"],
        [("S", "C", "i1"), ("S", "D", "i1"), ("S", "D", "i2"), ("S", "C", "i2")],
        keys={"S": ["i1-r1", "i1-r2", "i2-r1"]},
    )
    fields = [
        FieldOrder("S", "i1", "D", 10, 1),
        FieldOrder("S", "i1", "C", 10, 2),
        FieldOrder("S", "i2", "C", 20, 1),
        FieldOrder("S", "i2", "D", 20, 2),
    ]
    result = run(dataset, fields=fields)
    assert result["candidate_D"]["ordered_topic_ids"] == ["S", "C", "D"]
    assert any(d["code"] == "multi_info_exit_order_baseline_fallback" for d in result["candidate_D"]["diagnostics"])
    assert_invariants(result, dataset)


def test_scc_members_preserve_baseline_with_soft_start_inside_cycle():
    branch = Branch("branch", SOURCE, QUEST, "B", ("A", "B", "C"))
    dataset = data(["C", "A", "B"], [("A", "B", "i1"), ("B", "C", "i2"), ("C", "A", "i3")])
    result = run(dataset, [branch])
    assert result["ordered_topic_ids"] == ["C", "A", "B"]
    assert len(result["interior_cycle_edges"]) == 3
    assert_invariants(result, dataset)


def test_no_full_and_empty_bridge_retain_info_and_response_order():
    keys = {"A": ["info-a:r1", "info-a:r2", "info-b:r1"], "B": ["info-c:r1"], "X": ["isolated-info"]}
    dataset = data(["B", "X", "A"], [("A", "N", "i1"), ("N", "B", "i2")], zero=["N"], keys=keys)
    result = run(dataset)
    assert result["ordered_topic_ids"] == ["A", "N", "B", "X"]
    assert result["blocks"]["N"] == []
    assert result["ordered_keys"][:3] == keys["A"]
    assert_invariants(result, dataset)


def test_unrelated_isolated_packages_keep_source_baseline_order_not_identifier_sort():
    dataset = data(["B", "ZZ", "A", "AA"], [("A", "B", "info")])
    result = run(dataset)
    assert result["ordered_keys"] == ["A", "B", "ZZ", "AA"]
    assert all(metric["unknown_isolated_relative_order_stable"] for metric in result["candidate_metrics"].values())


@pytest.mark.parametrize("invalid", ["scope", "missing_start", "conflict", "duplicate", "wrong_members"])
def test_bad_branch_evidence_explicitly_selects_pure_a(invalid):
    branch = Branch("one", SOURCE, QUEST, "A", ("A", "B"))
    branches = [branch]
    if invalid == "scope":
        branches = [replace(branch, source_id="another-source")]
    elif invalid == "missing_start":
        branches = [replace(branch, start_topic="missing")]
    elif invalid == "conflict":
        branches += [Branch("two", SOURCE, QUEST, "B", ("B",))]
    elif invalid == "duplicate":
        branches *= 2
    else:
        branches = [replace(branch, members=None)]
    result = run(data(["B", "X", "A"], [("A", "B", "i1")]), branches)
    assert result["selection_reason"]["selected_candidate"] == "pure_A"
    assert result["ordered_keys"] == result["plugin_only_baselines"]["pure_A"]["ordered_keys"]
    assert any(d["code"] == "branch_preferences_disabled" for d in result["diagnostics"])


@pytest.mark.parametrize("invalid", ["missing_node", "wrong_kind", "scope", "split", "duplicate_key", "ordinal"])
def test_invalid_hard_evidence_preserves_exact_input_items(invalid):
    topics, items, links = data(["B", "A"], [("A", "B", "info")])
    if invalid == "missing_node":
        links = [replace(links[0], target_topic="missing")]
    elif invalid == "wrong_kind":
        topics[0] = replace(topics[0], kind="INFO")
    elif invalid == "scope":
        links[0] = replace(links[0], quest_id="other")
    elif invalid == "split":
        items.append(Item("another-b", "B", SOURCE, QUEST))
    elif invalid == "duplicate_key":
        items.append(items[-1])
    else:
        topics[0] = replace(topics[0], record_ordinal="bad")
    result = run((topics, items, links))
    assert result["status"] == "fallback" and result["diagnostics"]
    assert result["ordered_keys"] == [item.key for item in items]


def test_info_owner_conflicts_are_hard_evidence_failure():
    topics, items, links = data(["B", "A"], [("A", "B", "info")])
    items[0] = replace(items[0], info_id="info")
    result = run((topics, items, links))
    assert result["status"] == "fallback"
    assert any(d["code"] == "conflicting_info_owner" for d in result["diagnostics"])


@pytest.mark.parametrize("invalid", ["item_key", "topic_id", "link_endpoint", "info_id"])
def test_unhashable_invalid_evidence_returns_explicit_fallback(invalid):
    topics, items, links = data(["B", "A"], [("A", "B", "info")])
    if invalid == "item_key":
        items[0] = replace(items[0], key=["invalid", "key"])
    elif invalid == "topic_id":
        topics[0] = replace(topics[0], topic_id=["invalid"])
    elif invalid == "link_endpoint":
        links[0] = replace(links[0], target_topic=["invalid"])
    else:
        links[0] = replace(links[0], source_info=["invalid"])
    result = run((topics, items, links))
    assert result["status"] == "fallback" and result["diagnostics"]
    assert result["ordered_keys"] == [item.key for item in items]


def test_unhashable_branch_and_field_values_disable_soft_preferences():
    dataset = data(["B", "X", "A"], [("A", "B", "info")])
    branch = Branch(["invalid"], SOURCE, QUEST, "A", (["invalid"],))
    field = FieldOrder("A", "info", "B", 1, ["invalid"])
    result = run(dataset, [branch], [field])
    assert result["selection_reason"]["selected_candidate"] == "pure_A"
    assert any(d["code"] == "invalid_branch_evidence" for d in result["diagnostics"])
    assert any(d["code"] == "invalid_tclt_field_order_evidence" for d in result["diagnostics"])


def test_bad_field_evidence_disables_field_preference_and_diagnoses():
    dataset = data(["S", "C", "D"], [("S", "C", "info"), ("S", "D", "info")])
    result = run(dataset, fields=[FieldOrder("S", "info", "D", 1, -1)])
    assert result["candidate_D"]["ordered_topic_ids"] == ["S", "C", "D"]
    assert any(d["code"] == "invalid_tclt_field_order_evidence" for d in result["diagnostics"])


def test_extractor_invalid_preferences_explicitly_force_candidate_a():
    branch = Branch("one", SOURCE, QUEST, "A", ("A", "B"))
    result = run(data(["B", "X", "A"]), [branch], invalid_preferences=True)
    assert result["ordered_keys"] == ["B", "X", "A"]
    assert result["selection_reason"]["selected_candidate"] == "pure_A"
    assert any(d["code"] == "branch_hint_invalid" for d in result["diagnostics"])
    assert any(d["code"] == "branch_preferences_disabled" for d in result["diagnostics"])


def test_equivalent_evidence_enumeration_is_deterministic():
    dataset = data(["B", "X", "A"], [("A", "B", "i1")])
    branch = Branch("one", SOURCE, QUEST, "A", ("A", "B"))
    fields = [FieldOrder("A", "i1", "B", 10, 3)]
    expected = run(dataset, [branch], fields)
    alternate = run((list(reversed(dataset[0])), dataset[1], list(reversed(dataset[2]))), [branch], fields)
    assert alternate["ordered_keys"] == expected["ordered_keys"]
    assert alternate["selection_reason"] == expected["selection_reason"]


def test_component_diagnostics_describe_actual_visible_order():
    result = run(data(["B", "A"], [("A", "B", "i1")]))
    for component in result["weak_components"]:
        members = set(component["baseline_topic_ids"])
        assert component["ordered_visible_topic_ids"] == [
            node for node in result["ordered_topic_ids"] if node in members and result["blocks"][node]
        ]


def test_empty_input_and_all_empty_graph_are_supported():
    assert run(data([]))["ordered_keys"] == []
    dataset = data([], [("B", "A", "i1")], zero=["A", "B"])
    result = run(dataset)
    assert result["ordered_topic_ids"] == ["B", "A"] and result["ordered_keys"] == []
    assert_invariants(result, dataset)


def test_non_string_entry_keys_are_preserved_without_legacy_key_collision():
    first = EntryKey(SourceNamespace.legacy(), "same", "first")
    second = EntryKey(SourceNamespace.legacy(), "same", "second")
    dataset = data(["B", "A"], [("A", "B", "i1")], keys={"A": [first], "B": [second]})
    assert run(dataset)["ordered_keys"] == [first, second]


def test_long_chain_and_many_isolated_nodes_need_no_recursion_or_global_pair_table():
    nodes = [f"n{index}" for index in range(1500)]
    dataset = data(
        list(reversed(nodes)), [(left, right, f"i{index}") for index, (left, right) in enumerate(zip(nodes, nodes[1:]))]
    )
    result = run(dataset)
    assert result["ordered_keys"] == nodes
    assert result["candidate_metrics"]["pure_A"]["baseline_topic_pair_inversions"] == 1500 * 1499 // 2
    assert run(data(nodes))["ordered_keys"] == nodes

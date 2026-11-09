# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Pure plugin-only DIAL block ordering with explicit evidence fallback."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict

from .candidates import order_candidate, reading_packs, validate_preferences
from .graph import condense, validate
from .metrics import measure, score, start_predecessors
from .models import Branch, FieldOrder, Item, Link, Topic


def _algorithm(source_id, quest_id):
    return {
        "name": "plugin-only SCC reading layout",
        "version": 3,
        "reference_assisted": False,
        "xml_used_for_ordering": False,
        "scope": {"source_id": source_id, "quest_id": quest_id},
        "cycle_policy": "SCC internal baseline order",
        "baseline": "first item appearance; zero-output nodes appended by physical record ordinal",
        "hard_edges": "verified TCLT only; no branch/group/exit-order edges added",
        "zero_output_policy": "retain all nodes in graph; emit original keys only",
        "invalid_input_policy": "whole-input fallback; preserve exact input item sequence",
        "plugin_evidence": [
            "INFO.TCLT hard links and original per-INFO field order",
            "DIAL.BNAM branch membership",
            "DLBR.QNAM/SNAM scope and soft start anchor",
        ],
    }


def _render(result, graph, order, links):
    result["ordered_topic_ids"] = order
    result["ordered_keys"] = [key for node in order for key in graph.blocks[node]]
    baseline_visible = [node for node in graph.baseline if graph.blocks[node]]
    visible = [node for node in order if graph.blocks[node]]
    visible_positions = {node: index for index, node in enumerate(visible)}
    result["changed_blocks"] = [
        {
            "topic_id": node,
            "baseline_block_index": index,
            "ordered_block_index": visible_positions[node],
            "key_count": len(graph.blocks[node]),
        }
        for index, node in enumerate(baseline_visible)
        if visible_positions[node] != index
    ]
    result["sccs"] = [
        {
            "id": index,
            "topic_ids": component,
            "cyclic": len(component) > 1 or component[0] in graph.adjacency[component[0]],
        }
        for index, component in enumerate(graph.sccs)
    ]
    position = {node: index for index, node in enumerate(order)}
    for edge in links:
        if graph.owner[edge.source_topic] == graph.owner[edge.target_topic]:
            category = "interior_cycle_edges"
        elif position[edge.source_topic] < position[edge.target_topic]:
            category = "edges_satisfied"
        else:
            category = "edges_unsatisfied"
        result[category].append(asdict(edge))
    weak_owner = {node: index for index, component in enumerate(graph.weak) for node in component}
    ordered_components, visible_components = defaultdict(list), defaultdict(list)
    baseline_slots, ordered_slots = defaultdict(list), defaultdict(list)
    for node in order:
        ordered_components[weak_owner[node]].append(node)
    for index, node in enumerate(baseline_visible):
        baseline_slots[weak_owner[node]].append(index)
    for index, node in enumerate(visible):
        visible_components[weak_owner[node]].append(node)
        ordered_slots[weak_owner[node]].append(index)
    result["weak_components"] = [
        {
            "baseline_topic_ids": component,
            "ordered_topic_ids": ordered_components[index],
            "ordered_visible_topic_ids": visible_components[index],
            "baseline_visible_slots": baseline_slots[index],
            "ordered_visible_slots": ordered_slots[index],
        }
        for index, component in enumerate(graph.weak)
    ]


def plugin_only_layout(
    topics: Iterable[Topic],
    items: Iterable[Item],
    links: Iterable[Link],
    *,
    source_id: str,
    quest_id: str,
    branches: Iterable[Branch] = (),
    field_orders: Iterable[FieldOrder] = (),
    invalid_preferences: bool = False,
) -> dict:
    """Order whole DIAL blocks, comparing exactly frozen candidates A and D.

    Items define the source baseline and remain intact inside a DIAL, including
    INFO and response order. Topics without visible items remain graph nodes.
    Only TCLT links add precedence. Branches and field order affect legal ready
    choices and never introduce edges. Invalid hard evidence preserves the exact
    input item sequence; invalid branch evidence explicitly selects candidate A.
    The function performs no I/O and accepts no reference or translated text.
    """
    topics, items, links = tuple(topics), tuple(items), tuple(links)
    branches, fields = tuple(branches), tuple(field_orders)
    baseline, blocks, diagnostics = validate(topics, items, links, source_id, quest_id)
    keys = [item.key for item in items]
    result = {
        "status": "fallback" if diagnostics else "laid_out",
        "baseline_topic_ids": baseline,
        "ordered_topic_ids": baseline[:],
        "baseline_keys": keys,
        "ordered_keys": keys[:],
        "blocks": blocks,
        "changed_blocks": [],
        "sccs": [],
        "weak_components": [],
        "edges_satisfied": [],
        "edges_unsatisfied": [],
        "interior_cycle_edges": [],
        "diagnostics": diagnostics,
        "reference_assisted": False,
        "xml_used_for_ordering": False,
        "algorithm": _algorithm(source_id, quest_id),
    }
    if diagnostics:
        return result
    graph = condense(baseline, blocks, links)
    original_branches = branches
    branches, membership, fields, branch_diagnostics, field_diagnostics = validate_preferences(
        graph, links, branches, fields, source_id, quest_id
    )
    if invalid_preferences:
        branches, membership = (), {}
        branch_diagnostics.append({"code": "branch_hint_invalid"})
        if not any(item["code"] == "branch_preferences_disabled" for item in branch_diagnostics):
            branch_diagnostics.append({"code": "branch_preferences_disabled", "fallback": "pure_A"})
    packs = reading_packs(graph, branches)
    a_order, _, _ = order_candidate(graph, graph.weak)
    d_order, trace, soft_diagnostics = order_candidate(
        graph, packs, branches=branches, membership=membership, fields=fields, prefer_frontier=True
    )
    orders = {"pure_A": a_order, "D": d_order}
    predecessors = start_predecessors(graph, branches, orders.values())
    metrics = {name: measure(graph, order, branches, links, packs, predecessors) for name, order in orders.items()}
    eligible = [
        name
        for name, metric in metrics.items()
        if not metric["hard_edges_unsatisfied"]
        and metric["unrelated_pack_first_order_stable"]
        and metric["unknown_isolated_relative_order_stable"]
    ]
    # A is always eligible: components/packs retain earliest source appearance.
    selected = "pure_A" if branch_diagnostics else min(eligible, key=lambda name: score(metrics[name], name))
    _render(result, graph, orders[selected], links)
    result["diagnostics"].extend(branch_diagnostics + field_diagnostics)
    result["algorithm"]["mode"] = (
        "pure_A_weak_component_contiguous" if selected == "pure_A" else "D_branch_entry_and_ready_frontier"
    )
    if selected == "D":
        result["diagnostics"].extend(soft_diagnostics)
        result["algorithm"].update({"ready_choice_trace": trace, "reading_packs": packs})
    result["candidate_metrics"] = metrics
    result["selection_reason"] = {
        "selected_candidate": selected,
        "policy": "choice-v3 fixed lexicographic plugin-only metrics",
        "score_vectors": {name: list(score(metric, name)) for name, metric in metrics.items()},
        "no_reference_feedback": True,
    }
    a_keys = result["ordered_keys"] if selected == "pure_A" else [key for node in a_order for key in blocks[node]]
    d_keys = result["ordered_keys"] if selected == "D" else [key for node in d_order for key in blocks[node]]
    result["plugin_only_baselines"] = {
        "pure_A": {"ordered_topic_ids": a_order, "ordered_keys": a_keys, "metrics": metrics["pure_A"]}
    }
    result["candidate_D"] = {
        "ordered_topic_ids": d_order,
        "ordered_keys": d_keys,
        "metrics": metrics["D"],
        "diagnostics": branch_diagnostics + field_diagnostics + soft_diagnostics,
    }
    result["plugin_evidence"] = {
        "branches": [asdict(branch) for branch in original_branches],
        "original_info_tclt_field_order": [asdict(field) for field in fields],
    }
    return result

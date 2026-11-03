"""Frozen plugin-only selection metrics without an all-pairs reachability table."""

from .graph import Graph


def inversions(values):
    """Count baseline inversions in O(n log n) time and O(n) space."""
    tree, result = [0] * (len(values) + 1), 0
    for count, value in enumerate(values):
        index, prefix = value + 1, 0
        while index:
            prefix += tree[index]
            index -= index & -index
        result += count - prefix
        index = value + 1
        while index < len(tree):
            tree[index] += 1
            index += index & -index
    return result


def start_predecessors(graph: Graph, branches, orders):
    """Retain only member/start queries, never an all-node reachability table."""
    targets = {}
    positions = [{node: index for index, node in enumerate(order)} for order in orders]
    for branch in branches:
        start = graph.owner[branch.start_topic]
        targets.setdefault(start, set()).update(
            graph.owner[node]
            for node in branch.members
            if graph.owner[node] != start
            and any(position[node] < position[branch.start_topic] for position in positions)
        )
    result = {}
    for start, members in targets.items():
        if not members:
            result[start] = set()
            continue
        seen, stack = set(), list(graph.reverse[start])
        while stack:
            scc = stack.pop()
            if scc not in seen:
                seen.add(scc)
                stack.extend(graph.reverse[scc] - seen)
        result[start] = members & seen
    return result


def measure(graph, order, branches, links, packs, predecessors):
    position = {node: index for index, node in enumerate(order)}
    fragments, late_starts = 0, 0
    branch_nodes = {node for branch in branches for node in branch.members}
    for branch in branches:
        indices = sorted(position[node] for node in branch.members)
        fragments += sum(right != left + 1 for left, right in zip(indices, indices[1:]))
        start = graph.owner[branch.start_topic]
        late_starts += sum(
            position[node] < position[branch.start_topic]
            for node in branch.members
            if graph.owner[node] != start and graph.owner[node] not in predecessors[start]
        )
    unique_edges = {
        (edge.source_topic, edge.target_topic)
        for edge in links
        if graph.owner[edge.source_topic] != graph.owner[edge.target_topic]
    }
    pack_owner = {node: index for index, pack in enumerate(packs) for node in pack}
    first_packs = list(dict.fromkeys(pack_owner[node] for node in order))
    expected_packs = list(dict.fromkeys(pack_owner[node] for node in graph.baseline))
    connected = {node for edge in links for node in (edge.source_topic, edge.target_topic)}
    isolated = set(graph.baseline) - connected - branch_nodes
    visible = [node for node in graph.baseline if graph.blocks[node]]
    # Visible block movement ignores empty graph nodes, as the emitted input does.
    ordered_visible = {node: index for index, node in enumerate(node for node in order if graph.blocks[node])}
    satisfied = sum(
        position[left] < position[right]
        for left, right in (
            (edge.source_topic, edge.target_topic)
            for edge in links
            if graph.owner[edge.source_topic] != graph.owner[edge.target_topic]
        )
    )
    cross_count = sum(graph.owner[edge.source_topic] != graph.owner[edge.target_topic] for edge in links)
    return {
        "hard_edges_satisfied": satisfied,
        "hard_edges_unsatisfied": cross_count - satisfied,
        "cycle_internal_edges": len(links) - cross_count,
        "branch_extra_fragments": fragments,
        "avoidable_branch_start_late_pairs": late_starts,
        "cross_scc_link_adjacencies": sum(position[right] == position[left] + 1 for left, right in unique_edges),
        "cross_scc_link_total_gap": sum(position[right] - position[left] - 1 for left, right in unique_edges),
        "unrelated_pack_first_order_stable": first_packs == expected_packs,
        "unknown_isolated_relative_order_stable": (
            [node for node in graph.baseline if node in isolated] == [node for node in order if node in isolated]
        ),
        "baseline_topic_pair_inversions": inversions([position[node] for node in graph.baseline]),
        "changed_blocks": sum(ordered_visible[node] != index for index, node in enumerate(visible)),
    }


def score(metric, name):
    return (
        metric["branch_extra_fragments"],
        metric["avoidable_branch_start_late_pairs"],
        -metric["cross_scc_link_adjacencies"],
        metric["cross_scc_link_total_gap"],
        metric["baseline_topic_pair_inversions"],
        name != "pure_A",
    )

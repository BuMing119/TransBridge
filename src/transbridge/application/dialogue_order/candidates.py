# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""The two frozen plugin-only reading candidates, with soft ready preferences."""

from collections import defaultdict
from dataclasses import asdict
import heapq

from .graph import Graph, Groups, identifier, ordinal


def validate_preferences(graph, links, branches, fields, source_id, quest_id):
    membership, branch_ids, branch_diagnostics = {}, set(), []
    for branch in branches:
        members_valid = isinstance(branch.members, tuple | list) and all(identifier(node) for node in branch.members)
        valid = (
            (branch.source_id, branch.quest_id) == (source_id, quest_id)
            and identifier(branch.branch_id)
            and branch.branch_id not in branch_ids
            and members_valid
            and bool(branch.members)
            and branch.start_topic in branch.members
            and len(set(branch.members)) == len(branch.members)
            and all(node in graph.rank for node in branch.members)
        )
        if identifier(branch.branch_id):
            branch_ids.add(branch.branch_id)
        if not valid:
            branch_diagnostics.append({"code": "invalid_branch_evidence", "branch_id": branch.branch_id})
        if not members_valid:
            continue
        for node in branch.members:
            if node in membership and membership[node] != branch.branch_id:
                branch_diagnostics.append({"code": "conflicting_branch_membership", "topic_id": node})
            membership[node] = branch.branch_id
    if branch_diagnostics:
        branches, membership = (), {}
        branch_diagnostics.append({"code": "branch_preferences_disabled", "fallback": "pure_A"})
    hard_keys = {(edge.source_topic, edge.source_info, edge.target_topic) for edge in links}
    field_diagnostics, seen_fields, info_ordinals = [], set(), {}
    for field in fields:
        if (
            not all(identifier(node) for node in (field.source_topic, field.source_info, field.target_topic))
            or not ordinal(field.field_index)
            or not ordinal(field.info_record_ordinal)
        ):
            field_diagnostics.append({"code": "invalid_tclt_field_order_evidence", "field": asdict(field)})
            continue
        key = (field.source_info, field.field_index)
        previous = info_ordinals.setdefault(field.source_info, field.info_record_ordinal)
        if (
            (field.source_topic, field.source_info, field.target_topic) not in hard_keys
            or not ordinal(field.field_index)
            or not ordinal(field.info_record_ordinal)
            or previous != field.info_record_ordinal
            or key in seen_fields
        ):
            field_diagnostics.append({"code": "invalid_tclt_field_order_evidence", "field": asdict(field)})
        seen_fields.add(key)
    if field_diagnostics:
        fields = ()
    return branches, membership, fields, branch_diagnostics, field_diagnostics


def reading_packs(graph: Graph, branches):
    groups = Groups(graph.baseline)
    for component in graph.weak:
        for node in component[1:]:
            groups.join(component[0], node)
    for branch in branches:
        for node in branch.members[1:]:
            groups.join(branch.members[0], node)
    return groups.ordered(graph.baseline)


class _Ready:
    """Lazy heaps keep unrelated ready sets from introducing quadratic scans."""

    def __init__(self, starts, scc_branches):
        self.nodes = set()
        self.all = []
        self.starts = []
        self.branches = defaultdict(list)
        self.start_ids = starts
        self.scc_branches = scc_branches

    def add(self, scc):
        self.nodes.add(scc)
        heapq.heappush(self.all, scc)
        if scc in self.start_ids:
            heapq.heappush(self.starts, scc)
        for branch in self.scc_branches.get(scc, ()):
            heapq.heappush(self.branches[branch], scc)

    def first(self, heap):
        while heap and heap[0] not in self.nodes:
            heapq.heappop(heap)
        return heap[0] if heap else None


def order_candidate(graph: Graph, packs, *, branches=(), membership=None, fields=(), prefer_frontier=False):
    membership = membership or {}
    starts = {graph.owner[branch.start_topic] for branch in branches}
    branch_by_id = {branch.branch_id: branch for branch in branches}
    scc_branches = {
        scc: {membership[node] for node in nodes if node in membership} for scc, nodes in enumerate(graph.sccs)
    }
    sequences = defaultdict(lambda: defaultdict(list))
    sequence_seen = defaultdict(set)
    for field in sorted(fields, key=lambda item: (item.info_record_ordinal, item.field_index)):
        source, target = graph.owner[field.source_topic], graph.owner[field.target_topic]
        key = source, field.source_info
        if source != target and target not in sequence_seen[key]:
            sequences[source][field.source_info].append(target)
            sequence_seen[key].add(target)
    order, trace, diagnostics = [], [], []
    for pack in packs:
        scc_ids = {graph.owner[node] for node in pack}
        indegree = {scc: len(graph.reverse[scc]) for scc in scc_ids}
        ready = _Ready(starts, scc_branches)
        for scc in scc_ids:
            if indegree[scc] == 0:
                ready.add(scc)
        previous, active_branch = None, None
        while ready.nodes:
            chosen, reason = None, "baseline_ready_scc"
            if prefer_frontier and previous is not None:
                exits = [
                    [target for target in sequence if target in ready.nodes]
                    for sequence in sequences[previous].values()
                ]
                exits = [sequence for sequence in exits if sequence]
                if exits and all(sequence == exits[0] for sequence in exits):
                    chosen, reason = exits[0][0], "recent_ready_link_original_info_order"
                elif exits:
                    chosen = min(target for sequence in exits for target in sequence)
                    reason = "recent_ready_link_multi_info_baseline"
                    diagnostics.append({"code": "multi_info_exit_order_baseline_fallback", "scc": previous})
                else:
                    chosen = min(graph.dag[previous] & ready.nodes, default=None)
                    reason = "recent_ready_link_baseline"
            if chosen is None and prefer_frontier:
                active_start = graph.owner[branch_by_id[active_branch].start_topic] if active_branch else None
                if active_start in ready.nodes:
                    chosen, reason = active_start, "ready_current_branch_start"
                elif active_branch and (member := ready.first(ready.branches[active_branch])) is not None:
                    chosen, reason = member, "current_branch_ready_member"
                elif (start := ready.first(ready.starts)) is not None:
                    chosen, reason = start, "explicit_ready_branch_start"
            if chosen is None:
                chosen, reason = ready.first(ready.all), "baseline_ready_scc"
            ready.nodes.remove(chosen)
            order.extend(graph.sccs[chosen])
            if prefer_frontier:
                trace.append({"scc": chosen, "topic_ids": graph.sccs[chosen], "reason": reason})
            active_branch = next(iter(scc_branches[chosen])) if len(scc_branches[chosen]) == 1 else None
            previous = chosen
            for target in graph.dag[chosen]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.add(target)
    return order, trace, diagnostics

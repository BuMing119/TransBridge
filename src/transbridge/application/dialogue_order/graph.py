"""Validate a scoped dialogue graph and condense its true TCLT constraints."""

from collections import defaultdict
from collections.abc import Hashable
from dataclasses import asdict, dataclass

from .models import Item, Link, Topic


def identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def ordinal(value: object) -> bool:
    return type(value) is int and value >= 0


def _hashable(value):
    try:
        hash(value)
    except TypeError:
        return False
    return True


@dataclass(slots=True)
class Graph:
    baseline: list[str]
    blocks: dict[str, list[Hashable]]
    sccs: list[list[str]]
    owner: dict[str, int]
    dag: dict[int, set[int]]
    reverse: dict[int, set[int]]
    weak: list[list[str]]
    adjacency: dict[str, set[str]]
    rank: dict[str, int]


class Groups:
    """Disjoint sets for undirected display grouping, never causal constraints."""

    def __init__(self, nodes):
        self.parent = {node: node for node in nodes}
        self.size = dict.fromkeys(self.parent, 1)

    def find(self, node):
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != node:
            parent = self.parent[node]
            self.parent[node] = root
            node = parent
        return root

    def join(self, left, right):
        left, right = self.find(left), self.find(right)
        if left == right:
            return
        if self.size[left] < self.size[right]:
            left, right = right, left
        self.parent[right] = left
        self.size[left] += self.size[right]

    def ordered(self, baseline):
        groups = defaultdict(list)
        for node in baseline:
            groups[self.find(node)].append(node)
        return list(groups.values())


def validate(topics: tuple[Topic, ...], items: tuple[Item, ...], links: tuple[Link, ...], source_id, quest_id):
    """Return exact input blocks and diagnostics before touching graph endpoints."""
    shape_errors = []
    for topic in topics:
        if not identifier(topic.topic_id):
            shape_errors.append({"code": "invalid_topic_evidence", "topic_id": topic.topic_id})
    for item in items:
        if not _hashable(item.key) or not identifier(item.topic_id) or not isinstance(item.info_id, str):
            shape_errors.append({"code": "invalid_item_evidence", "key": item.key})
    for link in links:
        if not all(identifier(node) for node in (link.source_topic, link.target_topic)):
            shape_errors.append({"code": "invalid_link_evidence", "edge": asdict(link)})
        if not isinstance(link.source_info, str):
            shape_errors.append({"code": "invalid_info_identity", "edge": asdict(link)})
    if shape_errors:
        # Preserve every original key; malformed identifiers cannot index a graph.
        safe_blocks = {topic.topic_id: [] for topic in topics if identifier(topic.topic_id)}
        visible, seen = [], set()
        for item in items:
            if identifier(item.topic_id):
                if item.topic_id not in seen:
                    visible.append(item.topic_id)
                    seen.add(item.topic_id)
                safe_blocks.setdefault(item.topic_id, []).append(item.key)
        visible.extend(node for node in safe_blocks if node not in seen)
        return visible, safe_blocks, shape_errors
    diagnostics, visible, seen, blocks = [], [], set(), {}
    by_id = {topic.topic_id: topic for topic in topics}
    if not identifier(source_id) or not identifier(quest_id):
        diagnostics.append({"code": "source_scope_missing"})
    if len(by_id) != len(topics):
        diagnostics.append({"code": "duplicate_topic_id"})
    for topic in topics:
        blocks[topic.topic_id] = []
        if not identifier(topic.topic_id) or not ordinal(topic.record_ordinal):
            diagnostics.append({"code": "invalid_topic_evidence", "topic_id": topic.topic_id})
        if topic.kind != "DIAL":
            diagnostics.append({"code": "wrong_type_node", "topic_id": topic.topic_id, "kind": topic.kind})
    last = None
    info_owners = {}
    for item in items:
        blocks.setdefault(item.topic_id, []).append(item.key)
        if item.topic_id != last:
            if item.topic_id in seen:
                diagnostics.append({"code": "noncontiguous_input_blocks", "topic_id": item.topic_id})
            else:
                visible.append(item.topic_id)
            seen.add(item.topic_id)
            last = item.topic_id
        if item.topic_id not in by_id:
            diagnostics.append({"code": "missing_item_topic", "key": item.key, "topic_id": item.topic_id})
        if item.info_id:
            previous = info_owners.setdefault(item.info_id, item.topic_id)
            if previous != item.topic_id:
                diagnostics.append({"code": "conflicting_info_owner", "source_info": item.info_id})
    if len({item.key for item in items}) != len(items):
        diagnostics.append({"code": "duplicate_input_key"})
    for obj in (*topics, *items, *links):
        if (obj.source_id, obj.quest_id) != (source_id, quest_id):
            diagnostics.append({"code": "scope_conflict", "object": asdict(obj)})
    for link in links:
        if link.kind != "TCLT":
            diagnostics.append({"code": "unsupported_edge_type", "edge": asdict(link)})
        if not isinstance(link.source_info, str):
            diagnostics.append({"code": "invalid_info_identity", "edge": asdict(link)})
        if link.source_info:
            previous = info_owners.setdefault(link.source_info, link.source_topic)
            if previous != link.source_topic:
                diagnostics.append({"code": "conflicting_info_owner", "source_info": link.source_info})
        for endpoint in (link.source_topic, link.target_topic):
            if endpoint not in by_id:
                diagnostics.append({"code": "missing_edge_node", "topic_id": endpoint, "edge": asdict(link)})
    # Invalid ordinals still receive a deterministic fallback representation.
    zero = sorted(
        (topic for topic in topics if topic.topic_id not in seen),
        key=lambda topic: (topic.record_ordinal if ordinal(topic.record_ordinal) else -1, str(topic.topic_id)),
    )
    return visible + [topic.topic_id for topic in zero], blocks, diagnostics


def condense(baseline, blocks, links) -> Graph:
    rank = {node: index for index, node in enumerate(baseline)}
    adjacency = {node: set() for node in baseline}
    reverse = {node: set() for node in baseline}
    groups = Groups(baseline)
    for edge in links:
        adjacency[edge.source_topic].add(edge.target_topic)
        reverse[edge.target_topic].add(edge.source_topic)
        groups.join(edge.source_topic, edge.target_topic)
    # Iterative Kosaraju handles long chains without recursion limits.
    visited, finishing = set(), []
    for start in baseline:
        if start in visited:
            continue
        stack = [(start, False)]
        while stack:
            node, finish = stack.pop()
            if finish:
                finishing.append(node)
            elif node not in visited:
                visited.add(node)
                stack.append((node, True))
                stack.extend(
                    (neighbor, False)
                    for neighbor in sorted(adjacency[node], key=rank.get, reverse=True)
                    if neighbor not in visited
                )
    sccs, assigned = [], set()
    for start in reversed(finishing):
        if start in assigned:
            continue
        component, stack = [], [start]
        assigned.add(start)
        while stack:
            node = stack.pop()
            component.append(node)
            for neighbor in reverse[node]:
                if neighbor not in assigned:
                    assigned.add(neighbor)
                    stack.append(neighbor)
        sccs.append(sorted(component, key=rank.get))
    sccs.sort(key=lambda component: rank[component[0]])
    owner = {node: index for index, component in enumerate(sccs) for node in component}
    dag = {index: set() for index in range(len(sccs))}
    dag_reverse = {index: set() for index in range(len(sccs))}
    for source, targets in adjacency.items():
        for target in targets:
            left, right = owner[source], owner[target]
            if left != right:
                dag[left].add(right)
                dag_reverse[right].add(left)
    return Graph(baseline, blocks, sccs, owner, dag, dag_reverse, groups.ordered(baseline), adjacency, rank)

"""Plugin evidence accepted by the pure dialogue reading-order algorithm.

Identifiers are scoped to one source plugin and quest. These records deliberately
contain no XML ranks, translated text, or display names.
"""

from collections.abc import Hashable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Topic:
    topic_id: str
    source_id: str
    quest_id: str
    record_ordinal: int
    kind: str = "DIAL"


@dataclass(frozen=True, slots=True)
class Item:
    key: Hashable
    topic_id: str
    source_id: str
    quest_id: str
    info_id: str = ""


@dataclass(frozen=True, slots=True)
class Link:
    source_topic: str
    target_topic: str
    source_id: str
    quest_id: str
    source_info: str = ""
    kind: str = "TCLT"


@dataclass(frozen=True, slots=True)
class Branch:
    branch_id: str
    source_id: str
    quest_id: str
    start_topic: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FieldOrder:
    source_topic: str
    source_info: str
    target_topic: str
    info_record_ordinal: int
    field_index: int

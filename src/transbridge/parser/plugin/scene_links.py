# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Decode explicit SCEN quest and ordered dialogue-action references."""

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass
class _DialogueAction:
    topics: list[str] = field(default_factory=list)
    phases: dict[str, int] = field(default_factory=dict)
    closed: bool = False

    def has_valid_interval(self, phase_limit: int) -> bool:
        return self.closed and 0 <= self.phases.get("SNAM", -1) <= self.phases.get("ENAM", -1) < phase_limit


def scene_links(fields: Iterable[tuple[str, bytes]]) -> tuple[str, tuple[str, ...]]:
    quest_id = ""
    actions: list[_DialogueAction] = []
    in_action = False
    dialogue = None
    phase_markers = 0
    for name, data in fields:
        if name == "ANAM":
            # A uint16 starts an action; an empty ANAM closes it. PNAM inside
            # a package action points to PACK, whereas outside it points to QUST.
            if dialogue is not None:
                dialogue.closed = not data
            in_action = bool(data)
            dialogue = _DialogueAction() if data == b"\0\0" else None
            if dialogue is not None:
                actions.append(dialogue)
        elif name == "PNAM" and not in_action:
            quest_id = _reference(data)
        elif name == "HNAM" and not in_action:
            # Each declared phase starts and ends with an HNAM delimiter.
            phase_markers += 1
        elif dialogue is not None and name in {"SNAM", "ENAM"}:
            # SNAM in a timer action has a different type and meaning.
            value = int.from_bytes(data, "little") if len(data) == 4 else -1
            dialogue.phases[name] = value if name not in dialogue.phases else -1
        elif dialogue is not None and name == "DATA":
            topic_id = _reference(data)
            if topic_id:
                dialogue.topics.append(topic_id)
    speaking = [action for action in actions if action.topics]
    phase_limit = phase_markers // 2 if phase_markers else 0xFFFFFFFF
    if phase_markers % 2 == 0 and all(action.has_valid_interval(phase_limit) for action in speaking):
        speaking.sort(key=lambda action: action.phases["SNAM"])
    # Deduplicate after sorting so a reused topic occupies its earliest reading position.
    return quest_id, tuple(dict.fromkeys(topic for action in speaking for topic in action.topics))


def _reference(data: bytes) -> str:
    value = int.from_bytes(data, "little") if len(data) == 4 else 0
    return f"{value:08X}" if value else ""

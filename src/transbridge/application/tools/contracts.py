"""Transport-neutral contracts for tool invocation and observations."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """An immutable request identity used by authorization decisions."""

    tool_name: str
    arguments: Mapping[str, Any]
    owner_id: str
    plan_hash: str = ""
    _request_hash: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        arguments = deepcopy(dict(self.arguments))
        object.__setattr__(self, "arguments", arguments)
        payload = {
            "arguments": arguments,
            "owner_id": self.owner_id,
            "plan_hash": self.plan_hash,
            "tool_name": self.tool_name,
        }
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        object.__setattr__(self, "_request_hash", digest)

    @property
    def request_hash(self) -> str:
        return self._request_hash


@dataclass(frozen=True, slots=True)
class StructuredObservation:
    """Full structured result plus an independently bounded display summary."""

    tool_name: str
    result: Mapping[str, Any]
    display_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "result": dict(self.result),
            "display_summary": self.display_summary,
        }

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any
from uuid import uuid4


class ConversationManager:
    """Preserve complete evidence separately from the recent model projection.

    Only explicit user input opens a turn. Tool/confirmation continuations stay
    in their original turn; the configured turn limit never deletes history.
    """

    _OBSERVATION_PREFIX = "[Tool result - {name}]\n"
    _PLAN_RESULT_PREFIX = "[Plan execution completed]"
    _MAX_OBSERVATION_CHARS = 2000

    def __init__(self, max_turns: int = 20) -> None:
        self._messages: list[dict[str, Any]] = []
        self._message_ids: list[str] = []
        self._max_turns: int = max_turns
        # M3: 预记录每轮起始位置 (user 消息在 _messages 中的索引)
        self._turn_starts: list[int] = []
        # m5: 消息缓存 -- 仅在消息变更时重建副本
        self._messages_dirty: bool = True
        self._messages_cache: list[dict[str, Any]] = []
        self._loaded_tool_namespaces: set[str] = set()

    def _append(self, message: dict[str, Any], *, message_id: str | None = None) -> None:
        mid = message_id or str(uuid4())
        if mid in self._message_ids:
            raise ValueError(f"Conversation history already contains message ID {mid}")
        self._messages.append(deepcopy(message))
        self._message_ids.append(mid)
        self._messages_dirty = True

    def add_system(self, content: str) -> None:
        """system 消息始终在列表最前（索引 0），替换已有 system 消息。"""
        system_indices = [i for i, message in enumerate(self._messages) if message["role"] == "system"]
        unchanged = system_indices and self._messages[system_indices[0]].get("content") == content
        system_id = self._message_ids[system_indices[0]] if unchanged else str(uuid4())
        self._message_ids = [mid for i, mid in enumerate(self._message_ids) if i not in system_indices]
        self._messages = [m for m in self._messages if m["role"] != "system"]
        self._messages.insert(0, {"role": "system", "content": content})
        self._message_ids.insert(0, system_id)
        self._messages_dirty = True
        self._turn_starts = [start - sum(i < start for i in system_indices) + 1 for start in self._turn_starts]

    def add_user(self, content: str, *, message_id: str | None = None) -> None:
        # Record the turn only after an accepted append; a duplicate ingress ID
        # must not change either history or its projection boundary.
        start = len(self._messages)
        self._append({"role": "user", "content": content}, message_id=message_id)
        self._turn_starts.append(start)

    def add_assistant(self, content: str) -> None:
        self._append({"role": "assistant", "content": content})

    def add_assistant_turn(self, turn) -> None:
        """Persist a provider-neutral assistant turn, including native calls."""
        to_message = getattr(turn, "to_assistant_message", None)
        if callable(to_message):
            message = to_message()
        elif isinstance(turn, dict):
            message = dict(turn)
            message["role"] = "assistant"
        else:
            self.add_assistant(str(turn))
            return
        new_call_ids = {str(call.get("id", "")) for call in message.get("tool_calls", []) if str(call.get("id", ""))}
        historical_call_ids = {
            str(call.get("id", ""))
            for historical in self._messages
            if historical.get("role") == "assistant"
            for call in historical.get("tool_calls", [])
            if str(call.get("id", ""))
        }
        reused_call_ids = new_call_ids & historical_call_ids
        if reused_call_ids:
            from transbridge.infra.llm_tool_calling import LlmToolProtocolError

            reused = ", ".join(sorted(reused_call_ids))
            raise LlmToolProtocolError(f"The model reused historical tool call ids: {reused}")
        self._append(message)

    def add_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Any,
        *,
        display_summary: str = "",
        is_error: bool = False,
        message_id: str | None = None,
    ) -> None:
        """Close one native tool call, retaining the complete JSON evidence."""
        if any(
            message.get("role") == "tool" and str(message.get("tool_call_id", "")) == tool_call_id
            for message in self._messages
        ):
            return
        payload = result if isinstance(result, dict) else {"message": str(result)}
        content = json.dumps(payload, ensure_ascii=False, default=str)
        self._append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": content,
                "display_summary": display_summary,
                "is_error": is_error,
            },
            message_id=message_id,
        )

    def close_pending_tool_calls(self, reason: str = "工具调用已取消。") -> int:
        """Add synthetic error results for every unresolved native call."""
        resolved = {str(message.get("tool_call_id", "")) for message in self._messages if message.get("role") == "tool"}
        pending: list[tuple[str, str]] = []
        for message in self._messages:
            if message.get("role") != "assistant":
                continue
            for call in message.get("tool_calls", []):
                call_id = str(call.get("id", ""))
                if call_id and call_id not in resolved:
                    pending.append((call_id, str(call.get("name", "?"))))
        for call_id, tool_name in pending:
            self.add_tool_result(
                call_id,
                tool_name,
                {"success": False, "message": reason},
                display_summary=reason,
                is_error=True,
            )
        return len(pending)

    def load_tool_namespaces(self, namespaces: list[str] | tuple[str, ...] | set[str]) -> None:
        self._loaded_tool_namespaces.update(namespace.strip() for namespace in namespaces if namespace.strip())

    def get_loaded_tool_namespaces(self) -> tuple[str, ...]:
        return tuple(sorted(self._loaded_tool_namespaces))

    def add_observation(self, tool_name: str, result: str) -> None:
        """Keep the complete legacy result; only model projections may shorten it."""
        full_prefix = self._OBSERVATION_PREFIX.format(name=tool_name)
        self._append({
            "role": "user",
            "content": f"{full_prefix}{result}",
        })

    def add_plan_result(self, summary: str, *, success: bool = True, results: list[dict] | None = None) -> None:
        """Append a complete plan outcome, closing its native call when present."""
        pending_plan = self._latest_unresolved_call("propose_plan")
        if pending_plan is not None:
            self.add_tool_result(
                pending_plan,
                "propose_plan",
                {"success": success, "summary": summary, "results": results or []},
                display_summary=f"{self._PLAN_RESULT_PREFIX}\n{summary}",
                is_error=not success,
            )
            return
        self._append({"role": "user", "content": f"{self._PLAN_RESULT_PREFIX}\n{summary}"})

    def get_messages(self) -> list[dict[str, Any]]:
        """Return the recent projection; use get_history() for storage/display."""
        if self._messages_dirty:
            start = self._turn_starts[-self._max_turns] if 0 < self._max_turns < len(self._turn_starts) else 0
            indices = [i for i in range(len(self._messages)) if i >= start or self._messages[i]["role"] == "system"]
            self._messages_cache = [deepcopy(self._messages[i]) for i in indices]
            self._messages_dirty = False
        return deepcopy(self._messages_cache)

    def get_history(self) -> list[dict[str, Any]]:
        """Return complete original messages, including those outside the window."""
        return deepcopy(self._messages)

    def get_transcript(self) -> list[dict[str, Any]]:
        """Return complete messages with stable IDs for projections and lookup."""
        return [dict(deepcopy(message), message_id=mid) for message, mid in zip(self._messages, self._message_ids)]

    def clear(self) -> None:
        self._messages.clear()
        self._message_ids.clear()
        self._turn_starts.clear()
        self._messages_dirty = True
        self._messages_cache.clear()
        self._loaded_tool_namespaces.clear()

    # ── 序列化 (FR13) ──────────────────────────────────────

    def to_dict(self) -> dict:
        """导出消息列表为可序列化的字典。"""
        return {
            "messages": self.get_history(),
            "message_ids": list(self._message_ids),
            "loaded_tool_namespaces": list(self.get_loaded_tool_namespaces()),
        }

    def from_dict(self, data: dict) -> None:
        """从字典恢复消息列表。替换现有消息并重置轮次索引。"""
        self._messages = deepcopy(list(data.get("messages", [])))
        saved_ids = data.get("message_ids", [])
        self._message_ids = [
            str(message.pop("message_id", "") or (saved_ids[i] if i < len(saved_ids) else "") or uuid4())
            for i, message in enumerate(self._messages)
        ]
        if len(set(self._message_ids)) != len(self._message_ids):
            raise ValueError("Conversation history contains duplicate message IDs")
        self._loaded_tool_namespaces = set(data.get("loaded_tool_namespaces", []))
        self._turn_starts = []
        self._messages_dirty = True
        # 重建轮次索引
        successful_results = {
            str(message.get("tool_call_id", ""))
            for message in self._messages
            if message.get("role") == "tool" and not message.get("is_error", False)
        }
        for i, msg in enumerate(self._messages):
            if msg.get("role") == "user" and not self._is_legacy_observation(msg):
                self._turn_starts.append(i)
            if msg.get("role") == "assistant":
                for call in msg.get("tool_calls", []):
                    if call.get("name") != "get_tool_help" or str(call.get("id", "")) not in successful_results:
                        continue
                    args = call.get("arguments", {})
                    for namespace in str(args.get("namespace", "")).split(","):
                        if namespace.strip():
                            self._loaded_tool_namespaces.add(namespace.strip())
        self.close_pending_tool_calls("会话恢复时取消了未完成的工具调用。")

    @classmethod
    def _is_legacy_observation(cls, message: dict[str, Any]) -> bool:
        content = str(message.get("content", ""))
        observation_prefix = cls._OBSERVATION_PREFIX.partition("{name}")[0]
        return content.startswith(("【工具执行结果", observation_prefix, cls._PLAN_RESULT_PREFIX))

    def _latest_unresolved_call(self, tool_name: str) -> str | None:
        resolved = {str(message.get("tool_call_id", "")) for message in self._messages if message.get("role") == "tool"}
        for message in reversed(self._messages):
            if message.get("role") != "assistant":
                continue
            for call in reversed(message.get("tool_calls", [])):
                call_id = str(call.get("id", ""))
                if call.get("name") == tool_name and call_id and call_id not in resolved:
                    return call_id
        return None

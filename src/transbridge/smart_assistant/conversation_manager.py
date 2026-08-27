from __future__ import annotations

import json
from typing import Any


class ConversationManager:
    """维护多轮对话历史，封装 message list 操作。

    M10: _trim 按轮次裁剪，每轮包含 user -> assistant -> [observation*] -> [plan_result]
    M12: add_observation 结果超过 _MAX_OBSERVATION_CHARS 字符自动截断
    M3: turn_starts 预记录轮次起始位置，_trim 基于记录裁剪，不依赖遍历扫描消息顺序
    m5: _messages_cache 缓存 get_messages() 返回值，仅在消息变更时重建
    """

    _OBSERVATION_PREFIX = "[Tool result - {name}]\n"
    _PLAN_RESULT_PREFIX = "[Plan execution completed]"
    _MAX_OBSERVATION_CHARS = 2000

    def __init__(self, max_turns: int = 20) -> None:
        self._messages: list[dict[str, Any]] = []
        self._max_turns: int = max_turns
        # M3: 预记录每轮起始位置 (user 消息在 _messages 中的索引)
        self._turn_starts: list[int] = []
        # m5: 消息缓存 -- 仅在消息变更时重建副本
        self._messages_dirty: bool = True
        self._messages_cache: list[dict[str, Any]] = []
        self._loaded_tool_namespaces: set[str] = set()

    def add_system(self, content: str) -> None:
        """system 消息始终在列表最前（索引 0），替换已有 system 消息。"""
        had_system = any(m["role"] == "system" for m in self._messages)
        self._messages = [m for m in self._messages if m["role"] != "system"]
        self._messages.insert(0, {"role": "system", "content": content})
        self._messages_dirty = True
        # M3: 新增 system 消息会改变索引 0，所有 turn_starts 后移 1 位
        if not had_system:
            self._turn_starts = [s + 1 for s in self._turn_starts]

    def add_user(self, content: str) -> None:
        # M3: 记录此轮起始位置
        self._turn_starts.append(len(self._messages))
        self._messages.append({"role": "user", "content": content})
        self._messages_dirty = True
        self._trim()

    def add_assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})
        self._messages_dirty = True

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
        self._messages.append(message)
        self._messages_dirty = True

    def add_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Any,
        *,
        display_summary: str = "",
        is_error: bool = False,
    ) -> None:
        """Close one native tool call with bounded, valid JSON content."""
        if any(
            message.get("role") == "tool" and str(message.get("tool_call_id", "")) == tool_call_id
            for message in self._messages
        ):
            return
        payload = result if isinstance(result, dict) else {"message": str(result)}
        content = json.dumps(payload, ensure_ascii=False, default=str)
        if len(content) > self._MAX_OBSERVATION_CHARS:
            summary = display_summary or str(payload.get("message", ""))
            payload = {
                "success": not is_error,
                "truncated": True,
                "summary": summary[: self._MAX_OBSERVATION_CHARS - 100],
            }
            content = json.dumps(payload, ensure_ascii=False)
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": content,
            "display_summary": display_summary,
            "is_error": is_error,
        })
        self._messages_dirty = True

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
        """追加工具执行结果作为 user 消息。超过 _MAX_OBSERVATION_CHARS 字符时换行感知截断。

        result 字符串预期由 ToolResult.to_observation() 预格式化，此处的截断为兜底安全网。
        """
        # M8: 前缀也计入总长度，截断限制需剔除前缀开销
        full_prefix = self._OBSERVATION_PREFIX.format(name=tool_name)
        prefix_len = len(full_prefix)
        max_result_chars = self._MAX_OBSERVATION_CHARS - prefix_len
        if max_result_chars < 100:
            max_result_chars = 100  # 保底：前缀再长也保留至少 100 字符的结果

        if len(result) > max_result_chars:
            cut_pos = max_result_chars - 30
            last_nl = result.rfind("\n", 0, cut_pos)
            if last_nl > cut_pos // 2:
                result = result[:last_nl] + "\n  ...(truncated)"
            else:
                result = result[:cut_pos] + "...(truncated)"
        self._messages.append({
            "role": "user",
            "content": f"{full_prefix}{result}",
        })
        self._messages_dirty = True

    def add_plan_result(self, summary: str, *, success: bool = True, results: list[dict] | None = None) -> None:
        """追加计划执行聚合结果作为 user 消息。超过 _MAX_OBSERVATION_CHARS 字符自动截断。"""
        if len(summary) > self._MAX_OBSERVATION_CHARS:
            summary = summary[: self._MAX_OBSERVATION_CHARS] + f"...(truncated; {len(summary)} characters total)"
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
        self._messages.append({"role": "user", "content": f"{self._PLAN_RESULT_PREFIX}\n{summary}"})
        self._messages_dirty = True

    def get_messages(self) -> list[dict[str, Any]]:
        """返回消息列表副本。

        m5: 缓存机制 — 仅在 _messages 变更时重建副本，后续调用返回缓存的副本。
        """
        if self._messages_dirty:
            self._messages_cache = self._messages.copy()
            self._messages_dirty = False
        return self._messages_cache.copy()

    def clear(self) -> None:
        self._messages.clear()
        self._turn_starts.clear()
        self._messages_dirty = True
        self._messages_cache.clear()
        self._loaded_tool_namespaces.clear()

    # ── 序列化 (FR13) ──────────────────────────────────────

    def to_dict(self) -> dict:
        """导出消息列表为可序列化的字典。"""
        return {
            "messages": self.get_messages(),
            "loaded_tool_namespaces": list(self.get_loaded_tool_namespaces()),
        }

    def from_dict(self, data: dict) -> None:
        """从字典恢复消息列表。替换现有消息并重置轮次索引。"""
        self._messages = list(data.get("messages", []))
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
        return content.startswith("【工具执行结果") or content.startswith(cls._PLAN_RESULT_PREFIX)

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

    def _trim(self) -> None:
        """M3/M10: 基于预记录的 turn_starts 裁剪，保留最后 max_turns 轮。

        一轮 = 从 turn_starts[i] 到 turn_starts[i+1] (或末尾) 的所有消息。
        裁剪时整轮移除（含该轮关联的所有 observation 和 plan_result 消息）。

        M3: 使用 add_user() 时预记录的 _turn_starts，不再依赖遍历扫描消息顺序。
        m6: 使用列表切片重建 messages，避免逐个 del 的 O(n*k) 开销。
        """
        if self._max_turns <= 0:
            return

        while len(self._turn_starts) > self._max_turns:
            # 最旧一轮的起始位置
            oldest_start = self._turn_starts.pop(0)
            # 该轮结束位置 = 下一轮起始 (或消息末尾)
            if self._turn_starts:
                oldest_end = self._turn_starts[0]
            else:
                oldest_end = len(self._messages)

            # 计算本轮将被移除的消息数量
            removed_count = oldest_end - oldest_start
            # 调整剩余 turn_starts 的偏移量：
            # 所有后续轮次的起始索引前移 removed_count 位，
            # 以匹配切片后新列表的索引
            for i in range(len(self._turn_starts)):
                self._turn_starts[i] -= removed_count

            # 保存 system 消息 (始终位于索引 0)，切片 [oldest_end:]
            # 会将其一并移除，因此需要先备份
            system_msg = None
            if self._messages and self._messages[0]["role"] == "system":
                system_msg = self._messages[0]

            # m6: 使用切片重建消息列表，O(n) 单次操作替代逐个 del 的 O(n*k)
            # 切片保留从 oldest_end 开始的所有剩余轮次
            self._messages = self._messages[oldest_end:]
            self._messages_dirty = True

            # 若 system 消息被切片移除则恢复
            # M2: 防御性检查 — 若 sliced messages 已含 system 消息则不再插入
            if system_msg is not None and oldest_end > 0:
                if not (self._messages and self._messages[0]["role"] == "system"):
                    self._messages.insert(0, system_msg)
                    for i in range(len(self._turn_starts)):
                        self._turn_starts[i] += 1

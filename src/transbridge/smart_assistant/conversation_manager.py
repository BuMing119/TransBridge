from __future__ import annotations

from typing import Any


class ConversationManager:
    """维护多轮对话历史，封装 message list 操作。

    M10: _trim 按轮次裁剪，每轮包含 user -> assistant -> [observation*] -> [plan_result]
    M12: add_observation 结果超过 _MAX_OBSERVATION_CHARS 字符自动截断
    M3: turn_starts 预记录轮次起始位置，_trim 基于记录裁剪，不依赖遍历扫描消息顺序
    m5: _messages_cache 缓存 get_messages() 返回值，仅在消息变更时重建
    """

    _OBSERVATION_PREFIX = "【工具执行结果 - {name}】\n"
    _PLAN_RESULT_PREFIX = "【计划执行完成】"
    _MAX_OBSERVATION_CHARS = 2000

    def __init__(self, max_turns: int = 20) -> None:
        self._messages: list[dict[str, Any]] = []
        self._max_turns: int = max_turns
        # M3: 预记录每轮起始位置 (user 消息在 _messages 中的索引)
        self._turn_starts: list[int] = []
        # m5: 消息缓存 -- 仅在消息变更时重建副本
        self._messages_dirty: bool = True
        self._messages_cache: list[dict[str, Any]] = []

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

    def add_plan_result(self, summary: str) -> None:
        """追加计划执行聚合结果作为 user 消息。超过 _MAX_OBSERVATION_CHARS 字符自动截断。"""
        if len(summary) > self._MAX_OBSERVATION_CHARS:
            summary = summary[:self._MAX_OBSERVATION_CHARS] + f"...(已截断，共 {len(summary)} 字符)"
        self._messages.append({
            "role": "user",
            "content": f"{self._PLAN_RESULT_PREFIX}\n{summary}",
        })
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

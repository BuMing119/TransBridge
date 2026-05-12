class ConversationManager:
    """维护多轮对话历史，封装 message list 操作。

    M10: _trim 按轮次裁剪，每轮包含 user → assistant → [observation*] → [plan_result]
    M12: add_observation 结果超过 2000 字符自动截断
    """

    _OBSERVATION_PREFIX = "【工具执行结果 - "
    _PLAN_RESULT_PREFIX = "【计划执行完成】"

    def __init__(self, max_turns: int = 20):
        self._messages: list[dict] = []
        self._max_turns = max_turns

    def add_system(self, content: str) -> None:
        """system 消息始终在列表最前（索引 0），替换已有 system 消息。"""
        self._messages = [m for m in self._messages if m["role"] != "system"]
        self._messages.insert(0, {"role": "system", "content": content})

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def add_observation(self, tool_name: str, result: str) -> None:
        """追加工具执行结果作为 user 消息。M12: 超过 2000 字符自动截断。"""
        if len(result) > 2000:
            result = result[:2000] + f"...(已截断，共 {len(result)} 字符)"
        self._messages.append({
            "role": "user",
            "content": f"{self._OBSERVATION_PREFIX}{tool_name}】\n{result}",
        })

    def add_plan_result(self, summary: str) -> None:
        """追加计划执行聚合结果作为 user 消息。M12: 超过 2000 字符自动截断。"""
        if len(summary) > 2000:
            summary = summary[:2000] + f"...(已截断，共 {len(summary)} 字符)"
        self._messages.append({
            "role": "user",
            "content": f"{self._PLAN_RESULT_PREFIX}\n{summary}",
        })

    def get_messages(self) -> list[dict]:
        return self._messages.copy()

    def clear(self) -> None:
        self._messages.clear()

    def _is_observation(self, msg: dict) -> bool:
        return (msg["role"] == "user"
                and isinstance(msg.get("content", ""), str)
                and msg["content"].startswith(self._OBSERVATION_PREFIX))

    def _is_plan_result(self, msg: dict) -> bool:
        return (msg["role"] == "user"
                and isinstance(msg.get("content", ""), str)
                and msg["content"].startswith(self._PLAN_RESULT_PREFIX))

    def _trim(self) -> None:
        """M10: 按轮次裁剪，保留最后 max_turns 轮。

        一轮 = user(非observation/plan_result) + assistant + 后续 observation*/plan_result*
        裁剪时整轮移除（含该轮关联的所有 observation 和 plan_result 消息）。
        """
        if self._max_turns <= 0:
            return

        # 找到所有"真正"的 user 消息（排除 observation 和 plan_result）
        turns: list[list[int]] = []  # 每轮: user_idx, assistant_idx, [extra_idx...]
        current_turn: list[int] = []

        for i, m in enumerate(self._messages):
            if m["role"] == "user" and not self._is_observation(m) and not self._is_plan_result(m):
                # 新轮开始：结算上一轮
                if current_turn:
                    turns.append(current_turn)
                current_turn = [i]
            elif m["role"] == "assistant" and current_turn and len(current_turn) == 1:
                current_turn.append(i)
            elif (self._is_observation(m) or self._is_plan_result(m)) and current_turn:
                current_turn.append(i)
            # system 消息跳过，不属于任何轮

        if current_turn:
            turns.append(current_turn)

        # 保留最后 max_turns 轮
        while len(turns) > self._max_turns:
            removed_indices = turns.pop(0)
            # 从后往前删，避免索引偏移
            for idx in sorted(removed_indices, reverse=True):
                if idx < len(self._messages):
                    del self._messages[idx]

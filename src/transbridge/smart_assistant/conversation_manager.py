class ConversationManager:
    """维护多轮对话历史，封装 message list 操作。"""

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
        """追加工具执行结果作为 user 消息，供 LLM 继续推理。"""
        self._messages.append({
            "role": "user",
            "content": f"【工具执行结果 - {tool_name}】\n{result}",
        })

    def add_plan_result(self, summary: str) -> None:
        """追加计划执行聚合结果作为 user 消息。"""
        self._messages.append({
            "role": "user",
            "content": f"【计划执行完成】\n{summary}",
        })

    def get_messages(self) -> list[dict]:
        return self._messages.copy()

    def clear(self) -> None:
        self._messages.clear()

    def _trim(self) -> None:
        """保持 user+assistant 对数不超过 max_turns。"""
        if self._max_turns <= 0:
            return
        # 找到所有 user 消息的索引，以及其后紧跟的 assistant 消息
        pairs = []
        for i, m in enumerate(self._messages):
            if m["role"] == "user" and i + 1 < len(self._messages):
                if self._messages[i + 1]["role"] == "assistant":
                    pairs.append((i, i + 1))
        while len(pairs) > self._max_turns:
            u_idx, a_idx = pairs.pop(0)
            # 从后往前删，避免索引偏移
            del self._messages[a_idx]
            del self._messages[u_idx]

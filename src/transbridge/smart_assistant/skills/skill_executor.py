"""Skill 执行调度器。"""

from .skill_loader import SkillSpec


class SkillExecutor:
    """注入 Skill prompt → 限制工具 → 触发 LLM。"""

    def __init__(self, chat_widget):
        self._chat = chat_widget

    def execute(self, spec: SkillSpec) -> None:
        """执行单个 Skill。"""
        # 1. 注入 Skill 的 prompt 模板作为系统指令
        if spec.prompt_template:
            prompt_text = f"【Skill: {spec.display_name}】\n{spec.prompt_template}"
            self._chat.add_system_message(f"🔧 已激活 Skill: {spec.display_name}")
            # 2. 将 skill prompt 注入 conversation
            from src.transbridge.smart_assistant.conversation_manager import ConversationManager
            self._chat._conversation.add_system(prompt_text)

        # 3. 构建用户消息描述 Skill 意图
        user_msg = f"[Skill: {spec.name}] {spec.description}"

        # 4. 触发 LLM 推理
        self._chat.set_input(user_msg)
        self._chat._on_send()

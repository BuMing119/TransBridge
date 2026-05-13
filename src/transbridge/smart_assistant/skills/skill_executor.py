"""Skill 执行调度器。

BR4 已知问题：SkillExecutor 直接依赖 UI 层 ChatWidget 并调用其私有方法
（_conversation, set_input, _on_send），存在反向依赖。长期应改为信号驱动架构：
SkillExecutor 发出信号 → ChatWidget 响应，而非直接操纵 UI 实例。
"""

from .skill_loader import SkillSpec


class SkillExecutor:
    """注入 Skill prompt → 限制工具 → 触发 LLM。"""

    def __init__(self, chat_widget: "ChatWidget"):
        # BR4: chat_widget 类型使用前向引用字符串以避免循环导入。
        # 该依赖已知为反向依赖，计划后续重构为信号驱动。
        self._chat = chat_widget

    def execute(self, spec: SkillSpec) -> None:
        """执行单个 Skill（异步触发）。

        m22: 此方法通过 ChatWidget._on_send() 触发 LLM 推理，其执行是异步的：
        - LLM 请求在后台 QThread (ChatWorker) 中处理
        - 响应通过 Qt 信号/槽逐步回传并渲染
        - 本方法本身立即返回，不等待 LLM 响应完成
        调用方不应假设 Skill 执行在 execute() 返回时已完成。
        """
        # 1. 注入 Skill 的 prompt 模板作为系统指令
        if spec.prompt_template:
            prompt_text = f"【Skill: {spec.display_name}】\n{spec.prompt_template}"
            # BR4: add_system_message 通过 ChatWidget 的 UI 方法添加消息
            self._chat.add_system_message(f"🔧 已激活 Skill: {spec.display_name}")
            # 2. 将 skill prompt 注入 conversation
            from src.transbridge.smart_assistant.conversation_manager import ConversationManager
            # BR4: _conversation 是 ChatWidget 的私有属性
            self._chat._conversation.add_system(prompt_text)

        # 3. 构建用户消息描述 Skill 意图
        user_msg = f"[Skill: {spec.name}] {spec.description}"

        # 4. 触发 LLM 推理
        # BR4: set_input 和 _on_send 是 ChatWidget 的方法，反向依赖 UI 层
        self._chat.set_input(user_msg)
        self._chat._on_send()

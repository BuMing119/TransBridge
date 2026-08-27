"""Skill 执行调度器。

通过 ChatWidget 的公共接口（add_system_prompt / send_user_message）触发 Skill，
不再访问 UI 层的私有成员。SkillExecutor 不感知 ChatWidget 的内部实现细节。
"""

import logging

from .skill_loader import SkillSpec

logger = logging.getLogger(__name__)


class SkillExecutor:
    """注入 Skill prompt → 限制工具 → 触发 LLM。"""

    def __init__(self, chat_widget: "ChatWidget"):
        # chat_widget 类型使用前向引用字符串以避免循环导入。
        self._chat = chat_widget

    def execute(self, spec: SkillSpec) -> None:
        """执行单个 Skill（异步触发）。

        m22: 此方法通过 ChatWidget.send_user_message() 触发 LLM 推理，其执行是异步的：
        - LLM 请求在后台 QThread (ChatWorker) 中处理
        - 响应通过 Qt 信号/槽逐步回传并渲染
        - 本方法本身立即返回，不等待 LLM 响应完成
        调用方不应假设 Skill 执行在 execute() 返回时已完成。
        """
        try:
            # 1. 注入 Skill 的 prompt 模板作为系统指令
            if spec.prompt_template:
                prompt_text = f"[Skill: {spec.name}]\n{spec.prompt_template}"
                self._chat.add_system_message(f"🔧 已激活 Skill: {spec.display_name}")
                # C25: 使用 ChatWidget 的公共方法 add_system_prompt，而非访问私有 _conversation
                self._chat.add_system_prompt(prompt_text)

            # 2. 构建用户消息描述 Skill 意图
            user_msg = f"[Skill: {spec.name}] {spec.description}"

            # 3. 触发 LLM 推理
            # C25: 使用 ChatWidget 的公共方法 send_user_message，而非 set_input + _on_send
            self._chat.send_user_message(user_msg)
        except Exception:
            logger.exception("Skill 执行异常 [%s]，已跳过以免影响主聊天流程", spec.name)

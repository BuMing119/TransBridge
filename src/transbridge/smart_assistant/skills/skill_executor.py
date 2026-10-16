"""Skill 执行调度器。

通过 ChatWidget 的公共接口 send_user_message 触发 Skill，
不再访问 UI 层的私有成员。SkillExecutor 不感知 ChatWidget 的内部实现细节。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transbridge.ui.tools.smart_assistant.chat_widget import ChatWidget

from .skill_loader import SkillSpec

logger = logging.getLogger(__name__)


class SkillExecutor:
    """将技能说明与本次用户输入一起接纳，不改变会话的全局规则。"""

    def __init__(self, chat_widget: ChatWidget):
        # chat_widget 类型使用前向引用字符串以避免循环导入。
        self._chat = chat_widget

    def execute(self, spec: SkillSpec, *, user_request: str | None = None) -> bool:
        """执行单个 Skill（异步触发）。

        m22: 此方法通过 ChatWidget.send_user_message() 触发 LLM 推理，其执行是异步的：
        - LLM 请求在后台 QThread (ChatWorker) 中处理
        - 响应通过 Qt 信号/槽逐步回传并渲染
        - 本方法本身立即返回，不等待 LLM 响应完成
        调用方不应假设 Skill 执行在 execute() 返回时已完成。
        """
        request = spec.description if user_request is None else user_request.strip()
        if not request:
            return False
        try:
            # Persist the skill snapshot with this input, never mutate global rules.
            user_msg = f"[Skill: {spec.name}] {request}"
            accepted = (
                self._chat.send_user_message(
                    user_msg,
                    skill_context={
                        "name": spec.name,
                        "display_name": spec.display_name,
                        "prompt": spec.prompt_template,
                    },
                )
                is not False
            )
            if accepted:
                self._chat.add_system_message(f"已提交技能请求：{spec.display_name}")
            return accepted
        except Exception:
            logger.exception("Skill 执行异常 [%s]，本次请求未启动", spec.name)
            self._chat.add_system_message(f"技能「{spec.display_name}」未能启动，请稍后重试。")
            return False

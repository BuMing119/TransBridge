from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QTextEdit, QPushButton,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent

from .message_bubble import MessageBubble
from .conversation_manager import ConversationManager
from .chat_worker import ChatWorker
from .execution_engine import ExecutionEngine, StepResult
from .tool_card import ToolCard, BatchToolCard
from .plan_card import PlanCard


class ChatWidget(QWidget):
    """聊天区域：消息滚动列表 + 输入框 + 发送/清空按钮 + 双模式循环控制。"""

    _MAX_REACT_DEPTH = 10

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx

        self._conversation = ConversationManager(max_turns=20)
        self._worker: ChatWorker | None = None
        self._engine: ExecutionEngine | None = None
        self._react_depth = 0
        self._prompt_builder = None
        self._consecutive_errors = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # ── 消息滚动区 ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._msg_container = QWidget()
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(4, 4, 4, 4)
        self._msg_layout.setSpacing(4)
        self._msg_layout.addStretch()
        self._scroll.setWidget(self._msg_container)

        layout.addWidget(self._scroll, stretch=1)

        # ── 输入框 ──
        self._input = QTextEdit()
        self._input.setMaximumHeight(100)
        self._input.setPlaceholderText("输入消息，Ctrl+Enter 发送")
        self._input.installEventFilter(self)
        layout.addWidget(self._input)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("清空对话")
        clear_btn.clicked.connect(self._clear_conversation)
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self._on_send)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        btn_row.addWidget(send_btn)
        layout.addLayout(btn_row)

    # ── 公共方法 ──────────────────────────────────────────────

    def set_input(self, text: str) -> None:
        self._input.setPlainText(text)
        self._input.setFocus()

    def add_user_bubble(self, text: str) -> None:
        self._add_bubble(MessageBubble(text, "user"))

    def add_assistant_bubble(self, text: str) -> None:
        self._add_bubble(MessageBubble(text, "assistant"))

    def add_system_message(self, text: str) -> None:
        self._add_bubble(MessageBubble(text, "system"))

    def add_tool_card(self, step: dict) -> ToolCard:
        card = ToolCard(step)
        card.executed.connect(self._on_tool_executed)
        card.ignored.connect(self._on_tool_ignored)
        self._add_widget(card)
        return card

    def add_plan_card(self, steps: list) -> PlanCard:
        card = PlanCard(steps)
        card.confirmed.connect(self._on_plan_confirmed)
        card.cancelled.connect(self._on_plan_cancelled)
        self._add_widget(card)
        return card

    def add_batch_tool_card(self, steps: list) -> BatchToolCard:
        card = BatchToolCard(steps)
        card.all_executed.connect(self._on_batch_executed)
        self._add_widget(card)
        return card

    # ── LLM 循环控制 ─────────────────────────────────────────

    def _get_prompt_builder(self):
        if self._prompt_builder is None:
            from src.transbridge.ai_translator.prompt_builder import PromptBuilder
            self._prompt_builder = PromptBuilder()
        return self._prompt_builder

    @staticmethod
    def _get_llm_client():
        from src.transbridge.paratranz.config_manager import LLMConfig
        from src.transbridge.ai_translator.llm_client import create_llm_client
        cfg = LLMConfig.load_from_file()
        if not cfg.api_key:
            return None
        return create_llm_client(cfg)

    def _run_llm_round(self) -> None:
        client = self._get_llm_client()
        if client is None:
            self.add_system_message("请先在设置中配置 LLM API Key")
            return

        # 确保 system prompt 已设置
        if not any(m["role"] == "system" for m in self._conversation.get_messages()):
            from .context_builder import ContextBuilder
            from .prompts import build_system_prompt
            context = ContextBuilder.build(self._ctx)
            sys_prompt = build_system_prompt(context)
            self._conversation.add_system(sys_prompt)

        self._react_depth += 1
        messages = self._conversation.get_messages()
        self._worker = ChatWorker(client, messages, max_tokens=2048)
        self._worker.chunk.connect(self._on_llm_chunk)
        self._worker.finished.connect(self._on_llm_finished)
        self._worker.error.connect(self._on_llm_error)
        self._worker.start()

    def _on_llm_chunk(self, chunk: str) -> None:
        pass  # 流式打字机效果预留给 Story-05

    def _on_llm_finished(self, response: str) -> None:
        self._consecutive_errors = 0  # 成功响应，重置错误计数
        pb = self._get_prompt_builder()
        parsed = pb.parse_hybrid_response(response)

        thought = parsed.get("thought", "")
        if thought:
            self.add_assistant_bubble(thought)

        self._conversation.add_assistant(response)

        steps = parsed.get("steps", [])
        mode = parsed.get("mode", "react")

        if mode == "plan" and steps:
            self.add_plan_card(steps)
        elif len(steps) == 1:
            self.add_tool_card(steps[0])
        elif len(steps) > 1:
            self.add_batch_tool_card(steps)
        # steps 为空 → 纯文本回复，任务结束

    def _on_llm_error(self, msg: str) -> None:
        self._react_depth = 0
        self._consecutive_errors += 1

        is_network = any(kw in msg.lower() for kw in (
            "timeout", "connection", "refused", "network", "reset", "unreachable"
        ))
        is_auth = "401" in msg or "403" in msg or "unauthorized" in msg.lower()

        if is_auth:
            self.add_system_message("🔑 API 认证失败，请检查 LLM API Key 配置是否正确。")
            self._consecutive_errors = 0
        elif is_network:
            if self._consecutive_errors >= 3:
                self.add_system_message(
                    f"⚠ 连续 {self._consecutive_errors} 次网络错误，请检查网络连接或 VPN 状态后重试。"
                )
            else:
                self.add_system_message(f"🌐 网络请求失败: {msg}")
                # 添加重试按钮
                retry_btn = QPushButton("🔄 重试")
                retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                retry_btn.clicked.connect(self._on_retry)
                self._add_widget(retry_btn)
        else:
            self.add_system_message(f"❌ 请求失败: {msg}")

    # ── 计划模式 ─────────────────────────────────────────────

    def _on_plan_confirmed(self, steps: list) -> None:
        from .tool_registry import ToolRegistry
        import threading

        self._engine = ExecutionEngine(ToolRegistry, self._ctx)
        self._engine.all_finished.connect(self._on_plan_all_finished)
        t = threading.Thread(target=self._engine.execute, args=(steps,), daemon=True)
        t.start()

    def _on_plan_cancelled(self) -> None:
        if self._engine:
            self._engine.cancel()
        self.add_system_message("计划已取消")

    def _on_plan_all_finished(self, results: list) -> None:
        lines = []
        for r in results:
            icon = "✅" if r.success else "❌"
            lines.append(f"{icon} 步骤 {r.step_id} ({r.tool}): {r.message}")
        summary = "\n".join(lines)
        self.add_system_message(f"【计划执行完成】\n{summary}")
        self._conversation.add_plan_result(summary)
        self._react_depth = 0
        self._run_llm_round()

    # ── ReAct 模式 ───────────────────────────────────────────

    def _on_tool_executed(self, step: dict) -> None:
        tool_name = step.get("tool", "?")
        from .tool_registry import ToolRegistry
        spec = ToolRegistry.get(tool_name)
        if spec and spec.execute:
            try:
                result = spec.execute(step.get("args", {}), self._ctx)
            except Exception as exc:
                result = {"success": False, "message": str(exc), "error_type": type(exc).__name__}
        else:
            result = {"success": False, "message": f"未知工具: {tool_name}"}
        self._handle_tool_result(step, result)

    def _on_tool_ignored(self, step: dict) -> None:
        tool_name = step.get("tool", "?")
        self.add_system_message(f"已忽略: {tool_name}")
        self._conversation.add_observation(tool_name, "用户选择不执行此操作。")
        if self._check_react_depth():
            self._run_llm_round()

    def _on_batch_executed(self, steps: list) -> None:
        results = []
        for s in steps:
            t = s.get("tool", "?")
            results.append(f"✅ {t}: 执行完成")
        summary = "\n".join(results)
        self.add_system_message(f"【批量执行完成】\n{summary}")
        self._conversation.add_observation("batch", summary)
        if self._check_react_depth():
            self._run_llm_round()

    def _handle_tool_result(self, step: dict, result: dict) -> None:
        tool_name = step.get("tool", "?")
        if result.get("success"):
            msg = f"✅ {tool_name}: {result.get('message', '完成')}"
        else:
            err_msg = result.get('message', result.get('error', '失败'))
            err_type = result.get('error_type', '')
            if err_type:
                msg = f"❌ {tool_name}: {err_msg}\n  (类型: {err_type})"
            else:
                msg = f"❌ {tool_name}: {err_msg}"
        self.add_system_message(msg)
        self._conversation.add_observation(tool_name, msg)
        if self._check_react_depth():
            self._run_llm_round()

    def _check_react_depth(self) -> bool:
        if self._react_depth >= self._MAX_REACT_DEPTH:
            self.add_system_message("⚠ 已达最大推理深度，对话终止。")
            self._react_depth = 0
            return False
        return True

    def _on_retry(self) -> None:
        """网络错误后重试：清理旧 worker，重新发送上一条消息。"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._worker = None
        self.add_system_message("正在重试…")
        self._run_llm_round()

    # ── 内部方法 ──────────────────────────────────────────────

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        self.add_user_bubble(text)
        self._input.clear()
        self._conversation.add_user(text)
        self._react_depth = 0
        self._run_llm_round()

    def _clear_conversation(self) -> None:
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._conversation.clear()

    def _add_bubble(self, bubble: MessageBubble) -> None:
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _add_widget(self, widget: QWidget) -> None:
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        vsb = self._scroll.verticalScrollBar()
        if vsb:
            vsb.setValue(vsb.maximum())

    def eventFilter(self, obj, event):
        if obj == self._input and event.type() == QEvent.Type.KeyPress:
            ke = event
            if (ke.key() == Qt.Key.Key_Return and
                    ke.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

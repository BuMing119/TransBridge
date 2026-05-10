from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QTextEdit, QPushButton, QFileDialog, QLabel,
    QMessageBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QTimer

from .message_bubble import MessageBubble
from src.transbridge.smart_assistant.conversation_manager import ConversationManager
from src.transbridge.smart_assistant.chat_worker import ChatWorker
from src.transbridge.smart_assistant.execution_engine import ExecutionEngine, StepResult
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
        self._uploaded_docs: dict[str, object] = {}  # filename → ParsedDocument

        # 长期记忆
        from pathlib import Path
        from src.transbridge.smart_assistant.memory import MemoryStore, MemoryRetriever
        self._memory_store = MemoryStore(
            Path("data/memory"), embedding_mode="disabled"
        )
        self._memory_retriever = MemoryRetriever(self._memory_store)

        # 可观测性收集器 (S11)
        from src.transbridge.smart_assistant.observability import ObservabilityCollector
        self._obs_collector = ObservabilityCollector(storage_dir=Path("data/observability"))
        self._obs_collector.token_stats_updated.connect(self._on_token_stats_updated)

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

        # ── 文件上传栏 ──
        upload_row = QHBoxLayout()
        self._upload_label = QLabel("")
        self._upload_label.setStyleSheet("color: #666; font-size: 11px;")
        upload_btn = QPushButton("上传参考文件")
        upload_btn.setToolTip("上传纠错表/术语参考/风格指南（Excel/CSV/Markdown/TXT/JSON/PDF/Word）")
        upload_btn.clicked.connect(self._on_upload_file)
        upload_row.addWidget(upload_btn)
        upload_row.addWidget(self._upload_label, stretch=1)
        layout.addLayout(upload_row)

        # ── Agent 状态指示器 (S07) ──
        agent_row = QHBoxLayout()
        agent_row.setSpacing(8)
        agent_label = QLabel("Agent:")
        agent_label.setStyleSheet("color: #888; font-size: 10px;")
        agent_row.addWidget(agent_label)
        self._agent_indicators: dict[str, QLabel] = {}
        for agent_id, label_text in [("translator", "翻译"), ("proofreader", "校对"), ("orchestrator", "编排")]:
            lbl = QLabel(f"○ {label_text}")
            lbl.setStyleSheet("color: #999; font-size: 10px; padding: 1px 4px;")
            agent_row.addWidget(lbl)
            self._agent_indicators[agent_id] = lbl
        agent_row.addStretch()
        layout.addLayout(agent_row)

        # ── 观测面板 Tab (S11) ──
        self._obs_tabs = QTabWidget()
        self._obs_tabs.setMaximumHeight(160)
        self._obs_tabs.setStyleSheet("font-size: 10px;")
        # Token 仪表盘
        self._obs_token_widget = QWidget()
        token_layout = QHBoxLayout(self._obs_token_widget)
        self._obs_token_labels: dict[str, QLabel] = {}
        for period in ["今日", "本周", "本月"]:
            lbl = QLabel(f"{period}\n输入: 0\n输出: 0")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("background: #f5f5f5; border-radius: 4px; padding: 4px;")
            token_layout.addWidget(lbl)
            self._obs_token_labels[period] = lbl
        self._obs_tabs.addTab(self._obs_token_widget, "Token")
        # 工具调用表
        self._obs_tool_table = QTableWidget(0, 4)
        self._obs_tool_table.setHorizontalHeaderLabels(["时间", "工具", "耗时", "状态"])
        self._obs_tool_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._obs_tabs.addTab(self._obs_tool_table, "工具调用")
        # 对话轮次
        self._obs_round_list = QListWidget()
        self._obs_tabs.addTab(self._obs_round_list, "轮次")
        layout.addWidget(self._obs_tabs)

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
        from src.transbridge.infra.llm_client import create_llm_client
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
            from src.transbridge.smart_assistant.context_builder import ContextBuilder
            from src.transbridge.smart_assistant.prompts import build_system_prompt
            self._ctx._uploaded_docs = self._uploaded_docs
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

        # 自动记录本轮对话记忆
        try:
            from src.transbridge.smart_assistant.memory import MemoryEntry
            user_msgs = [m["content"] for m in self._conversation.get_messages() if m["role"] == "user"]
            last_user = user_msgs[-1][:100] if user_msgs else ""
            entry = MemoryEntry(
                type="conversation",
                summary=last_user,
                content=f"User: {last_user}\nAssistant: {response[:300]}",
                source="chat",
            )
            self._memory_store.add(entry)
        except Exception:
            pass  # 记忆记录失败不影响对话流程

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
        from src.transbridge.smart_assistant.tool_registry import ToolRegistry
        from src.transbridge.smart_assistant.guardrails import PermissionGuard, InputValidationGuard, OutputValidationGuard
        from src.transbridge.paratranz.config_manager import LLMConfig
        import threading

        cfg = LLMConfig.load_from_file()
        middlewares = []
        if cfg.guardrails_enable_input_validation:
            middlewares.append(InputValidationGuard(cfg.guardrails_max_input_size))
        middlewares.append(PermissionGuard(
            enable_admin_confirm=cfg.guardrails_enable_admin_confirm,
            write_require_confirm=cfg.guardrails_write_require_confirm,
        ))
        if cfg.guardrails_enable_output_validation:
            middlewares.append(OutputValidationGuard())

        self._engine = ExecutionEngine(ToolRegistry, self._ctx, middlewares=middlewares)
        self._engine.all_finished.connect(self._on_plan_all_finished)
        self._engine.step_requires_confirmation.connect(self._on_confirm_required)
        # Observability
        self._obs_collector.start_conversation(f"conv_{id(steps)}")
        self._engine.step_started.connect(self._obs_collector.on_step_started)
        self._engine.step_finished.connect(self._obs_collector.on_step_finished)
        self._engine.step_retrying.connect(self._obs_collector.on_step_retrying)
        # Agent 状态指示器
        self._update_agent_indicators({"translator": "exec", "orchestrator": "exec"})
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
        # 观测结束 + Agent 状态重置
        self._obs_collector.end_conversation()
        self._update_agent_indicators({k: "idle" for k in self._agent_indicators})
        self._react_depth = 0
        self._run_llm_round()

    # ── Agent 状态与确认 (S07/S08/S10) ──────────────────────

    def _update_agent_indicators(self, states: dict[str, str]) -> None:
        """states: {"translator": "exec"|"done"|"fail"|"idle", ...}"""
        color_map = {"idle": "#999", "exec": "#2196F3", "done": "#4CAF50", "fail": "#F44336"}
        icon_map = {"idle": "○", "exec": "◉", "done": "●", "fail": "✕"}
        for agent_id, state in states.items():
            if agent_id in self._agent_indicators:
                c = color_map.get(state, "#999")
                i = icon_map.get(state, "○")
                base = self._agent_indicators[agent_id].text().split()[-1] if self._agent_indicators[agent_id].text() else ""
                self._agent_indicators[agent_id].setText(f"{i} {base}")
                self._agent_indicators[agent_id].setStyleSheet(
                    f"color: {c}; font-size: 10px; padding: 1px 4px;"
                )

    def _on_confirm_required(self, node_id: str, prompt: str, choices: list) -> None:
        reply = QMessageBox.question(self, "操作确认", prompt,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        choice = choices[0] if reply == QMessageBox.StandardButton.Yes else (choices[1] if len(choices) > 1 else "跳过")
        if self._engine:
            self._engine.provide_decision(node_id, choice)

    def _on_token_stats_updated(self, stats) -> None:
        """更新观测面板 Token 统计 (S11)。"""
        today = self._obs_token_labels.get("今日")
        if today:
            today.setText(f"今日\n输入: {stats.input_tokens}\n输出: {stats.output_tokens}")

    # ── ReAct 模式 ───────────────────────────────────────────

    def _on_tool_executed(self, step: dict) -> None:
        tool_name = step.get("tool", "?")
        from src.transbridge.smart_assistant.tool_registry import ToolRegistry
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

        # 检索相关历史记忆并注入上下文
        if self._memory_store.count > 0:
            memories = self._memory_retriever.retrieve(text, top_k=3)
            if memories:
                mem_lines = ["相关历史记忆:"]
                for m in memories:
                    mem_lines.append(f"  - [{m.type}] {m.summary}")
                self._conversation.add_system("\n".join(mem_lines))

        self.add_user_bubble(text)
        self._input.clear()
        self._conversation.add_user(text)
        self._react_depth = 0
        self._run_llm_round()

    def _on_skill(self, skill_name: str) -> None:
        """Skill 按钮触发：加载并执行 Skill。"""
        from src.transbridge.smart_assistant.skills import SkillRegistry, SkillExecutor
        spec = SkillRegistry.get(skill_name)
        if spec:
            executor = SkillExecutor(self)
            executor.execute(spec)

    def _on_upload_file(self) -> None:
        """选择文件 → 解析 → 存储到 _uploaded_docs。"""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择参考文件",
            "",
            "文档 (*.xlsx *.csv *.md *.txt *.json *.pdf *.docx *.zip);;全部 (*.*)",
        )
        if not paths:
            return
        from pathlib import Path
        from src.transbridge.smart_assistant.file_parser import FileParser
        for p in paths:
            fpath = Path(p)
            parser = FileParser.get_parser(fpath)
            if parser is None:
                self.add_system_message(f"不支持的文件格式: {fpath.name}")
                continue
            try:
                doc = parser.parse(fpath)
                self._uploaded_docs[fpath.name] = doc
            except Exception as exc:
                self.add_system_message(f"解析文件失败: {fpath.name} — {exc}")
        names = ", ".join(self._uploaded_docs.keys())
        self._upload_label.setText(f"已上传: {names}" if names else "")

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

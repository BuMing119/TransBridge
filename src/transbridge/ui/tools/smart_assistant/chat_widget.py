from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QTextEdit, QPushButton, QFileDialog, QLabel,
    QMessageBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QHeaderView, QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QTimer, QSettings
from pathlib import Path

from .message_bubble import MessageBubble
from .quick_actions import QuickActionsChips
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

        # 全局字体
        from PyQt6.QtGui import QFont
        font = QFont("Microsoft YaHei", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)

        self._conversation = ConversationManager(max_turns=20)
        self._worker: ChatWorker | None = None
        self._engine: ExecutionEngine | None = None
        self._react_depth = 0
        self._prompt_builder = None
        self._consecutive_errors = 0
        self._uploaded_docs: dict[str, object] = {}  # filename → ParsedDocument
        self._middlewares: list | None = None  # B1: 延迟构建护栏链

        # 流式打字机状态
        self._streaming_text = ""
        self._streaming_bubble: MessageBubble | None = None
        self._streaming_dirty = False
        self._streaming_timer = QTimer(self)
        self._streaming_timer.setInterval(50)
        self._streaming_timer.timeout.connect(self._flush_streaming)

        # 自动模式 (QSettings 持久化)
        self._auto_mode = False
        try:
            qs = QSettings("TransBridge", "SmartAssistant")
            self._auto_mode = qs.value("auto_mode", False, type=bool)
        except Exception:
            pass

        # 长期记忆
        from src.transbridge.config.paths import get_data_dir
        from src.transbridge.smart_assistant.memory import MemoryStore, MemoryRetriever
        self._memory_store = MemoryStore(
            Path(get_data_dir()) / "memory", embedding_mode="disabled"
        )
        self._memory_retriever = MemoryRetriever(self._memory_store)

        # 可观测性收集器 (S11)
        from src.transbridge.smart_assistant.observability import ObservabilityCollector
        self._obs_collector = ObservabilityCollector(storage_dir=Path(get_data_dir()) / "observability")
        self._obs_collector.token_stats_updated.connect(self._on_token_stats_updated)

        # B2: 异步任务完成通知
        from src.transbridge.smart_assistant.tools.task_manager import TaskManager
        tm = TaskManager()
        tm.task_completed.connect(self._on_task_completed)
        tm.task_failed.connect(self._on_task_failed)

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

        # ── 回到底部浮动按钮 ──
        self._back_to_bottom_btn = QPushButton("[v] 回到底部", self._scroll)
        self._back_to_bottom_btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(0,0,0,0.55); color: white; border: none;"
            "  border-radius: 14px; padding: 5px 12px; font-size: 11px;"
            "}"
            "QPushButton:hover { background: rgba(0,0,0,0.7); }"
        )
        self._back_to_bottom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back_to_bottom_btn.clicked.connect(self._on_back_to_bottom)
        self._back_to_bottom_btn.setVisible(False)
        self._back_to_bottom_btn.raise_()

        # 监听滚动
        vsb = self._scroll.verticalScrollBar()
        if vsb:
            vsb.valueChanged.connect(self._on_scroll_changed)

        # ── 工具栏：chips 快捷指令 + 上传 ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self._chips = QuickActionsChips()
        self._chips.action_clicked.connect(self.set_input)
        self._chips.skill_triggered.connect(self._on_skill)
        toolbar.addWidget(self._chips, stretch=1)
        self._upload_label = QLabel("")
        self._upload_label.setStyleSheet("color: #888; font-size: 11px;")
        upload_btn = QPushButton("上传")
        upload_btn.setToolTip("上传纠错表/术语参考/风格指南（Excel/CSV/Markdown/TXT/JSON/PDF/Word）")
        upload_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #f5f5f5; border: 1px solid #ddd; border-radius: 12px;"
            "  padding: 3px 10px; font-size: 11px; color: #666;"
            "}"
            "QPushButton:hover { background-color: #e8e8e8; }"
        )
        upload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        upload_btn.clicked.connect(self._on_upload_file)
        toolbar.addWidget(upload_btn)
        toolbar.addWidget(self._upload_label)
        layout.addLayout(toolbar)

        # ── 观测面板 Tab (S11) — 默认折叠 ──
        self._obs_header = QPushButton("[>] 观测面板")
        self._obs_header.setStyleSheet(
            "QPushButton {"
            "  text-align: left; background: #fafafa; border: 1px solid #e0e0e0;"
            "  border-radius: 6px; padding: 4px 10px; font-size: 11px; color: #888;"
            "}"
            "QPushButton:hover { background: #f0f0f0; }"
        )
        self._obs_header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._obs_header.clicked.connect(self._toggle_obs_panel)
        layout.addWidget(self._obs_header)

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
        self._obs_tabs.setVisible(False)  # 默认折叠

        # ── 输入框 ──
        self._input = QTextEdit()
        self._input.setMaximumHeight(100)
        self._input.setMinimumHeight(60)
        self._input.setPlaceholderText("输入消息，Ctrl+Enter 发送")
        self._input.setStyleSheet(
            "QTextEdit {"
            "  border: 1px solid #ddd; border-radius: 12px;"
            "  padding: 8px 12px; font-size: 13px;"
            "  background: #fff;"
            "}"
            "QTextEdit:focus { border-color: #4CAF50; }"
        )
        self._input.installEventFilter(self)
        layout.addWidget(self._input)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("清空对话")
        clear_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #f5f5f5; border: 1px solid #ddd; border-radius: 8px;"
            "  padding: 6px 14px; font-size: 12px; color: #666;"
            "}"
            "QPushButton:hover { background-color: #e8e8e8; }"
        )
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_conversation)
        send_btn = QPushButton("发送")
        send_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #4CAF50; color: white; border: none;"
            "  border-radius: 8px; padding: 6px 20px;"
            "  font-size: 13px; font-weight: bold;"
            "}"
            "QPushButton:hover { background-color: #43A047; }"
            "QPushButton:pressed { background-color: #388E3C; }"
        )
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.clicked.connect(self._on_send)
        btn_row.addWidget(clear_btn)
        # 自动模式开关
        self._auto_cb = QCheckBox("Auto")
        self._auto_cb.setToolTip("自动模式：LLM返回工具/计划时直接执行，不显示确认卡片（admin级工具始终确认）")
        self._auto_cb.setChecked(self._auto_mode)
        self._auto_cb.toggled.connect(self._on_auto_mode_toggled)
        self._auto_cb.setStyleSheet(
            "QCheckBox { font-size: 11px; color: #888; spacing: 4px; }"
            "QCheckBox:hover { color: #555; }"
        )
        btn_row.addWidget(self._auto_cb)
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

        # 插入占位气泡用于流式渲染
        self._streaming_text = ""
        self._streaming_bubble = MessageBubble("...", "assistant")
        self._add_bubble(self._streaming_bubble)

        self._react_depth += 1
        messages = self._conversation.get_messages()
        self._worker = ChatWorker(client, messages, max_tokens=2048)
        self._worker.chunk.connect(self._on_llm_chunk)
        self._worker.finished.connect(self._on_llm_finished)
        self._worker.error.connect(self._on_llm_error)
        self._worker.start()

    def _on_llm_chunk(self, chunk: str) -> None:
        self._streaming_text += chunk
        self._streaming_dirty = True
        if not self._streaming_timer.isActive():
            self._streaming_timer.start()

    def _flush_streaming(self) -> None:
        """节流渲染：每 50ms 将累积文本刷新到气泡中。"""
        if not self._streaming_dirty or self._streaming_bubble is None:
            self._streaming_timer.stop()
            return
        self._streaming_dirty = False
        # 替换旧气泡
        old = self._streaming_bubble
        idx = self._msg_layout.indexOf(old)
        if idx >= 0:
            self._msg_layout.removeWidget(old)
            old.deleteLater()
        self._streaming_bubble = MessageBubble(self._streaming_text, "assistant")
        if idx >= 0:
            self._msg_layout.insertWidget(idx, self._streaming_bubble)
        else:
            self._add_bubble(self._streaming_bubble)
        self._scroll_to_bottom()

    def _on_llm_finished(self, response: str) -> None:
        # 停止流式 timer，最终刷新
        self._streaming_timer.stop()
        if self._streaming_bubble:
            self._flush_streaming()
            self._streaming_bubble = None
        self._streaming_text = ""

        self._consecutive_errors = 0  # 成功响应，重置错误计数
        pb = self._get_prompt_builder()
        parsed = pb.parse_hybrid_response(response)

        thought = parsed.get("thought", "")
        # 流式渲染已包含 thought，仅无流式气泡时才单独显示
        if thought and not self._streaming_bubble:
            self.add_assistant_bubble(thought)

        self._conversation.add_assistant(response)

        steps = parsed.get("steps", [])
        mode = parsed.get("mode", "react")

        if self._auto_mode and steps:
            self._auto_execute_steps(steps, mode)
        elif mode == "plan" and steps:
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
        self._streaming_timer.stop()
        self._streaming_text = ""
        self._streaming_dirty = False
        self._react_depth = 0
        self._consecutive_errors += 1

        is_network = any(kw in msg.lower() for kw in (
            "timeout", "connection", "refused", "network", "reset", "unreachable"
        ))
        is_auth = "401" in msg or "403" in msg or "unauthorized" in msg.lower()

        if is_auth:
            self.add_system_message("API 认证失败，请检查 LLM API Key 配置是否正确。")
            self._consecutive_errors = 0
        elif is_network:
            if self._consecutive_errors >= 3:
                self.add_system_message(
                    f"连续 {self._consecutive_errors} 次网络错误，请检查网络连接或 VPN 状态后重试。"
                )
            else:
                self.add_system_message(f"网络请求失败: {msg}")
                # 添加重试按钮
                retry_btn = QPushButton("重试")
                retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                retry_btn.clicked.connect(self._on_retry)
                self._add_widget(retry_btn)
        else:
            self.add_system_message(f"请求失败: {msg}")

    # ── 计划模式 ─────────────────────────────────────────────

    def _on_plan_confirmed(self, steps: list) -> None:
        from src.transbridge.smart_assistant.tool_registry import ToolRegistry
        import threading

        middlewares = self._ensure_middlewares()
        self._engine = ExecutionEngine(ToolRegistry, self._ctx, middlewares=middlewares)
        self._engine.all_finished.connect(self._on_plan_all_finished)
        self._engine.step_requires_confirmation.connect(self._on_confirm_required)
        # Observability
        self._obs_collector.start_conversation(f"conv_{id(steps)}")
        self._engine.step_started.connect(self._obs_collector.on_step_started)
        self._engine.step_finished.connect(self._obs_collector.on_step_finished)
        self._engine.step_retrying.connect(self._obs_collector.on_step_retrying)
        t = threading.Thread(target=self._engine.execute, args=(steps,), daemon=True)
        t.start()

    def _on_plan_cancelled(self) -> None:
        if self._engine:
            self._engine.cancel()
        self.add_system_message("计划已取消")

    def _on_plan_all_finished(self, results: list) -> None:
        lines = []
        for r in results:
            icon = "[OK]" if r.success else "[FAIL]"
            lines.append(f"{icon} 步骤 {r.step_id} ({r.tool}): {r.message}")
        summary = "\n".join(lines)
        self.add_system_message(f"【计划执行完成】\n{summary}")
        self._conversation.add_plan_result(summary)
        self._obs_collector.end_conversation()
        self._react_depth = 0
        self._run_llm_round()

    # ── 确认 (S08) ──────────────────────────────────────────

    def _on_confirm_required(self, node_id: str, prompt: str, choices: list) -> None:
        reply = QMessageBox.question(self, "操作确认", prompt,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        choice = choices[0] if reply == QMessageBox.StandardButton.Yes else (choices[1] if len(choices) > 1 else "跳过")
        if self._engine:
            self._engine.provide_decision(node_id, choice)

    # ── 异步任务通知 (B2) ──────────────────────────────────

    def _on_task_completed(self, task_id: str, result: dict) -> None:
        """后台翻译/润色任务完成回调。"""
        succ = result.get("success_count", 0)
        fail = result.get("failed_count", 0)
        skip = result.get("skipped_count", 0)
        entry_count = result.get("entry_count", succ + fail + skip)
        parts = []
        if entry_count:
            parts.append(f"共 {entry_count} 条")
        if succ:
            parts.append(f"成功 {succ}")
        if fail:
            parts.append(f"失败 {fail}")
        if skip:
            parts.append(f"跳过 {skip}")
        msg = f"任务 {task_id} 完成: {', '.join(parts)}" if parts else f"任务 {task_id} 完成"
        self._conversation.add_observation("start_translation", msg)
        self.add_system_message(f"[OK] {msg}")
        if self._check_react_depth():
            self._run_llm_round()

    def _on_task_failed(self, task_id: str, error: str) -> None:
        """后台任务失败回调。"""
        msg = f"任务 {task_id} 失败: {error}"
        self._conversation.add_observation("start_translation", msg)
        self.add_system_message(f"[FAIL] {msg}")
        if self._check_react_depth():
            self._run_llm_round()

    def _on_token_stats_updated(self, stats) -> None:
        """更新观测面板 Token 统计 (S11)。"""
        today = self._obs_token_labels.get("今日")
        if today:
            today.setText(f"今日\n输入: {stats.input_tokens}\n输出: {stats.output_tokens}")

    # ── 护栏 ────────────────────────────────────────────────

    def _ensure_middlewares(self) -> list:
        """B1: 延迟构建护栏中间件链，遵循用户配置。"""
        if self._middlewares is not None:
            return self._middlewares
        try:
            from src.transbridge.paratranz.config_manager import LLMConfig
            cfg = LLMConfig.load_from_file()
        except Exception:
            from src.transbridge.smart_assistant.tools.base import _build_guard_chain
            self._middlewares = _build_guard_chain() or []
            return self._middlewares

        from src.transbridge.smart_assistant.guardrails import PermissionGuard, InputValidationGuard, OutputValidationGuard
        middlewares = []
        if getattr(cfg, 'guardrails_enable_input_validation', True):
            middlewares.append(InputValidationGuard(getattr(cfg, 'guardrails_max_input_size', 102400)))
        middlewares.append(PermissionGuard(
            enable_admin_confirm=getattr(cfg, 'guardrails_enable_admin_confirm', True),
            write_require_confirm=getattr(cfg, 'guardrails_write_require_confirm', False),
        ))
        if getattr(cfg, 'guardrails_enable_output_validation', True):
            middlewares.append(OutputValidationGuard())
        self._middlewares = middlewares
        return middlewares

    # ── ReAct 模式 ───────────────────────────────────────────

    def _on_tool_executed(self, step: dict) -> None:
        tool_name = step.get("tool", "?")
        from src.transbridge.smart_assistant.tool_registry import ToolRegistry
        spec = ToolRegistry.get(tool_name)
        if spec and spec.execute:
            from src.transbridge.smart_assistant.tools.base import execute_with_guardrails, ExecutionContext, ToolResult
            from src.transbridge.smart_assistant.tools.task_manager import TaskManager
            exec_ctx = ExecutionContext(app_context=self._ctx, task_manager=TaskManager())
            # M6: ReAct 模式重试循环，集成 RetryHandler
            try:
                from src.transbridge.smart_assistant.reflexion.retry_handler import RetryHandler
                retry_handler = RetryHandler()
            except ImportError:
                retry_handler = None
            max_attempts = retry_handler.MAX_RETRIES + 1 if retry_handler else 3
            current_step = dict(step)
            for attempt in range(max_attempts):
                try:
                    result = execute_with_guardrails(
                        spec, current_step.get("args", {}), exec_ctx,
                        middlewares=self._ensure_middlewares(),
                    )
                except Exception as exc:
                    result = ToolResult.fail(str(exc))
                if getattr(result, 'success', False) or attempt == max_attempts - 1:
                    break
                err_msg = getattr(result, 'message', '')
                if retry_handler and retry_handler.should_retry(err_msg):
                    adjusted = retry_handler.analyze_and_adjust(current_step, err_msg, attempt)
                    if adjusted:
                        current_step = adjusted
                        self.add_system_message(f"[重试 {attempt+2}/{max_attempts}] {spec.name}: {err_msg}")
                        continue
                break
        else:
            from src.transbridge.smart_assistant.tools.base import ToolResult
            result = ToolResult.fail(f"未知工具: {tool_name}")
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
            results.append(f"[OK] {t}: 执行完成")
        summary = "\n".join(results)
        self.add_system_message(f"【批量执行完成】\n{summary}")
        self._conversation.add_observation("batch", summary)
        if self._check_react_depth():
            self._run_llm_round()

    def _handle_tool_result(self, step: dict, result) -> None:
        """统一处理工具执行结果，接受 ToolResult 或 dict。"""
        tool_name = step.get("tool", "?")
        from src.transbridge.smart_assistant.tools.base import ToolResult
        if isinstance(result, ToolResult):
            success = result.success
            message = result.message
        elif isinstance(result, dict):
            success = result.get("success")
            message = result.get("message", result.get("error", ""))
        else:
            success = False
            message = str(result)

        if success:
            msg = f"[OK] {tool_name}: {message or '完成'}"
        else:
            msg = f"[FAIL] {tool_name}: {message or '失败'}"
        self.add_system_message(msg)
        self._conversation.add_observation(tool_name, msg)
        if self._check_react_depth():
            self._run_llm_round()

    def _check_react_depth(self) -> bool:
        if self._react_depth >= self._MAX_REACT_DEPTH:
            self.add_system_message("已达最大推理深度，对话终止。")
            self._react_depth = 0
            return False
        return True

    def _auto_execute_steps(self, steps: list, mode: str) -> None:
        """自动模式下直接执行工具/计划，跳过确认卡片。admin 级工具始终需确认。"""
        from src.transbridge.smart_assistant.tool_registry import ToolRegistry

        def _is_admin(step: dict) -> bool:
            spec = ToolRegistry.get(step.get("tool", ""))
            return spec is not None and getattr(spec, "permission", "") == "admin"

        if any(_is_admin(s) for s in steps):
            # 有 admin 级工具 → 回退到手动确认卡片
            self.add_system_message("(自动模式：检测到敏感操作，切换为手动确认)")
            if mode == "plan":
                self.add_plan_card(steps)
            elif len(steps) == 1:
                self.add_tool_card(steps[0])
            else:
                self.add_batch_tool_card(steps)
            return

        self.add_system_message(f"(自动模式：直接执行 {len(steps)} 步)")
        if mode == "plan":
            self._on_plan_confirmed(steps)
        else:
            for s in steps:
                self._on_tool_executed(s)

    def _on_auto_mode_toggled(self, checked: bool) -> None:
        self._auto_mode = checked
        try:
            QSettings("TransBridge", "SmartAssistant").setValue("auto_mode", checked)
        except Exception:
            pass

    def _on_retry(self) -> None:
        """网络错误后重试：清理旧 worker，重新发送上一条消息。"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            # m19: 3s 超时添加错误处理
            try:
                finished = self._worker.wait(3000)
                if not finished:
                    self.add_system_message("[警告] 上一轮 worker 未在 3s 内终止，已强制继续")
            except Exception:
                pass
        self._worker = None
        self.add_system_message("正在重试…")
        self._run_llm_round()

    # ── 内部方法 ──────────────────────────────────────────────

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return

        # 中断正在进行的流式输出
        self._streaming_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        self._worker = None
        if self._streaming_bubble:
            idx = self._msg_layout.indexOf(self._streaming_bubble)
            if idx >= 0:
                self._msg_layout.removeWidget(self._streaming_bubble)
                self._streaming_bubble.deleteLater()
            self._streaming_bubble = None
        self._streaming_text = ""
        self._streaming_dirty = False

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
        # M14: 检查并取消运行中的 worker/engine
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
            self._worker = None
        if self._engine:
            self._engine.cancel()
            self._engine = None
        self._react_depth = 0
        self._consecutive_errors = 0

        self._streaming_timer.stop()
        self._streaming_bubble = None
        self._streaming_text = ""
        self._streaming_dirty = False
        while self._msg_layout.count() > 1:
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._conversation.clear()
        # M13联动: 释放上传文件引用
        self._uploaded_docs.clear()

    def _add_bubble(self, bubble: MessageBubble) -> None:
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def _add_widget(self, widget: QWidget) -> None:
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, widget)
        self._scroll_to_bottom()

    def _toggle_obs_panel(self) -> None:
        """展开/折叠观测面板。"""
        visible = not self._obs_tabs.isVisible()
        self._obs_tabs.setVisible(visible)
        self._obs_header.setText("[v] 观测面板" if visible else "[>] 观测面板")

    def _scroll_to_bottom(self) -> None:
        vsb = self._scroll.verticalScrollBar()
        if vsb:
            vsb.setValue(vsb.maximum())
        self._back_to_bottom_btn.setVisible(False)

    def _on_scroll_changed(self, value: int) -> None:
        vsb = self._scroll.verticalScrollBar()
        if not vsb:
            return
        max_val = vsb.maximum()
        # 用户手动上滚超过一屏 → 显示回到底部按钮
        if max_val > 0 and value < max_val - 50:
            self._back_to_bottom_btn.setVisible(True)
            # 定位到右下角
            sa = self._scroll
            btn = self._back_to_bottom_btn
            btn.move(
                sa.width() - btn.width() - 12,
                sa.height() - btn.height() - 12,
            )
        else:
            self._back_to_bottom_btn.setVisible(False)

    def _on_back_to_bottom(self) -> None:
        self._scroll_to_bottom()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._back_to_bottom_btn.isVisible():
            sa = self._scroll
            btn = self._back_to_bottom_btn
            btn.move(
                sa.width() - btn.width() - 12,
                sa.height() - btn.height() - 12,
            )

    def eventFilter(self, obj, event):
        if obj == self._input and event.type() == QEvent.Type.KeyPress:
            ke = event
            if (ke.key() == Qt.Key.Key_Return and
                    ke.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

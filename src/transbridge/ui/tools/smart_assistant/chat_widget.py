from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QTextEdit, QPushButton, QFileDialog, QLabel,
    QMessageBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QHeaderView, QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QTimer, QSettings, QObject
import logging
from pathlib import Path

from .message_bubble import MessageBubble
from .quick_actions import QuickActionsChips
from src.transbridge.smart_assistant.conversation_manager import ConversationManager
from src.transbridge.smart_assistant.chat_worker import ChatWorker
from src.transbridge.smart_assistant.execution_engine import ExecutionEngine, StepResult
from .tool_card import ToolCard, BatchToolCard
from .plan_card import PlanCard

logger = logging.getLogger(__name__)


class _SignalBridge(QObject):
    """Worker→主线程回调桥接器。

    在 worker 线程中调用 _dispatch.emit(callback)，Qt 自动排队到主线程执行。
    1 个 QObject + 1 个 pyqtSignal，C 栈开销可忽略。
    """
    _dispatch = pyqtSignal(object)


class ChatWidget(QWidget):
    """聊天区域：消息滚动列表 + 输入框 + 发送/清空按钮 + 双模式循环控制。"""

    _MAX_REACT_DEPTH = 10
    _STREAMING_FLUSH_MS = 50

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
        self._pending_memory_context = ""      # BR2: 待注入的记忆上下文

        # CR7: LLM 客户端缓存 — 配置不变时复用，避免重复创建 HTTP 连接池
        self._llm_client = None
        self._llm_client_config_hash = None

        # m2: 重试按钮引用，防止多次错误叠加多个重试按钮
        self._retry_btn: QPushButton | None = None

        # B2: TaskManager 信号延迟到首次 LLM 轮次时连接，避免 UI 构建期 C 栈溢出
        self._task_manager_connected = False

        # Worker→主线程回调桥接器
        self._cb_bridge = _SignalBridge()
        self._cb_bridge._dispatch.connect(lambda cb: cb())

        # Micro-stage 拆分: Stage A → B → C 之间的临时状态传递
        self._round_messages: list = []
        self._round_max_tokens: int = 2048

        # 流式打字机状态
        self._streaming_text = ""
        self._streaming_bubble: MessageBubble | None = None
        self._streaming_dirty = False

        # 自动模式 (QSettings 持久化)
        self._auto_mode = False
        try:
            qs = QSettings("TransBridge", "SmartAssistant")
            self._auto_mode = qs.value("auto_mode", False, type=bool)
        except Exception as e:
            logger.debug("QSettings auto_mode 读取失败，使用默认值 False: %s", e)

        # 可观测性收集器引用（延迟创建）
        self._obs_collector = None
        # 长期记忆引用（延迟创建）
        self._memory_store = None
        self._memory_retriever = None
        # UI 控件引用（延迟创建，resizeEvent 等可能在 _init_ui 前触发）
        self._main_layout: QVBoxLayout | None = None
        self._back_to_bottom_btn: QPushButton | None = None
        self._scroll: QScrollArea | None = None
        self._input: QTextEdit | None = None

        # 延后 UI 构建：__init__ 累积的 Python→C++ 调用在 Windows 1MB C 栈
        # 上可能溢出 (0xC00000FD)。通过 QTimer.singleShot 将 QObject 密集的
        # UI 构建推迟到事件循环空闲时，此时 C 栈已完全展开。
        QTimer.singleShot(0, self._init_ui)

    def _init_ui(self) -> None:
        """延迟构建 UI 入口：分 4 阶段串行化，每阶段间 C 栈完全展开。

        从 __init__ 通过 QTimer.singleShot(0) 调用。每阶段结束调度下一阶段，
        避免单次调用帧内累积过多 Python→C++ QObject 创建导致 C 栈溢出。
        """
        self._init_ui_stage1()

    def _init_ui_stage1(self) -> None:
        """Stage 1/4: QTimers + 长期记忆 + 可观测性收集器。"""
        from src.transbridge.config.paths import get_data_dir

        # ── QTimers ──
        self._pending_scroll_value = 0
        self._scroll_throttle_timer = QTimer(self)
        self._scroll_throttle_timer.setInterval(100)
        self._scroll_throttle_timer.setSingleShot(True)
        self._scroll_throttle_timer.timeout.connect(self._update_scroll_button)

        self._streaming_timer = QTimer(self)
        self._streaming_timer.setInterval(self._STREAMING_FLUSH_MS)
        self._streaming_timer.timeout.connect(self._flush_streaming)

        # ── 长期记忆 ──
        from src.transbridge.smart_assistant.memory import MemoryStore, MemoryRetriever
        self._memory_store = MemoryStore(
            Path(get_data_dir()) / "memory", embedding_mode="disabled"
        )
        _emb_client = None
        try:
            from src.transbridge.paratranz.config_manager import LLMConfig as _LLMCfg
            _cfg = _LLMCfg.load_from_file()
            if _cfg.embedding.mode != "disabled" and _cfg.embedding.api_key:
                from src.transbridge.infra import create_llm_client
                _emb_client = create_llm_client(_cfg.embedding.api_key, _cfg.embedding.base_url)
        except Exception as e:
            logger.info("Embedding 客户端创建失败，语义检索降级: %s", e)
        self._memory_retriever = MemoryRetriever(self._memory_store, embedding_client=_emb_client)

        # ── 可观测性收集器 ──
        from src.transbridge.smart_assistant.observability import ObservabilityCollector
        self._obs_collector = ObservabilityCollector(
            storage_dir=Path(get_data_dir()) / "observability",
            on_token_stats_updated=lambda stats: self._on_token_stats_updated(stats),
        )

        QTimer.singleShot(0, self._init_ui_stage2)

    def _init_ui_stage2(self) -> None:
        """Stage 2/4: 布局 + 消息滚动区 + 回到底部按钮。"""
        # ── 主布局 ──
        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(0, 0, 0, 0)
        self._main_layout.setSpacing(4)

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

        self._main_layout.addWidget(self._scroll, stretch=1)

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

        vsb = self._scroll.verticalScrollBar()
        if vsb:
            vsb.valueChanged.connect(self._on_scroll_changed)

        QTimer.singleShot(0, self._init_ui_stage3)

    def _init_ui_stage3(self) -> None:
        """Stage 3/4: 工具栏(chips+上传) + 观测面板(QTabWidget/QTableWidget)。"""
        assert self._main_layout is not None

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
        self._main_layout.addLayout(toolbar)

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
        self._main_layout.addWidget(self._obs_header)

        self._obs_tabs = QTabWidget()
        self._obs_tabs.setMaximumHeight(160)
        self._obs_tabs.setStyleSheet("font-size: 10px;")
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
        self._obs_tool_table = QTableWidget(0, 4)
        self._obs_tool_table.setHorizontalHeaderLabels(["时间", "工具", "耗时", "状态"])
        self._obs_tool_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._obs_tabs.addTab(self._obs_tool_table, "工具调用")
        self._obs_round_list = QListWidget()
        self._obs_tabs.addTab(self._obs_round_list, "轮次")
        self._main_layout.addWidget(self._obs_tabs)
        self._obs_tabs.setVisible(False)

        QTimer.singleShot(0, self._init_ui_stage4)

    def _init_ui_stage4(self) -> None:
        """Stage 4/4: 输入框 + 按钮行 + 异步通知调度。"""
        assert self._main_layout is not None

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
        self._main_layout.addWidget(self._input)

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
        self._auto_cb = QCheckBox("Auto")
        self._auto_cb.setToolTip(
            "自动模式：LLM返回工具/计划时直接执行，不显示确认卡片（admin级工具始终确认）"
        )
        self._auto_cb.setChecked(self._auto_mode)
        self._auto_cb.toggled.connect(self._on_auto_mode_toggled)
        self._auto_cb.setStyleSheet(
            "QCheckBox { font-size: 11px; color: #888; spacing: 4px; }"
            "QCheckBox:hover { color: #555; }"
        )
        btn_row.addWidget(self._auto_cb)
        btn_row.addStretch()
        btn_row.addWidget(send_btn)
        self._main_layout.addLayout(btn_row)

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

    def _get_llm_client(self):
        """CR7: 缓存 LLM 客户端，仅配置变更时重建。"""
        from src.transbridge.paratranz.config_manager import LLMConfig
        from src.transbridge.infra.llm_client import create_llm_client
        cfg = LLMConfig.load_from_file()
        if not cfg.api_key:
            return None
        current_hash = hash((cfg.api_key, cfg.provider, cfg.base_url, cfg.model))
        if self._llm_client is not None and self._llm_client_config_hash == current_hash:
            return self._llm_client
        self._llm_client = create_llm_client(cfg)
        self._llm_client_config_hash = current_hash
        return self._llm_client

    def _run_llm_round(self) -> None:
        """Stage A: 纯 Python 准备工作（无 QObject 创建）。

        将 QObject 密集的 MessageBubble (Stage B) 和 ChatWorker (Stage C)
        分别延迟到后续事件循环帧，避免单帧内累积 3+ QObject 创建导致
        Windows 1MB C 栈溢出 (0xC00000FD)。
        """
        # B2: 懒连接 TaskManager，首次 LLM 轮次时才触发
        self._ensure_task_manager()

        client = self._get_llm_client()
        if client is None:
            self.add_system_message("请先在设置中配置 LLM API Key")
            return

        # 确保 system prompt 已设置
        if not any(m["role"] == "system" for m in self._conversation.get_messages()):
            from src.transbridge.smart_assistant.context_builder import ContextBuilder
            from src.transbridge.smart_assistant.prompts import build_system_prompt
            self._ctx._uploaded_docs = self._uploaded_docs
            context = ContextBuilder(self._ctx).build()
            sys_prompt = build_system_prompt(context)
            if self._pending_memory_context:
                sys_prompt = sys_prompt + "\n\n" + self._pending_memory_context
                self._pending_memory_context = ""
            self._conversation.add_system(sys_prompt)

        self._react_depth += 1
        self._round_messages = self._conversation.get_messages()
        # m12: 从 LLMConfig 读取 max_tokens 配置
        try:
            from src.transbridge.paratranz.config_manager import LLMConfig as _LLMCfg
            _cfg = _LLMCfg.load_from_file()
            self._round_max_tokens = getattr(_cfg, 'max_output_tokens', 0) or 2048
        except Exception as e:
            logger.debug("LLMConfig max_output_tokens 读取失败，使用默认值 2048: %s", e)
            self._round_max_tokens = 2048

        QTimer.singleShot(0, self._run_llm_round_stage_b)

    def _run_llm_round_stage_b(self) -> None:
        """Stage B: 创建 MessageBubble (QWidget)，插入布局后调度 Stage C。"""
        self._streaming_text = ""
        self._streaming_bubble = MessageBubble("...", "assistant")
        self._add_bubble(self._streaming_bubble)
        QTimer.singleShot(0, self._run_llm_round_stage_c)

    def _run_llm_round_stage_c(self) -> None:
        """Stage C: 创建 ChatWorker (threading.Thread)，赋值回调，启动后台线程。

        Phase 2: ChatWorker 从 QThread+pyqtSignal 迁移到 AsyncWorker+回调。
        回调在 worker 线程中调用，通过 _SignalBridge._dispatch.emit() 排队到主线程。
        pyqtSignal.emit() 在非主线程调用时自动使用 QueuedConnection，等价于原行为。
        """
        client = self._get_llm_client()
        messages = self._round_messages
        _max = self._round_max_tokens
        # 清理临时状态引用
        del self._round_messages
        del self._round_max_tokens

        _bridge = self._cb_bridge

        self._worker = ChatWorker(client, messages, max_tokens=_max)
        self._worker.on_chunk = lambda c: _bridge._dispatch.emit(
            lambda: self._on_llm_chunk(c))
        self._worker.on_finished = lambda t: _bridge._dispatch.emit(
            lambda: self._on_llm_finished(t))
        self._worker.on_error = lambda m: _bridge._dispatch.emit(
            lambda: self._on_llm_error(m))
        self._worker.on_token_usage = lambda model, i, o: _bridge._dispatch.emit(
            lambda: self._obs_collector.on_llm_tokens(model, i, o))
        self._worker.start()

    def _on_llm_chunk(self, chunk: str) -> None:
        self._streaming_text += chunk
        self._streaming_dirty = True
        if not self._streaming_timer.isActive():
            self._streaming_timer.start()

    def _flush_streaming(self) -> None:
        """节流渲染：每 _STREAMING_FLUSH_MS ms 将累积文本刷新到气泡中。

        M14: 流式模式下跳过 markdown 渲染，直接用 QLabel 纯文本更新，
        避免每次刷新重建 MarkdownRenderer widget 树。流式结束后由
        _on_llm_finished 调用 set_text() 完成最终 markdown 渲染。
        """
        if not self._streaming_dirty or self._streaming_bubble is None:
            self._streaming_timer.stop()
            return
        self._streaming_dirty = False
        # M14: 流式模式下直接更新纯文本，避免 MarkdownRenderer 重建 widget 树
        bubble = self._streaming_bubble
        if bubble._role != "system" and bubble._inner is not None:
            inner_layout = bubble._inner.layout()
            if inner_layout is not None:
                if bubble._content is not None and not isinstance(bubble._content, QLabel):
                    # 首次流式刷新：将 markdown content 替换为 QLabel
                    inner_layout.removeWidget(bubble._content)
                    bubble._content.deleteLater()
                    bubble._content = None
                if bubble._content is None:
                    bubble._content = QLabel(self._streaming_text)
                    bubble._content.setWordWrap(True)
                    bubble._content.setTextFormat(Qt.TextFormat.PlainText)
                    inner_layout.addWidget(bubble._content)
                else:
                    bubble._content.setText(self._streaming_text)
        else:
            bubble.set_text(self._streaming_text)
        self._scroll_to_bottom()

    def _on_llm_finished(self, response: str) -> None:
        # 停止流式 timer，最终刷新
        self._streaming_timer.stop()
        if self._streaming_bubble:
            self._flush_streaming()
            # M14: 流式结束后用 markdown 渲染最终结果，替换流式期间的纯文本 QLabel
            if self._streaming_bubble._role != "system":
                self._streaming_bubble.set_text(self._streaming_text)
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
        if not steps:
            self._obs_collector.end_conversation()

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
        except Exception as e:
            logger.warning("记忆记录失败: %s", e)

        # CR8: 清理 worker，清除回调并释放线程
        if self._worker:
            self._worker.on_chunk = None
            self._worker.on_finished = None
            self._worker.on_error = None
            self._worker.on_token_usage = None
            self._worker = None

    def _on_llm_error(self, msg: str) -> None:
        self._streaming_timer.stop()
        self._streaming_text = ""
        self._streaming_dirty = False
        self._react_depth = 0
        self._consecutive_errors += 1

        # m1: 错误路径清理流式气泡，防止 UI 残留
        if self._streaming_bubble:
            idx = self._msg_layout.indexOf(self._streaming_bubble)
            if idx >= 0:
                self._msg_layout.removeWidget(self._streaming_bubble)
                self._streaming_bubble.deleteLater()
            self._streaming_bubble = None

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
                # m2: 移除旧的重试按钮，防止叠加
                if self._retry_btn is not None:
                    idx = self._msg_layout.indexOf(self._retry_btn)
                    if idx >= 0:
                        self._msg_layout.removeWidget(self._retry_btn)
                        self._retry_btn.deleteLater()
                    self._retry_btn = None
                retry_btn = QPushButton("重试")
                retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                retry_btn.clicked.connect(self._on_retry)
                self._add_widget(retry_btn)
                self._retry_btn = retry_btn
        else:
            self.add_system_message(f"请求失败: {msg}")

        # CR8: 清理 worker，清除回调并释放线程
        if self._worker:
            self._worker.on_chunk = None
            self._worker.on_finished = None
            self._worker.on_error = None
            self._worker.on_token_usage = None
            self._worker = None

    # ── 计划模式 ─────────────────────────────────────────────

    def _on_plan_confirmed(self, steps: list) -> None:
        from src.transbridge.smart_assistant.tool_registry import ToolRegistry

        middlewares = self._ensure_middlewares()
        self._engine = ExecutionEngine(ToolRegistry, self._ctx, middlewares=middlewares)
        self._engine.all_finished.connect(self._on_plan_all_finished)
        self._engine.step_requires_confirmation.connect(self._on_confirm_required)
        # Observability
        self._obs_collector.start_conversation(f"conv_{id(steps)}")
        self._engine.step_started.connect(self._obs_collector.on_step_started)
        self._engine.step_finished.connect(self._obs_collector.on_step_finished)
        self._engine.step_retrying.connect(self._obs_collector.on_step_retrying)
        # CR15: 复用引擎内置的 ThreadPoolExecutor 替代冗余 daemon 线程
        self._engine._executor.submit(self._engine.execute, steps)

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
        # M5: 计划执行完后检查 ReAct 深度，超限则停止
        if not self._check_react_depth():
            return
        self._run_llm_round()

    # ── 确认 (S08) ──────────────────────────────────────────

    def _on_confirm_required(self, node_id: str, prompt: str, choices: list) -> None:
        reply = QMessageBox.question(self, "操作确认", prompt,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        choice = choices[0] if reply == QMessageBox.StandardButton.Yes else (choices[1] if len(choices) > 1 else "跳过")
        if self._engine:
            self._engine.provide_decision(node_id, choice)

    # ── 异步任务通知 (B2) ──────────────────────────────────

    def _ensure_task_manager(self) -> None:
        """懒初始化 TaskManager 单例并注册回调（幂等）。

        不在 UI 构建期执行，而是延迟到首次 _run_llm_round 调用时。
        此时 C 栈已完全展开。Phase 1: TaskManager 去 QObject 化后改用回调列表替代 pyqtSignal，
        消除 QObject.__new__ 的 C++ 元对象栈开销。
        """
        if self._task_manager_connected:
            return
        self._task_manager_connected = True
        try:
            from src.transbridge.smart_assistant.tools.task_manager import TaskManager
            tm = TaskManager()
            tm.on_completed(self._on_task_completed)
            tm.on_failed(self._on_task_failed)
        except Exception as e:
            logger.error("TaskManager 初始化失败，后台任务通知不可用: %s", e)
            self._task_manager_connected = False

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
        except Exception as e:
            logger.warning("护栏配置加载失败，使用默认护栏链: %s", e)
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

            # CR3: ReAct 模式权限预检查 — 将确认需求改为用户交互而非直接拒绝
            middlewares = self._ensure_middlewares()
            from src.transbridge.smart_assistant.guardrails.permission import PermissionGuard
            perm_guard = next((mw for mw in middlewares if isinstance(mw, PermissionGuard)), None)
            if perm_guard is not None:
                perm_result = perm_guard.before_execute(step, exec_ctx)
                if not perm_result.allowed and perm_result.reason in ("admin_confirm_required", "write_confirm_required"):
                    perm_label = "管理级" if "admin" in perm_result.reason else "写入级"
                    reply = QMessageBox.question(
                        self, "操作确认",
                        f"工具 '{tool_name}' 需要{perm_label}权限确认。是否继续？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        result = ToolResult.fail(f"用户拒绝{perm_label}操作: {tool_name}")
                        self._handle_tool_result(step, result)
                        return
                    # 用户确认后，从护栏链中移除 PermissionGuard 以避免二次拦截
                    middlewares = [mw for mw in middlewares if not isinstance(mw, PermissionGuard)]

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
                        middlewares=middlewares,
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
            self._obs_collector.end_conversation()
            self.add_system_message("已达最大推理深度，对话终止。")
            self._react_depth = 0
            return False
        return True

    def _auto_execute_steps(self, steps: list, mode: str) -> None:
        """自动模式下直接执行工具/计划，跳过确认卡片。admin 级工具始终需确认。"""
        from src.transbridge.smart_assistant.tool_registry import ToolRegistry

        def _needs_confirm(step: dict) -> bool:
            spec = ToolRegistry.get(step.get("tool", ""))
            if spec is None:
                return False
            # MA1: admin 级工具 + require_confirmation 工具均需确认
            return (getattr(spec, "permission", "") == "admin"
                    or getattr(spec, "require_confirmation", False))

        if any(_needs_confirm(s) for s in steps):
            # 有需确认的工具 → 回退到手动确认卡片
            self.add_system_message("(自动模式：检测到需确认的操作，切换为手动确认)")
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
        except Exception as e:
            logger.info("QSettings auto_mode 保存失败: %s", e)

    def _on_retry(self) -> None:
        """网络错误后重试：清理旧 worker，重新发送上一条消息。"""
        if self._worker and self._worker.is_alive():
            self._worker.cancel()
            # m19: 3s 超时添加错误处理
            try:
                self._worker.join(timeout=3)
                finished = not self._worker.is_alive()
                if not finished:
                    self.add_system_message("[警告] 上一轮 worker 未在 3s 内终止，已强制继续")
            except Exception as e:
                logger.warning("Worker 终止等待异常: %s", e)
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
        if self._worker and self._worker.is_alive():
            self._worker.cancel()
            self._worker.join(timeout=3)
        self._worker = None
        if self._streaming_bubble:
            idx = self._msg_layout.indexOf(self._streaming_bubble)
            if idx >= 0:
                self._msg_layout.removeWidget(self._streaming_bubble)
                self._streaming_bubble.deleteLater()
            self._streaming_bubble = None
        self._streaming_text = ""
        self._streaming_dirty = False

        self.add_user_bubble(text)
        self._input.clear()
        self._conversation.add_user(text)
        self._react_depth = 0

        # M18: 延迟到事件循环执行检索+LLM，避免主线程同步检索阻塞 UI
        QTimer.singleShot(0, lambda: self._do_send_retrieve_and_run(text))

    def _do_send_retrieve_and_run(self, text: str) -> None:
        """M18: 延迟执行记忆检索与 LLM 轮次，避免阻塞主线程。"""
        self._pending_memory_context = ""
        if self._memory_store.count > 0:
            try:
                memories = self._memory_retriever.retrieve(text, top_k=3)
                if memories:
                    mem_lines = ["相关历史记忆:"]
                    for m in memories:
                        mem_lines.append(f"  - [{m.type}] {m.summary}")
                    self._pending_memory_context = "\n".join(mem_lines)
            except Exception as e:
                logger.info("记忆检索失败: %s", e)
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
                # m38: 过滤错误消息中可能泄露的文件路径
                _exc_msg = str(exc)
                try:
                    _exc_msg = _exc_msg.replace(str(fpath), fpath.name)
                except Exception:
                    pass  # 路径脱敏失败不影响错误消息展示，沿用原始消息
                self.add_system_message(f"解析文件失败: {fpath.name} — {_exc_msg}")
        names = ", ".join(self._uploaded_docs.keys())
        self._upload_label.setText(f"已上传: {names}" if names else "")

    def _clear_conversation(self) -> None:
        # M14: 检查并取消运行中的 worker/engine
        if self._worker and self._worker.is_alive():
            self._worker.cancel()
            self._worker.join(timeout=2)
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
        # M15: 从末尾向前移除 widget，避免每次 takeAt(0) 导致 O(n²) 内部移位
        # 布局最后一项是 stretch，从 count-2 开始移除实际 widget
        count = self._msg_layout.count()
        while count > 1:
            item = self._msg_layout.takeAt(count - 2)
            if item.widget():
                item.widget().deleteLater()
            count -= 1
        self._conversation.clear()
        # M13联动: 释放上传文件引用
        self._uploaded_docs.clear()
        # m2联动: 清空重试按钮引用
        self._retry_btn = None

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
        # m3: 节流 — 记录值并启动 100ms 单次定时器，由 _update_scroll_button 执
        # 行实际更新，避免每次像素变化都触发 signal/slot + move() 计算
        self._pending_scroll_value = value
        if not self._scroll_throttle_timer.isActive():
            self._scroll_throttle_timer.start()

    def _update_scroll_button(self) -> None:
        """m3: 100ms 节流后更新回到底部按钮的可见性和位置。"""
        vsb = self._scroll.verticalScrollBar()
        if not vsb:
            return
        max_val = vsb.maximum()
        value = self._pending_scroll_value
        if max_val > 0 and value < max_val - 50:
            self._back_to_bottom_btn.setVisible(True)
            # m3-fix: 延迟 btn.move() 到下一事件循环，切断 QScrollArea 内部
            # 布局反馈循环 (0xC00000FD stack overflow)。
            QTimer.singleShot(0, self._reposition_back_to_bottom_btn)
        else:
            self._back_to_bottom_btn.setVisible(False)

    def _on_back_to_bottom(self) -> None:
        self._scroll_to_bottom()

    def _reposition_back_to_bottom_btn(self) -> None:
        """m3-fix: 延迟定位「回到底部」按钮，切断 Qt 布局反馈循环。"""
        try:
            btn = self._back_to_bottom_btn
            if btn.isVisible():
                sa = self._scroll
                btn.move(
                    sa.width() - btn.width() - 12,
                    sa.height() - btn.height() - 12,
                )
        except RuntimeError:
            pass  # 控件已被销毁

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._back_to_bottom_btn is not None and self._back_to_bottom_btn.isVisible():
            # m3-fix: 延迟 btn.move() 到下一事件循环，切断 QScrollArea 内部
            # 布局反馈循环 (0xC00000FD stack overflow)。
            QTimer.singleShot(0, self._reposition_back_to_bottom_btn)

    def eventFilter(self, obj, event):
        if obj == self._input and event.type() == QEvent.Type.KeyPress:
            ke = event
            if (ke.key() == Qt.Key.Key_Return and
                    ke.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

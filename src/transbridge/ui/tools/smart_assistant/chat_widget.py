from __future__ import annotations

# TODO: i18n — 所有用户可见字符串均为硬编码中文，待国际化改造
"""
ChatWidget 颜色面板 (无主题系统 — 所有值硬编码在各控件 StyleSheet 中):

  语义色:
    主色/成功:        #4CAF50  (绿色, 发送按钮/AI头像/聚焦边框)
    主色深色(hover):   #43A047
    主色更深(pressed): #388E3C
    主色浅色(disabled):#A5D6A7
    危险/失败:         #D32F2F  (红色左边框, 系统消息)
    危险背景:          #FFEBEE
    成功背景:          #E8F5E9

  中性色:
    文字主色:          #333     (消息内容, 系统消息文字)
    文字次级:          #666     (上传按钮文字, 清空按钮文字)
    文字三级:          #888     (上传状态标签, Auto复选框)
    文字悬停:          #555     (Auto复选框悬停)
    输入框背景:        #fff     (白色)
    用户气泡背景:      #f7f7f7  (淡灰)
    系统消息默认背景:  #F5F5F5
    系统消息默认边框:  #757575
    按钮默认背景:      #f5f5f5  (清空/上传按钮)
    按钮默认边框:      #ddd
    按钮悬停背景:      #e8e8e8

  覆盖色:
    回到底部按钮背景:  rgba(0,0,0,0.55)
    回到底部按钮悬停:  rgba(0,0,0,0.7)
    回到底部按钮文字:  white
    发送按钮文字:      white
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QTextEdit, QPushButton, QFileDialog, QLabel, QFrame,
    QMessageBox, QCheckBox,
)
from PyQt6.QtCore import Qt, QEvent, QTimer, QSettings
from PyQt6.QtGui import QShortcut, QKeySequence
import logging
import os
import re
from pathlib import Path

from .message_bubble import MessageBubble
from .quick_actions import QuickActionsChips
from .thinking_indicator import ThinkingIndicator
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler
from transbridge.smart_assistant.conversation_orchestrator import ConversationOrchestrator
from transbridge.smart_assistant.conversation_manager import ConversationManager
from transbridge.smart_assistant.execution_engine import ExecutionEngine, StepResult
from transbridge.smart_assistant.session_controller import SessionController  # FR12: Story 01
from .tool_card import ToolCard, BatchToolCard
from .plan_card import PlanCard

logger = logging.getLogger(__name__)


class ChatWidget(QWidget):
    """聊天区域：消息滚动列表 + 输入框 + 发送/清空按钮 + 双模式循环控制。"""

    MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
    MAX_VISIBLE_WIDGETS = 100  # M52: 消息区最大控件数，超出时从头部移除最旧控件

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx

        # 全局字体
        from PyQt6.QtGui import QFont
        font = QFont("Microsoft YaHei", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)

        self._conversation = ConversationManager(max_turns=20)
        self._engine: ExecutionEngine | None = None
        self._uploaded_docs: dict[str, object] = {}  # filename → ParsedDocument
        self._pending_memory_context = ""      # BR2: 待注入的记忆上下文

        # m2: 重试按钮引用，防止多次错误叠加多个重试按钮
        self._retry_btn: QPushButton | None = None

        # B2: TaskManager 信号延迟到首次 LLM 轮次时连接，避免 UI 构建期 C 栈溢出
        self._task_manager_connected = False

        # FR14: 后台任务监控
        self._task_monitor = None
        self._task_monitor_timer: QTimer | None = None

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
        self._thinking_indicator: ThinkingIndicator | None = None

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
        try:
            from transbridge.config.paths import get_data_dir

            # ── QTimers ──
            self._pending_scroll_value = 0
            self._scroll_throttle_timer = QTimer(self)
            self._scroll_throttle_timer.setInterval(100)
            self._scroll_throttle_timer.setSingleShot(True)
            self._scroll_throttle_timer.timeout.connect(self._update_scroll_button)

            # ── 长期记忆 ──
            from transbridge.smart_assistant.memory import MemoryStore, MemoryRetriever
            self._memory_store = MemoryStore(
                Path(get_data_dir()) / "memory", embedding_mode="disabled",
                persist_to_disk=False,
            )
            _emb_client = None
            try:
                from transbridge.paratranz.config_manager import LLMConfig as _LLMCfg
                _cfg = _LLMCfg.load_from_file()
                if _cfg.embedding.mode != "disabled" and _cfg.embedding.api_key:
                    from transbridge.infra import create_llm_client
                    _emb_client = create_llm_client(_cfg.embedding.api_key, _cfg.embedding.base_url)
            except Exception as e:
                logger.info("Embedding 客户端创建失败，语义检索降级: %s", e)
            self._memory_retriever = MemoryRetriever(self._memory_store, embedding_client=_emb_client)

            # ── 可观测性收集器 ──
            from transbridge.smart_assistant.observability import ObservabilityCollector
            self._obs_collector = ObservabilityCollector(
                storage_dir=Path(get_data_dir()) / "observability",
                on_token_stats_updated=lambda stats: self._on_token_stats_updated(stats),
            )

            # ── 工具执行处理器（Story-09-1）──
            self._tool_handler = ToolExecutionHandler(
                ctx=self._ctx,
                conversation_manager=self._conversation,
                on_system_message=lambda msg: self.add_system_message(msg),
                on_plan_card=lambda steps: self.add_plan_card(steps),
                on_tool_card=lambda step: self.add_tool_card(step),
                on_batch_tool_card=lambda steps: self.add_batch_tool_card(steps),
                on_plan_confirmed=lambda steps: self._on_plan_confirmed(steps),
                on_step_completed=lambda: self._controller.handle_execution_complete([]),
                on_task_started=lambda task_id, run_id: self._controller.handle_task_started(task_id, run_id),
                on_confirm_permission=lambda title, msg: (
                    QMessageBox.question(self, title, msg,
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    ) == QMessageBox.StandardButton.Yes
                ),
            )

            # ── 对话编排器（Story-09-2）──
            self._orchestrator = ConversationOrchestrator(
                ctx=self._ctx,
                conversation_manager=self._conversation,
                tool_execution_handler=self._tool_handler,
                obs_collector=self._obs_collector,
                memory_store=self._memory_store,
                on_system_message=lambda msg: self.add_system_message(msg),
                on_streaming_bubble_factory=lambda: MessageBubble("...", "assistant"),
                on_streaming_flush=lambda text, bubble, dirty: self._do_streaming_flush(text, bubble),
                on_add_bubble=lambda b: self._add_bubble(b),
                on_scroll_to_bottom=lambda: self._scroll_to_bottom(),
                on_thinking_indicator_show=lambda t: self._show_thinking_indicator(t),
                on_thinking_indicator_hide=lambda: self._hide_thinking_indicator(),
                on_plan_card=lambda steps: self.add_plan_card(steps),
                on_tool_card=lambda step: self.add_tool_card(step),
                on_batch_tool_card=lambda steps: self.add_batch_tool_card(steps),
                on_end_conversation=lambda: self._obs_collector.end_conversation(),
                on_remove_widget=lambda w: self._remove_widget_safely(w),
                on_retry_offer=lambda msg: self._offer_retry_button(),
                on_log_memory=lambda msgs, resp: self._log_conversation_memory(msgs, resp),
                on_get_uploaded_docs=lambda: self._uploaded_docs,
                on_get_pending_memory=lambda: self._pending_memory_context,
                on_clear_pending_memory=lambda: setattr(self, '_pending_memory_context', ''),
                # FR12: SessionController 响应回调 + FR13 自动保存+命名
                on_response_parsed=lambda parsed: self._on_response_parsed_safe(parsed),
            )
            # 同步 auto_mode
            self._orchestrator.auto_mode = self._auto_mode

            # ── 会话控制器 (FR12 Story 01: 新旧并行) ──
            self._controller = SessionController(
                orchestrator=self._orchestrator,
                tool_handler=self._tool_handler,
                conversation=self._conversation,
                on_state_changed=lambda old, new, ctx: logger.debug(
                    "SessionController: %s → %s", old.value, new.value),
                on_present_plan_card=lambda steps: self.add_plan_card(steps),
                on_present_tool_card=lambda step: self.add_tool_card(step),
                on_present_batch_tool_card=lambda steps: self.add_batch_tool_card(steps),
                on_system_message=lambda msg: self.add_system_message(msg),
                on_conversation_end=lambda: self._obs_collector.end_conversation(),
                on_llm_round_start=lambda: self._orchestrator.start_round(),
                on_thinking_indicator_hide=lambda: self._hide_thinking_indicator(),
            )
            # 同步 auto_mode 到 controller
            self._controller.auto_mode = self._auto_mode
            logger.debug("SessionController 初始化完成，新旧路径并行运行")
        except Exception as e:
            logger.error("UI初始化 Stage 1/4 失败: %s", e)

        QTimer.singleShot(0, self._init_ui_stage2)

    def _init_ui_stage2(self) -> None:
        """Stage 2/4: 布局 + 消息滚动区 + 回到底部按钮。"""
        try:
            # ── 主布局 ──
            self._main_layout = QVBoxLayout(self)
            self._main_layout.setContentsMargins(0, 0, 0, 0)
            self._main_layout.setSpacing(4)

            # ── 消息滚动区 ──
            self._scroll = QScrollArea()
            self._scroll.setAccessibleName("消息滚动区域")
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
        except Exception as e:
            logger.error("UI初始化 Stage 2/4 失败: %s", e)

        QTimer.singleShot(0, self._init_ui_stage3)

    def _init_ui_stage3(self) -> None:
        """Stage 3/4: 工具栏(chips+上传) + 观测面板(QTabWidget/QTableWidget)。"""
        try:
            assert self._main_layout is not None

            # ── 工具栏：chips 快捷指令 + 上传 ──
            toolbar = QHBoxLayout()
            toolbar.setSpacing(6)
            toolbar.setContentsMargins(4, 2, 4, 2)
            self._chips = QuickActionsChips()
            self._chips.action_clicked.connect(self.set_input)
            self._chips.skill_triggered.connect(self._on_skill)
            toolbar.addWidget(self._chips)
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

            # ── 观测数据内联显示（替代 QTabWidget）──
            self._obs_inline_visible = False
            self._obs_token_labels: dict[str, QLabel] = {}
        except Exception as e:
            logger.error("UI初始化 Stage 3/4 失败: %s", e)

        QTimer.singleShot(0, self._init_ui_stage4)

    def _init_ui_stage4(self) -> None:
        """Stage 4/4: 输入框 + 按钮行 + 异步通知调度。"""
        try:
            assert self._main_layout is not None

            # ── 输入框 ──
            self._input = QTextEdit()
            self._input.setAccessibleName("消息输入框")
            self._input.setMaximumHeight(100)
            self._input.setMinimumHeight(40)
            self._input.document().setMaximumBlockCount(500)  # M21: 限制输入最大块数，防大段粘贴撑爆内存
            self._input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._input.setPlaceholderText("输入消息，Ctrl+Enter 发送  |  输入 /obs 切换观测信息显示")
            self._input.setStyleSheet(
                "QTextEdit {"
                "  border: 1px solid #ddd; border-radius: 8px;"
                "  padding: 6px 10px; font-size: 13px;"
                "  background: #fff; margin: 0 4px;"
                "}"
                "QTextEdit:focus { border-color: #4CAF50; }"
            )
            self._input.installEventFilter(self)
            self._main_layout.addWidget(self._input)

            # ── 按钮行 ──
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(4, 0, 4, 2)
            btn_row.setSpacing(8)
            clear_btn = QPushButton("清空对话")
            clear_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #f5f5f5; border: 1px solid #ddd; border-radius: 8px;"
                "  padding: 5px 12px; font-size: 12px; color: #666;"
                "}"
                "QPushButton:hover { background-color: #e8e8e8; }"
            )
            clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            clear_btn.clicked.connect(self._clear_conversation)
            self._send_btn = QPushButton("发送")
            self._send_btn.setAccessibleName("发送消息按钮")
            self._send_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #4CAF50; color: white; border: none;"
                "  border-radius: 8px; padding: 5px 18px;"
                "  font-size: 13px; font-weight: bold;"
                "}"
                "QPushButton:hover { background-color: #43A047; }"
                "QPushButton:pressed { background-color: #388E3C; }"
                "QPushButton:disabled { background-color: #A5D6A7; }"
            )
            self._send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._send_btn.clicked.connect(self._on_send)
            self._send_btn.setEnabled(False)  # M9: 初始输入为空，禁用发送按钮
            # M9: 输入内容变化时自动切换发送按钮启用/禁用状态
            self._input.textChanged.connect(
                lambda: self._send_btn.setEnabled(
                    bool(self._input.toPlainText().strip())
                )
            )
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
            btn_row.addWidget(self._send_btn)
            self._main_layout.addLayout(btn_row)

            # ── Ctrl+O 快捷键：展开/折叠思考过程 ──
            self._shortcut_ctrl_o = QShortcut(QKeySequence("Ctrl+O"), self)
            self._shortcut_ctrl_o.activated.connect(self._toggle_thought_expand)
        except Exception as e:
            logger.error("UI初始化 Stage 4/4 失败: %s", e)

    # ── 公共方法 ──────────────────────────────────────────────

    def shutdown(self, *, wait_for_worker: bool = True) -> None:
        """关闭 ChatWidget 时清理所有资源（B8: panel.closeEvent 只需调用此方法）。

        所有清理操作各自 try/except，确保单个失败不影响后续清理。
        """
        if getattr(self, "_shutdown_complete", False):
            return
        self._shutdown_complete = True
        # 1/ 停止定时器
        try:
            if hasattr(self, '_streaming_timer') and self._streaming_timer is not None:
                self._streaming_timer.stop()
        except Exception:
            logger.debug("shutdown: 停止 streaming_timer 失败", exc_info=True)
        try:
            if hasattr(self, '_scroll_throttle_timer') and self._scroll_throttle_timer is not None:
                self._scroll_throttle_timer.stop()
        except Exception:
            logger.debug("shutdown: 停止 scroll_throttle_timer 失败", exc_info=True)
        # FR14: 停止任务监控轮询定时器
        try:
            if self._task_monitor_timer is not None:
                self._task_monitor_timer.stop()
                self._task_monitor_timer = None
        except Exception:
            logger.debug("shutdown: 停止 task_monitor_timer 失败", exc_info=True)

        # 2/ 结束可观测性活跃追踪（防止 trace 数据丢失）
        try:
            if hasattr(self, '_obs_collector') and self._obs_collector is not None:
                self._obs_collector.end_conversation()
        except Exception:
            logger.debug("shutdown: 结束 obs_collector 会话失败", exc_info=True)

        # 3/ 清理 ObservabilityCollector 回调，解除引用
        try:
            if hasattr(self, '_obs_collector') and self._obs_collector is not None:
                self._obs_collector._on_token_stats_updated = None
        except Exception:
            logger.debug("shutdown: 清理 obs_collector 回调失败", exc_info=True)

        # 4/ 移除 TaskManager 监听器
        try:
            from transbridge.smart_assistant.tools.task_manager import TaskManager
            tm = TaskManager()
            tm.remove_listener(self._on_task_completed)
            tm.remove_listener(self._on_task_failed)
        except Exception:
            logger.debug("shutdown: 移除 TaskManager 监听器失败", exc_info=True)

        # 5/ 取消并等待 worker 线程
        try:
            if hasattr(self, '_worker') and self._worker is not None and self._worker.is_alive():
                self._worker.cancel()
                if wait_for_worker:
                    self._worker.join(timeout=3)
        except Exception:
            logger.debug("shutdown: 取消 worker 线程失败", exc_info=True)

        # 6/ 取消并关闭执行引擎
        try:
            if hasattr(self, '_engine') and self._engine is not None:
                self._engine.cancel()
                self._engine.shutdown()
        except Exception:
            logger.debug("shutdown: 关闭执行引擎失败", exc_info=True)

        # 7/ 关闭长期记忆存储（停止 writer 线程并最终刷盘）
        try:
            if hasattr(self, '_memory_store') and self._memory_store is not None:
                self._memory_store.close()
        except Exception:
            logger.debug("shutdown: 关闭 memory_store 失败", exc_info=True)

        # 8/ 清理 orchestrator 的 Qt 子对象
        try:
            if hasattr(self, '_orchestrator') and self._orchestrator is not None:
                self._orchestrator.shutdown()
        except Exception:
            logger.debug("shutdown: 关闭 orchestrator 失败", exc_info=True)

        # 9/ 重置 TaskManager 单例，防止会话间泄漏
        try:
            from transbridge.smart_assistant.tools.task_manager import TaskManager
            TaskManager.reset()
        except Exception:
            logger.debug("shutdown: 重置 TaskManager 失败", exc_info=True)

    def set_input(self, text: str) -> None:
        self._input.setPlainText(text)
        self._input.setFocus()

    def add_system_prompt(self, text: str) -> None:
        """注入 System Prompt 到对话历史（供 SkillExecutor 等外部调用者使用）。

        将文本作为 system 角色消息插入 conversation，替换已有 system 消息。
        注意：此方法仅操作数据层 (_conversation)，不产生 UI 渲染。
        """
        self._conversation.add_system(text)

    def send_user_message(self, text: str) -> None:
        """以编程方式发送用户消息并触发 LLM 推理（供 SkillExecutor 等外部调用者使用）。

        封装了完整发送流程：添加用户气泡 → 写入对话历史 → 触发 LLM 轮次。
        与 _on_send() 不同，此方法直接接受文本参数，不依赖 _input 控件状态。
        """
        text = text.strip()
        if not text:
            return
        self._orchestrator.cancel_current_round()
        self.add_user_bubble(text)
        self._conversation.add_user(text)
        # m22: LLM 推理在后台 QThread 中异步执行，本方法立即返回
        QTimer.singleShot(0, lambda: self._do_send_retrieve_and_run(text))

    def add_user_bubble(self, text: str) -> None:
        self._add_bubble(MessageBubble(text, "user"))

    def add_assistant_bubble(self, text: str) -> None:
        self._add_bubble(MessageBubble(text, "assistant"))

    def add_system_message(self, text: str) -> None:
        """FR7.16: 融入式系统消息 — 轻量横条标签替代居中灰色文本。"""
        is_ok = text.startswith("[OK]")
        is_fail = text.startswith("[FAIL]")
        if is_ok:
            color, bg = "#388E3C", "#E8F5E9"
        elif is_fail:
            color, bg = "#D32F2F", "#FFEBEE"
        else:
            color, bg = "#757575", "#F5F5F5"
        frame = QFrame()
        frame.setStyleSheet(
            f"QFrame {{"
            f"  border-left: 3px solid {color};"
            f"  background-color: {bg};"
            f"  border-radius: 4px; padding: 4px 10px;"
            f"  margin: 2px 0;"
            f"}}"
        )
        fl = QHBoxLayout(frame)
        fl.setContentsMargins(8, 4, 8, 4)
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color: #333; font-size: 11px; border: none; background: transparent;")
        lbl.setWordWrap(True)
        fl.addWidget(lbl)
        self._add_widget(frame)

    def add_tool_card(self, step: dict) -> ToolCard:
        card = ToolCard(step)
        # M34: 断开后再连接，防止重复连接导致信号重复触发
        try:
            card.executed.disconnect(self._on_tool_executed)
        except TypeError:
            pass
        try:
            card.ignored.disconnect(self._on_tool_ignored)
        except TypeError:
            pass
        card.executed.connect(self._on_tool_executed)
        card.ignored.connect(self._on_tool_ignored)
        self._add_widget(card)
        return card

    def add_plan_card(self, steps: list) -> PlanCard:
        if not hasattr(self, '_msg_layout') or self._msg_layout is None:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda: self.add_plan_card(steps))
            return
        card = PlanCard(steps)
        # M34: 断开后再连接，防止重复连接导致信号重复触发
        try:
            card.confirmed.disconnect(self._on_plan_confirmed)
        except TypeError:
            pass
        try:
            card.cancelled.disconnect(self._on_plan_cancelled)
        except TypeError:
            pass
        card.confirmed.connect(self._on_plan_confirmed)
        card.cancelled.connect(self._on_plan_cancelled)
        self._add_widget(card)
        return card

    def add_batch_tool_card(self, steps: list) -> BatchToolCard:
        if not hasattr(self, '_msg_layout') or self._msg_layout is None:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda: self.add_batch_tool_card(steps))
            return
        card = BatchToolCard(steps)
        # M34: 断开后再连接，防止重复连接导致信号重复触发
        try:
            card.all_executed.disconnect(self._on_batch_executed)
        except TypeError:
            pass
        try:
            card.all_ignored.disconnect(self._on_batch_ignored)
        except TypeError:
            pass
        card.all_executed.connect(self._on_batch_executed)
        card.all_ignored.connect(self._on_batch_ignored)
        self._add_widget(card)
        return card

    # ── 流式刷新辅助（回调，操作 MessageBubble 内部 Widget）──

    def _do_streaming_flush(self, text: str, bubble: MessageBubble) -> None:
        """流式文本刷新：直接更新 MessageBubble 内部 QLabel 纯文本。"""
        wrapper = bubble._content_wrapper
        if wrapper is not None:
            wrapper_layout = wrapper.layout()
            if wrapper_layout is not None:
                if bubble._content is not None and not isinstance(bubble._content, QLabel):
                    wrapper_layout.removeWidget(bubble._content)
                    bubble._content.deleteLater()
                    bubble._content = None
                if bubble._content is None:
                    bubble._content = QLabel(text)
                    bubble._content.setWordWrap(True)
                    bubble._content.setTextFormat(Qt.TextFormat.PlainText)
                    wrapper_layout.addWidget(bubble._content)
                else:
                    bubble._content.setText(text)

    def _remove_widget_safely(self, widget) -> None:
        """安全移除 widget：从布局移除并标记删除。"""
        idx = self._msg_layout.indexOf(widget)
        if idx >= 0:
            self._msg_layout.removeWidget(widget)
            widget.deleteLater()

    def _offer_retry_button(self) -> None:
        """创建重试按钮并添加到消息区。"""
        if self._retry_btn is not None:
            self._remove_widget_safely(self._retry_btn)
            self._retry_btn = None
        retry_btn = QPushButton("重试")
        retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        retry_btn.clicked.connect(self._on_retry)
        self._add_widget(retry_btn)
        self._retry_btn = retry_btn

    def _log_conversation_memory(self, messages: list, response: str) -> None:
        """记录本轮对话到长期记忆。"""
        try:
            from transbridge.smart_assistant.memory import MemoryEntry
            user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
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

    # ── 计划模式 ─────────────────────────────────────────────

    def _on_plan_confirmed(self, steps: list) -> None:
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        # FR12 Story 01: 新旧并行 — 通知 SessionController
        self._controller.handle_user_confirmed(steps, "plan")

        # M61: 工具/计划开始执行时隐藏思考指示器
        self._hide_thinking_indicator()

        # B4: 关闭上一个引擎的线程池，防止 ThreadPoolExecutor 泄漏
        if self._engine:
            self._engine.cancel()
            self._engine.shutdown()

        middlewares = self._ensure_middlewares()
        self._engine = ExecutionEngine(ToolRegistry, self._ctx, middlewares=middlewares)
        self._engine.on_all_finished(self._on_plan_all_finished)
        self._engine.on_step_requires_confirmation(self._on_confirm_required)
        # Observability
        self._obs_collector.start_conversation(f"conv_{id(steps)}")
        self._engine.on_step_started(self._obs_collector.on_step_started)
        self._engine.on_step_finished(self._obs_collector.on_step_finished)
        self._engine.on_step_retrying(self._obs_collector.on_step_retrying)
        # CR15: 复用引擎内置的 ThreadPoolExecutor 替代冗余 daemon 线程
        # M1: _executor 现为 GraphExecutor，ThreadPoolExecutor 在其下一层
        self._engine._executor._executor.submit(self._engine.execute, steps)

    def _on_plan_cancelled(self) -> None:
        if self._engine:
            self._engine.cancel()
        self._hide_thinking_indicator()
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
        self._controller.handle_execution_complete(results)

    # ── 确认 (S08) ──────────────────────────────────────────

    def _on_confirm_required(self, node_id: str, prompt: str, choices: list) -> None:
        """线程安全的确认回调：QMessageBox 必须在主线程创建，但本方法可能从
        ThreadPoolExecutor worker 线程被调用（plan 模式执行路径）。
        非主线程时通过 QTimer + threading.Event 桥接到主线程。"""
        from PyQt6.QtCore import QThread, QCoreApplication
        import threading

        app_thread = QCoreApplication.instance().thread()
        if QThread.currentThread() == app_thread:
            self._show_confirm_dialog(node_id, prompt, choices)
        else:
            result_holder: list = []
            done = threading.Event()

            def _on_main():
                try:
                    self._show_confirm_dialog(node_id, prompt, choices)
                    result_holder.append(True)
                finally:
                    done.set()

            QTimer.singleShot(0, _on_main)
            done.wait()
            if not result_holder:
                # 极端情况：主线程未能处理对话框，回退默认值
                if self._engine:
                    self._engine.provide_decision(node_id, choices[0])

    def _show_confirm_dialog(self, node_id: str, prompt: str, choices: list) -> None:
        """在主线程创建 QMessageBox 并注入决策结果。"""
        reply = QMessageBox.question(self, "操作确认", prompt,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        choice = choices[0] if reply == QMessageBox.StandardButton.Yes else (
            choices[1] if len(choices) > 1 else "跳过")
        if self._engine:
            self._engine.provide_decision(node_id, choice)

    # ── 异步任务通知 (B2) ──────────────────────────────────

    def _ensure_task_manager(self) -> None:
        """懒初始化 TaskManager 单例并注册回调（幂等）。

        不在 UI 构建期执行，而是延迟到首次 LLM 轮次时。
        此时 C 栈已完全展开。Phase 1: TaskManager 去 QObject 化后改用回调列表替代 pyqtSignal，
        消除 QObject.__new__ 的 C++ 元对象栈开销。

        ADR-008 (B10): 注入 Qt 主线程调度器，使 TaskManager 后端层保持 PyQt6-free，
        同时保证回调在 GUI 主线程安全执行。
        """
        if self._task_manager_connected:
            return
        self._task_manager_connected = True
        try:
            from PyQt6.QtCore import QCoreApplication
            from transbridge.smart_assistant.tools.task_manager import TaskManager

            # ADR-008/B10: 注入 Qt 队列调度器，桥接后端→主线程回调
            app = QCoreApplication.instance()
            if app is not None:
                TaskManager.set_main_thread_dispatcher(
                    lambda fn: QTimer.singleShot(0, fn))

            tm = TaskManager()
            tm.on_completed(self._on_task_completed)
            tm.on_failed(self._on_task_failed)
            # FR14: 任务完成/失败时立即刷新监控面板
            tm.on_finished(lambda tid, success, msg, data: self._refresh_task_monitor())
            # FR14: 启动定时轮询
            if self._task_monitor is not None:
                self._start_task_monitor_polling()
        except Exception as e:
            logger.error("TaskManager 初始化失败，后台任务通知不可用: %s", e)
            self._task_manager_connected = False

    def _on_task_completed(self, task_id: str, result: dict) -> None:
        """后台翻译/润色任务完成回调。"""
        from transbridge.smart_assistant.tools.task_manager import TaskManager

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
        run_id = str(TaskManager().get_status(task_id).get("run_id", ""))
        self._controller.handle_task_completed(task_id, result, run_id)

    def _on_task_failed(self, task_id: str, error: str) -> None:
        """后台任务失败回调。"""
        from transbridge.smart_assistant.tools.task_manager import TaskManager

        safe_error = self._sanitize_error_message(error)
        msg = f"任务 {task_id} 失败: {safe_error}"
        self._conversation.add_observation("start_translation", msg)
        self.add_system_message(f"[FAIL] {msg}")
        run_id = str(TaskManager().get_status(task_id).get("run_id", ""))
        self._controller.handle_task_completed(task_id, {"error": safe_error}, run_id)

    def _on_token_stats_updated(self, stats) -> None:
        """FR7.16: 观测数据后台采集，内联可见时插入对话流。"""
        if self._obs_inline_visible and hasattr(stats, 'input_tokens'):
            self.add_system_message(
                f"Token: 输入 {stats.input_tokens} / 输出 {stats.output_tokens}"
            )

    # ── 护栏 ────────────────────────────────────────────────

    def _ensure_middlewares(self) -> list:
        """B1: 延迟构建护栏中间件链（委托给 ToolExecutionHandler）。"""
        return self._tool_handler._ensure_middlewares()

    # ── ReAct 模式 ───────────────────────────────────────────

    def _on_tool_executed(self, step: dict) -> None:
        """执行单个工具步骤（Controller 统一驱动）。"""
        self._hide_thinking_indicator()
        self._controller.handle_user_confirmed([step], "react")
        # Controller._execute_react 已通过 tool_handler.execute_step(skip_react_continue=True) 执行，
        # 此处不再重复调用，仅触发 ReAct 继续
        self._controller.handle_execution_complete([])

    def _on_tool_ignored(self, step: dict) -> None:
        tool_name = step.get("tool", "?")
        self.add_system_message(f"已忽略: {tool_name}")
        self._conversation.add_observation(tool_name, "用户选择不执行此操作。")
        self._controller.handle_user_cancelled()

    def _on_batch_executed(self, steps: list) -> None:
        """批量执行确认后的工具步骤（Controller 统一驱动）。

        Controller._execute_react 已逐个调用 execute_step(skip_react_continue=True)，
        此处不再重复执行，仅触发 ReAct 继续。
        """
        self._controller.handle_user_confirmed(steps, "react")
        self._hide_thinking_indicator()
        self._controller.handle_execution_complete([])

    def _on_batch_ignored(self, steps: list) -> None:
        """批量跳过：用户点击跳过按钮，不执行任何步骤。"""
        tool_names = [s.get("tool", "?") for s in steps]
        self.add_system_message("已跳过: " + ", ".join(tool_names))
        for name in tool_names:
            self._conversation.add_observation(name, "用户选择跳过此批量操作。")
        self._controller.handle_user_cancelled()

    def _on_auto_mode_toggled(self, checked: bool) -> None:
        self._auto_mode = checked
        self._orchestrator.auto_mode = checked
        self._controller.auto_mode = checked
        try:
            QSettings("TransBridge", "SmartAssistant").setValue("auto_mode", checked)
        except Exception as e:
            logger.info("QSettings auto_mode 保存失败: %s", e)

    def _on_retry(self) -> None:
        """网络错误后重试（委托给编排器）。"""
        self._orchestrator.retry()

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _sanitize_error_message(msg: str) -> str:
        """M68: 脱敏错误消息，移除文件路径和 API 密钥等敏感信息。"""
        if not msg:
            return msg
        # 移除 Windows 绝对路径 (e.g. D:\path\to\file.py:123)
        msg = re.sub(r'[A-Za-z]:[\\/][^\s,;:"]+', '[path]', msg)
        # 移除 Unix 绝对路径 (e.g. /home/user/project/file.py)
        msg = re.sub(r'/(?:home|Users|usr|tmp|var|etc|opt|root|mnt)/[^\s,;:"]+', '[path]', msg)
        # 移除常见 API 密钥前缀 (OpenAI sk-, Anthropic sk-ant-, 等)
        msg = re.sub(r'\b(sk-[A-Za-z0-9_-]{20,})\b', '[api_key]', msg)
        msg = re.sub(r'\b(sk-ant-[A-Za-z0-9_-]{20,})\b', '[api_key]', msg)
        return msg

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return

        # FR7.16: /obs 命令切换观测信息显示
        if text == "/obs":
            self._input.clear()
            self._toggle_obs_inline()
            return

        # 中断正在进行的流式输出
        self._orchestrator.cancel_current_round()

        self.add_user_bubble(text)
        self._input.clear()
        self._conversation.add_user(text)

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
        self._ensure_task_manager()
        self._controller.handle_abort()
        self._controller.handle_user_message(text)

    def _on_skill(self, skill_name: str) -> None:
        """Skill 按钮触发：加载并执行 Skill。"""
        from transbridge.smart_assistant.skills import SkillRegistry, SkillExecutor
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
        from transbridge.smart_assistant.file_parser import FileParser
        for p in paths:
            fpath = Path(p)
            parser = FileParser.get_parser(fpath)
            if parser is None:
                self.add_system_message(f"不支持的文件格式: {fpath.name}")
                continue
            # Critical C6: 拒绝超大文件，防止内存耗尽崩溃
            file_size = os.path.getsize(str(fpath))
            if file_size > ChatWidget.MAX_UPLOAD_BYTES:
                size_mb = file_size / (1024 * 1024)
                limit_mb = ChatWidget.MAX_UPLOAD_BYTES / (1024 * 1024)
                self.add_system_message(
                    f"文件过大 ({size_mb:.1f} MB)，已超过 {limit_mb:.0f} MB 上限: {fpath.name}"
                )
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
        self._orchestrator.cancel_current_round()
        if self._engine:
            self._engine.cancel()
            self._engine.shutdown()
            self._engine = None
        self._controller.handle_abort()
        self._orchestrator.reset_state()
        self._hide_thinking_indicator()
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
        self._enforce_widget_limit()

    def _add_widget(self, widget: QWidget) -> None:
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, widget)
        self._scroll_to_bottom()
        self._enforce_widget_limit()

    def _enforce_widget_limit(self) -> None:
        """M52: 限制消息区最大控件数，超出时从头部移除最旧控件。

        布局最后一项是 stretch，实际控件数为 count-1。
        超过 MAX_VISIBLE_WIDGETS 时从索引 0 开始逐出最旧控件。
        """
        # 布局最后一项是 stretch，所以实际控件数为 count - 1
        while self._msg_layout.count() - 1 > self.MAX_VISIBLE_WIDGETS:
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _toggle_obs_inline(self) -> None:
        """FR7.16: 切换内联观测信息显示。"""
        self._obs_inline_visible = not self._obs_inline_visible
        if self._obs_inline_visible:
            self.add_system_message("[观测] Token 统计和工具调用记录已开启。输入 /obs 关闭。")
        else:
            self.add_system_message("[观测] 观测信息已关闭。输入 /obs 重新开启。")

    # ── 思考指示器 (FR7.16 / Story-08-5) ─────────────────────

    def _show_thinking_indicator(self, thought: str) -> None:
        """在消息流中插入 ThinkingIndicator。"""
        self._hide_thinking_indicator()
        self._thinking_indicator = ThinkingIndicator()
        self._thinking_indicator.set_thought(thought)
        self._add_widget(self._thinking_indicator)

    def _hide_thinking_indicator(self) -> None:
        """移除当前 ThinkingIndicator。"""
        if self._thinking_indicator:
            self._thinking_indicator.stop_animation()
            idx = self._msg_layout.indexOf(self._thinking_indicator)
            if idx >= 0:
                self._msg_layout.removeWidget(self._thinking_indicator)
                self._thinking_indicator.deleteLater()
            self._thinking_indicator = None

    def _toggle_thought_expand(self) -> None:
        """Ctrl+O: 展开/折叠当前思考内容。"""
        if self._thinking_indicator and self._thinking_indicator.isVisible():
            self._thinking_indicator.toggle_expand()

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
            # M60/M66: 先移动再显示，避免按钮在旧位置闪现
            self._reposition_back_to_bottom_btn()
            self._back_to_bottom_btn.setVisible(True)
        else:
            self._back_to_bottom_btn.setVisible(False)

    def _on_back_to_bottom(self) -> None:
        self._scroll_to_bottom()

    def _reposition_back_to_bottom_btn(self) -> None:
        """定位「回到底部」按钮到滚动区域右下角。"""
        try:
            btn = self._back_to_bottom_btn
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

    # ── 会话持久化 (FR13 Story 03) ──────────────────────────

    def set_session_manager(self, mgr):
        """注入 SessionManager 实例（由 Panel 调用）。"""
        self._session_mgr = mgr

    # ── FR14: 后台任务监控 ────────────────────────────────────

    def set_task_monitor(self, monitor) -> None:
        """注入 TaskMonitorWidget 引用（由 Panel 调用）。"""
        self._task_monitor = monitor
        # 如果 TaskManager 已初始化，立即启动轮询
        if self._task_manager_connected:
            self._start_task_monitor_polling()

    def _start_task_monitor_polling(self) -> None:
        """启动 1s 定时轮询刷新任务监控。"""
        if self._task_monitor_timer is not None:
            return  # 已启动
        self._task_monitor_timer = QTimer(self)
        self._task_monitor_timer.setInterval(1000)
        self._task_monitor_timer.timeout.connect(self._refresh_task_monitor)
        self._task_monitor_timer.start()

    def _refresh_task_monitor(self) -> None:
        """从 TaskManager 拉取最新任务列表并刷新 TaskMonitorWidget。"""
        if self._task_monitor is None:
            return
        try:
            from transbridge.smart_assistant.tools.task_manager import TaskManager
            tm = TaskManager()
            all_ids = tm.list_all()
            tasks = []
            for tid in all_ids:
                status_data = tm.get_status(tid)
                if status_data.get("error"):
                    continue
                tasks.append(status_data)
            self._task_monitor.refresh(tasks)
        except Exception:
            logger.debug("刷新任务监控失败", exc_info=True)

    def save_current_session(self, session_id: str) -> None:
        """保存当前对话到指定会话。"""
        if not hasattr(self, '_session_mgr') or self._session_mgr is None:
            return
        messages = self._conversation.to_dict()["messages"]
        if messages:
            self._session_mgr.save_session(session_id, messages)

    def load_session(self, data: dict) -> None:
        """加载会话数据：清空当前对话并渲染历史消息。"""
        self._controller.handle_abort()
        # 清空 UI
        self._clear_all_bubbles()
        # 从数据恢复
        self._conversation.from_dict({"messages": data.get("messages", [])})
        self.load_history(data.get("messages", []))
        # FR14: 会话切换时重置任务监控
        if self._task_monitor is not None:
            self._task_monitor.reset()

    def load_history(self, messages: list[dict]) -> None:
        """渲染历史消息列表为 MessageBubble。若 UI 尚未就绪则延迟重试。"""
        if not hasattr(self, '_msg_layout') or self._msg_layout is None:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(50, lambda: self.load_history(messages))
            return
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                continue  # system prompt 已通过 conversation.from_dict 恢复
            elif role == "assistant":
                self._add_bubble(MessageBubble(content, "assistant"))
            elif role == "user":
                if content.startswith("【工具执行结果") or content.startswith("【计划执行完成】"):
                    self.add_system_message(content)
                else:
                    self._add_bubble(MessageBubble(content, "user"))

    def _clear_all_bubbles(self) -> None:
        """清空聊天区所有消息气泡。"""
        while self._msg_layout.count() > 1:  # 最后一个是 stretch
            item = self._msg_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _on_response_parsed_safe(self, parsed: dict) -> None:
        """安全包装：确保 Controller 异常不会导致 UI 卡死。"""
        try:
            self._controller.handle_llm_response(parsed)
        except Exception:
            logger.error("SessionController.handle_llm_response 异常", exc_info=True)
            self.add_system_message("内部错误：响应处理失败，请重试")
        try:
            self._auto_save_hook(parsed)
        except Exception:
            logger.error("_auto_save_hook 异常", exc_info=True)

    def _auto_save_hook(self, parsed: dict | None = None) -> None:
        """每轮 LLM 后自动保存当前会话 + 首次对话后 AI 自动命名。"""
        if not hasattr(self, '_session_mgr') or self._session_mgr is None:
            return
        panel = self._find_panel()
        if panel is None:
            return
        sid = panel._active_session_id
        if sid:
            self.save_current_session(sid)
            # 首次对话后：用 AI 的 thought 自动命名（仅当名称仍是"新对话"时）
            if parsed:
                self._auto_name_session(sid, parsed, panel)

    def _auto_name_session(self, sid: str, parsed: dict, panel) -> None:
        """如果会话名仍是默认的'新对话'，用 AI 的 thought 字段自动命名。"""
        data = self._session_mgr.get_session(sid)
        if data is None or data.get("name") != "新对话":
            return
        thought = parsed.get("thought", "")
        if not thought:
            # 无 thought 时用第一条用户消息前20字
            msgs = data.get("messages", [])
            for m in msgs:
                if m.get("role") == "user" and not m.get("content", "").startswith("【"):
                    thought = m["content"]
                    break
        name = thought[:20].strip()
        if name:
            self._session_mgr.rename_session(sid, name)
            panel._refresh_session_list()

    def _find_panel(self):
        """向上查找 SmartAssistantPanel 父组件。"""
        w = self.parent()
        while w is not None:
            from .panel import SmartAssistantPanel
            if isinstance(w, SmartAssistantPanel):
                return w
            w = w.parent()
        return None

    def eventFilter(self, obj, event):
        if obj == self._input and event.type() == QEvent.Type.KeyPress:
            ke = event
            if (ke.key() == Qt.Key.Key_Return and
                    ke.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self._on_send()
                return True
        return super().eventFilter(obj, event)

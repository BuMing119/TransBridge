"""
WorkbenchWidget: 工作台整体布局（左侧集合统计 + 右侧三步骤面板）。
"""

from PyQt6.QtWidgets import QWidget, QSplitter, QVBoxLayout, QScrollArea
from PyQt6.QtCore import Qt

from .stats_panel import CollectionStatsPanel
from .step1 import Step1SourceWidget
from .step2 import Step2PreviewWidget
from .step3 import Step3OpsWidget


class WorkbenchWidget(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._tool_windows: dict = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        # 左侧：集合统计面板
        self._stats = CollectionStatsPanel(self._ctx)
        splitter.addWidget(self._stats)

        # 右侧：步骤 1/2/3 纵向排列
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.setSpacing(6)

        self._step1 = Step1SourceWidget(self._ctx)
        self._step2 = Step2PreviewWidget(self._ctx)
        self._step3 = Step3OpsWidget(self._ctx)

        # Step1 → Step2 进度联动
        self._step1.parse_started.connect(lambda: self._step2.set_parsing(True))
        self._step1.parse_finished.connect(lambda _: self._step2.set_parsing(False))

        right_layout.addWidget(self._step1)
        right_layout.addWidget(self._step2)
        right_layout.addWidget(self._step3)

        scroll = QScrollArea()
        scroll.setWidget(right)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        splitter.addWidget(scroll)
        splitter.setSizes([240, 900])

        layout.addWidget(splitter)

    def open_tool(self, tool_id: str):
        if tool_id == "ai_translator":
            # 若进度窗口存在且 worker 仍在运行，直接显示进度窗口
            progress_win = self._tool_windows.get("ai_translator_progress")
            if progress_win is not None and progress_win.is_running():
                progress_win.show()
                progress_win.raise_()
                progress_win.activateWindow()
                return

            # 显示/创建配置窗口
            config_win = self._tool_windows.get("ai_translator")
            if config_win is None or not config_win.isVisible():
                from src.transbridge.ui.tools.ai_translator_window import AITranslatorWindow
                config_win = AITranslatorWindow(self._ctx, self._step2, parent=self)
                config_win.progress_window_created.connect(self._on_progress_window_created)
                self._tool_windows["ai_translator"] = config_win
            config_win.show()
            config_win.raise_()
            config_win.activateWindow()
            return

        if tool_id in self._tool_windows:
            win = self._tool_windows[tool_id]
            win.show()
            win.raise_()
            win.activateWindow()

    def _on_progress_window_created(self, progress_win):
        self._tool_windows["ai_translator_progress"] = progress_win
        # 配置窗口关闭后清除引用，下次重新创建
        self._tool_windows.pop("ai_translator", None)

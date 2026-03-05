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

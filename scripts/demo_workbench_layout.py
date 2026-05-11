"""
布局 Demo：工作台 + ParaTranz 管理 双模式主窗口

运行方式：
    python scripts/demo_workbench_layout.py
"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QTabBar, QTreeWidget, QTreeWidgetItem,
    QLabel, QPushButton, QLineEdit, QFileDialog, QGroupBox,
    QProgressBar, QTextEdit, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QStackedWidget, QSizePolicy, QComboBox,
    QStatusBar, QMenuBar, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QColor, QIcon


# ─────────────────────────────────────────────
# 通用占位 Widget
# ─────────────────────────────────────────────

def placeholder(text: str, bg: str = "#f0f0f0") -> QLabel:
    w = QLabel(text)
    w.setAlignment(Qt.AlignmentFlag.AlignCenter)
    w.setStyleSheet(f"background:{bg}; color:#888; border:1px dashed #bbb; border-radius:4px;")
    w.setMinimumHeight(40)
    return w


def section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    font = QFont()
    font.setBold(True)
    font.setPointSize(10)
    lbl.setFont(font)
    lbl.setStyleSheet("color: #333; padding: 4px 0;")
    return lbl


def h_line() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: #ddd;")
    return f


# ─────────────────────────────────────────────
# 工作台 - 左侧：集合统计树
# ─────────────────────────────────────────────

class CollectionStatsPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(220)
        self.setMaximumWidth(300)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(section_title("当前集合"))

        # 总览标签
        self.summary = QLabel("未加载")
        self.summary.setStyleSheet(
            "background:#e8f4fd; border-radius:4px; padding:6px; color:#1a6aa8;"
        )
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        layout.addWidget(h_line())
        layout.addWidget(section_title("按类型统计"))

        # 统计树
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["分类", "词条数", "已译"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setRootIsDecorated(True)
        layout.addWidget(self.tree)

        self._populate_demo()

    def _populate_demo(self):
        self.summary.setText("547 条词条\n已翻译：312 (57%)\n来源：AuriBoss.esp")

        data = [
            ("对话", 210, 98, [("INFO", 180, 80), ("DIAL", 30, 18)]),
            ("人名", 87, 87, [("NPC_:FULL", 75, 75), ("NPC_:SHRT", 12, 12)]),
            ("物品", 124, 62, [("WEAP", 40, 20), ("ARMO", 50, 25), ("MISC", 34, 17)]),
            ("书籍", 56, 32, [("BOOK:FULL", 30, 18), ("BOOK:DESC", 26, 14)]),
            ("其他", 70, 33, []),
        ]

        for name, total, done, children in data:
            item = QTreeWidgetItem([name, str(total), str(done)])
            item.setForeground(1, QColor("#555"))
            item.setForeground(2, QColor("#2a7a2a") if done == total else QColor("#e07000"))
            for cname, ct, cd in children:
                child = QTreeWidgetItem([cname, str(ct), str(cd)])
                child.setForeground(2, QColor("#2a7a2a") if cd == ct else QColor("#e07000"))
                item.addChild(child)
            self.tree.addTopLevelItem(item)

        self.tree.expandAll()


# ─────────────────────────────────────────────
# 工作台 - 右侧：步骤面板
# ─────────────────────────────────────────────

class Step1SourcePanel(QGroupBox):
    """步骤1：选择源文件"""
    def __init__(self):
        super().__init__("步骤 1  /  源文件")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        def file_row(label: str, hint: str, required: bool = False) -> QWidget:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            tag = QLabel(f"{'● ' if required else '○ '}{label}")
            tag.setFixedWidth(110)
            tag.setStyleSheet("color:" + ("#333" if required else "#888") + ";")
            edit = QLineEdit()
            edit.setPlaceholderText(hint)
            edit.setReadOnly(True)
            btn = QPushButton("选择…")
            btn.setFixedWidth(56)
            btn.setStyleSheet("font-size:11px;")
            rl.addWidget(tag)
            rl.addWidget(edit)
            rl.addWidget(btn)
            return row

        layout.addWidget(file_row("插件文件 *", "选择 .esp / .esm 文件", required=True))
        layout.addWidget(file_row("EET XML", "（可选）用于迁移旧译文"))
        layout.addWidget(file_row("XT XML", "（可选）用于迁移旧译文"))

        opt_row = QWidget()
        ol = QHBoxLayout(opt_row)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(QLabel("跳过空串："))
        skip_combo = QComboBox()
        skip_combo.addItems(["是（推荐）", "否"])
        ol.addWidget(skip_combo)
        ol.addStretch()
        layout.addWidget(opt_row)

        parse_btn = QPushButton("▶  解析插件")
        parse_btn.setFixedHeight(34)
        parse_btn.setStyleSheet(
            "background:#1976d2; color:white; border-radius:4px; font-weight:bold;"
        )
        layout.addWidget(parse_btn)


class Step2ResultPanel(QGroupBox):
    """步骤2：解析结果预览"""
    def __init__(self):
        super().__init__("步骤 2  /  解析结果")
        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        # 进度条
        prog = QProgressBar()
        prog.setValue(72)
        prog.setFormat("已解析 %p%")
        layout.addWidget(prog)

        # 统计行
        stats_row = QWidget()
        sl = QHBoxLayout(stats_row)
        sl.setContentsMargins(0, 0, 0, 0)
        for label, val, color in [
            ("总词条", "547", "#333"),
            ("已有译文", "312", "#2a7a2a"),
            ("EET 迁移", "48", "#1976d2"),
            ("未翻译", "235", "#c0392b"),
        ]:
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(8, 4, 8, 4)
            bl.setSpacing(0)
            num = QLabel(val)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet(f"font-size:18px; font-weight:bold; color:{color};")
            lbl = QLabel(label)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("font-size:10px; color:#888;")
            bl.addWidget(num)
            bl.addWidget(lbl)
            box.setStyleSheet("background:#f8f8f8; border-radius:4px;")
            sl.addWidget(box)
        layout.addWidget(stats_row)

        # 词条预览表格
        table = QTableWidget(5, 4)
        table.setHorizontalHeaderLabels(["Key", "原文", "译文", "类型"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)

        sample = [
            ("Auri:00012345|1~NPC_:FULL", "Auri",         "奥里",  "NPC_:FULL"),
            ("Auri:00012345|2~NPC_:SHRT", "Auri",         "",      "NPC_:SHRT"),
            ("Boss:00067890|1~INFO:NAM1", "I will find…", "我会…", "INFO:NAM1"),
            ("None:000ABCD|1~BOOK:FULL",  "The Old Ways", "",      "BOOK:FULL"),
            ("Sword:000EF01|1~WEAP:FULL", "Auri's Bow",   "奥里之弓","WEAP:FULL"),
        ]
        for r, (key, orig, trans, typ) in enumerate(sample):
            table.setItem(r, 0, QTableWidgetItem(key))
            table.setItem(r, 1, QTableWidgetItem(orig))
            item = QTableWidgetItem(trans)
            item.setForeground(QColor("#2a7a2a") if trans else QColor("#bbb"))
            table.setItem(r, 2, item)
            table.setItem(r, 3, QTableWidgetItem(typ))
        table.setMaximumHeight(160)
        layout.addWidget(table)


class Step3OpsPanel(QGroupBox):
    """步骤3：导出 / 写回 / 对接 ParaTranz"""
    def __init__(self):
        super().__init__("步骤 3  /  操作")
        layout = QHBoxLayout(self)
        layout.setSpacing(12)

        def op_card(title: str, desc: str, btn_text: str, color: str) -> QWidget:
            card = QGroupBox(title)
            cl = QVBoxLayout(card)
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#666; font-size:11px;")
            btn = QPushButton(btn_text)
            btn.setFixedHeight(30)
            btn.setStyleSheet(
                f"background:{color}; color:white; border-radius:4px; font-weight:bold;"
            )
            cl.addWidget(lbl)
            cl.addStretch()
            cl.addWidget(btn)
            return card

        layout.addWidget(op_card(
            "导出分类 JSON",
            "按词条类型拆分为多个 JSON 文件，保存到本地目录。",
            "📁  导出…",
            "#607d8b",
        ))
        layout.addWidget(op_card(
            "上传到 ParaTranz",
            "将分类 JSON 上传至当前选中的 ParaTranz 项目，新建或更新文件。",
            "☁  上传…",
            "#1976d2",
        ))
        layout.addWidget(op_card(
            "从 ParaTranz 下载",
            "下载 ParaTranz 已翻译词条，按 key 合并到本地集合。",
            "⬇  下载合并…",
            "#388e3c",
        ))
        layout.addWidget(op_card(
            "写回 ESP 插件",
            "将本地集合中的译文写回源插件文件，生成汉化版 .esp。",
            "💾  写回 ESP…",
            "#f57c00",
        ))


class WorkbenchPanel(QWidget):
    """工作台整体面板（左：统计树，右：步骤区）"""
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧集合统计
        splitter.addWidget(CollectionStatsPanel())

        # 右侧步骤区
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)
        rl.setSpacing(8)
        rl.addWidget(Step1SourcePanel())
        rl.addWidget(Step2ResultPanel())
        rl.addWidget(Step3OpsPanel())
        splitter.addWidget(right)

        splitter.setSizes([240, 900])
        layout.addWidget(splitter)


# ─────────────────────────────────────────────
# ParaTranz 管理面板（原有结构占位）
# ─────────────────────────────────────────────

class ParatranzPanel(QWidget):
    """ParaTranz 管理面板（保持原有左右结构）"""
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：项目列表
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 8, 8, 8)
        ll.setSpacing(6)
        left.setMinimumWidth(200)
        left.setMaximumWidth(280)

        ll.addWidget(section_title("项目列表"))

        search = QLineEdit()
        search.setPlaceholderText("搜索项目…")
        ll.addWidget(search)

        project_list = QListWidget()
        for name in ["AuriBoss 汉化", "Vigilant 汉化", "Unslaad 汉化", "测试项目"]:
            item = QListWidgetItem(name)
            project_list.addItem(item)
        project_list.setCurrentRow(0)
        ll.addWidget(project_list)

        new_btn = QPushButton("+ 新建项目")
        new_btn.setStyleSheet("color:#1976d2;")
        ll.addWidget(new_btn)

        splitter.addWidget(left)

        # 右侧：标签页
        tabs = QTabWidget()
        for tab_name in ["概览", "文件管理", "词条管理", "术语管理",
                         "成员管理", "历史记录", "贡献统计", "导出管理"]:
            tabs.addTab(placeholder(f"{tab_name}（待实现）", "#fafafa"), tab_name)

        splitter.addWidget(tabs)
        splitter.setSizes([240, 900])
        layout.addWidget(splitter)


# ─────────────────────────────────────────────
# 主窗口
# ─────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TransBridge")
        self.resize(1280, 820)

        # 菜单栏
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        file_menu.addAction("刷新项目列表")
        file_menu.addSeparator()
        file_menu.addAction("设置 / API 配置")
        file_menu.addSeparator()
        file_menu.addAction("退出").triggered.connect(self.close)
        account_menu = menubar.addMenu("账户")
        account_menu.addAction("我的信息")
        account_menu.addAction("私信")
        menubar.addMenu("帮助")

        # 中央：顶层模式切换
        central = QWidget()
        self.setCentralWidget(central)
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # 顶层标签（工作台 / ParaTranz 管理）
        self.mode_tabs = QTabWidget()
        self.mode_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.mode_tabs.setStyleSheet("""
            QTabBar::tab {
                padding: 8px 24px;
                font-size: 13px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                color: #1976d2;
                border-bottom: 2px solid #1976d2;
            }
        """)

        self.workbench = WorkbenchPanel()
        self.paratranz  = ParatranzPanel()

        self.mode_tabs.addTab(self.workbench, "🔧  工作台")
        self.mode_tabs.addTab(self.paratranz,  "☁  ParaTranz 管理")

        cl.addWidget(self.mode_tabs)

        # 状态栏
        sb = QStatusBar()
        sb.showMessage("就绪  |  未连接  |  未加载集合")
        self.setStatusBar(sb)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

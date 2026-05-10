# Story 11: 应用内报告对话框

**所属方案**: `plans/ai-post-process/plan.md`
**技术模块**: UI (PyQt6)
**状态**: ✔️ 已实现
**创建日期**: 2026-05-09

## 前置依赖

### 上游 Story
- Story-10（报告生成后端）：已完成 → 提供 `ReportGenerator` 类和 `report_path`

### 跨 Plan 依赖
- `ui-workbench/plan.md` → `Step2PreviewWidget` — 提供条目定位方法，供双击跳转使用
- `core-data-model/plan.md` → `TranslationEntry` — 数据结构

### 引用的架构决策
- ADR-004: QThread + 信号总线异步模式 — 报告对话框为非模态，不阻塞主线程
- ADR-001: TranslationEntry 统一数据模型

## 验收标准

（从 plan 原样复制）

- [ ] 翻译报告：3 个 Tab（汇总 / 条目详情 / 问题明细）
- [ ] 润色报告：2 个 Tab（汇总 / 条目详情），无问题 Tab
- [ ] 汇总 Tab：统计卡片布局（总数/成功/失败/跳过、后处理检测/修复/润色/裁决计数）
- [ ] 条目 Tab：QTableWidget 支持按裁决结果筛选、按信心度排序
- [ ] 双击条目行 → 发射 `entry_activated(str entry_id)` 信号
- [ ] 底部按钮栏：「打开 Excel」（report_path 非空时启用）、「关闭」
- [ ] 翻译完全失败时仍可正常显示（汇总全 0）

## 数据流

```
调用方（翻译或润色完成处理）
    │
    ├── 翻译模式: TranslationResult + refine_results + polish_results + decisions + report_path
    │      │
    │      ├──→ _build_summary_tab()
    │      │      └── 第一行卡片: 总条目/成功/失败/跳过/新增术语
    │      │      └── 第二行卡片: 检查数/错误/警告/信息/需审核
    │      │      └── 第三行卡片: 通过/打回/待审/修复数/润色数
    │      │
    │      ├──→ _build_entries_tab()
    │      │      └── 列: 原文/原译文/最终译文/裁决结果/信心度/问题数
    │      │      └── 筛选: QComboBox (全部/通过/打回/待审)
    │      │      └── 点击信心度列头 → 排序
    │      │
    │      └──→ _build_issues_tab()
    │             └── 列: 条目ID/问题类型/严重度/描述/建议
    │             └── 筛选: QComboBox (全部/错误/警告/信息)
    │
    └── 润色模式: entries + polish_results_dict + polish_stats + report_path
           │
           ├──→ _build_summary_tab()
           │      └── 卡片: 润色总数/接受/拒绝/失败/信心度均值
           │
           └──→ _build_entries_tab()
                  └── 列: 原文/原译文/润色结果/接受?/信心度/变更摘要
                  └── 筛选: QComboBox (全部/已接受/已拒绝)
    │
    ▼
用户交互
    ├── QComboBox 筛选 → 隐藏不匹配行
    ├── 点击列头 → 按信心度排序
    ├── 双击条目行 → entry_activated.emit(entry_id) → MainWindow 定位
    └── 点击「打开 Excel」→ os.startfile(report_path)
```

## 关键接口

### 类定义

```python
# _translation_report_dialog.py (新)

class _TranslationReportDialog(QDialog):
    """AI翻译/润色结果报告对话框。"""

    entry_activated = pyqtSignal(str)  # entry_id，用于跳转Step2

    def __init__(
        self,
        # ── 翻译模式参数 ──
        translate_result: "TranslationResult | None" = None,
        refine_results: dict[str, "RefineResult"] | None = None,
        polish_results: dict[str, "PolishResult"] | None = None,
        decisions: dict[str, "ArbiterDecision"] | None = None,
        # ── 润色模式参数 ──
        polish_entries: list["TranslationEntry"] | None = None,
        polish_results_dict: dict[str, "PolishResult"] | None = None,
        polish_stats: dict | None = None,
        # ── 通用 ──
        report_path: str | None = None,
        parent=None,
    ):
        """自动根据传入参数判断模式。"""
        ...

    # ── 模式判断 ──
    def _is_translate_mode(self) -> bool: ...
    def _is_polish_mode(self) -> bool: ...

    # ── Tab 构建 ──
    def _build_summary_tab(self) -> QWidget: ...
    def _build_entries_tab(self) -> QWidget: ...
    def _build_issues_tab(self) -> QWidget: ...  # 仅翻译模式

    # ── 辅助 ──
    def _make_stat_card(self, label: str, value: str, color: str = "#333") -> QWidget:
        """创建单个统计卡片：大数字 + 小标签。"""
        ...

    def _build_entry_row_translate(self, entry_id: str) -> list[QTableWidgetItem]: ...
    def _build_entry_row_polish(self, entry_id: str) -> list[QTableWidgetItem]: ...

    # ── 交互 ──
    def _apply_entry_filter(self, filter_value: str) -> None: ...
    def _on_entry_double_clicked(self, item: QTableWidgetItem) -> None:
        """双击行时获取该行的 entry_id，发射 entry_activated 信号。"""
        ...
    def _on_open_excel(self) -> None:
        """使用系统默认程序打开 Excel 报告文件。"""
        ...
```

### 关键数据结构

```python
# polish_stats 结构（从调用方传入）
polish_stats = {
    "total": int,          # 润色条目总数
    "accepted": int,       # 用户接受数
    "rejected": int,       # 用户拒绝数
    "failed": int,         # 润色失败数
    "polish_level": str,   # "light" / "moderate" / "aggressive"
    "avg_confidence": float,  # 平均信心度
}
```

### MainWindow 新增方法

```python
# main_window.py (修改)

def locate_entry(self, entry_id: str) -> None:
    """切换到Step2并在表格中定位到指定条目。"""
    # 1. 确保 Step2 可见且为当前面板
    # 2. 在 _ctx.collection 中查找 entry_id
    # 3. 清除现有筛选，或临时添加 entry_id 搜索
    # 4. 在 Step2 表格中定位到对应行
    # 5. 选中并高亮该行
    # 6. 若条目不存在 → 状态栏提示 "条目不存在或已被删除"
    ...
```

## 实现步骤

### 步骤 1: 创建报告对话框骨架

**涉及文件**: `src/transbridge/ui/tools/ai_translator/_translation_report_dialog.py`（新建）

**实现要点**:
- 创建 `_TranslationReportDialog(QDialog)` 类
- 构造函数中判断模式：若 `translate_result is not None` → 翻译模式；若 `polish_stats is not None` → 润色模式
- 创建 `QTabWidget` 作为主布局
- 窗口标题：「翻译报告」/「润色报告」
- 窗口大小 `850x600`，非模态

**边界条件**:
- 两种模式的数据都不传 → 不成立，调用方负责保证
- 同时传两种数据 → 以翻译模式优先

**伪代码**:
```python
class _TranslationReportDialog(QDialog):
    entry_activated = pyqtSignal(str)

    def __init__(self, translate_result=None, refine_results=None,
                 polish_results=None, decisions=None,
                 polish_entries=None, polish_results_dict=None,
                 polish_stats=None, report_path=None, parent=None):
        super().__init__(parent)
        self._translate_result = translate_result
        self._refine_results = refine_results or {}
        self._polish_results = polish_results or {}
        self._decisions = decisions or {}
        self._polish_entries = polish_entries or []
        self._polish_results_dict = polish_results_dict or {}
        self._polish_stats = polish_stats or {}
        self._report_path = report_path

        self._translate_mode = translate_result is not None
        title = "翻译报告" if self._translate_mode else "润色报告"
        self.setWindowTitle(title)
        self.resize(850, 600)

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_summary_tab(), "汇总")
        self._tabs.addTab(self._build_entries_tab(), "条目详情")
        if self._translate_mode:
            self._tabs.addTab(self._build_issues_tab(), "问题明细")

        # 底部按钮栏
        self._init_bottom_bar(layout)
```

**测试策略**: 分别以翻译模式和润色模式创建对话框，验证标题、Tab 数量（翻译3/润色2）正确。

### 步骤 2: 实现汇总 Tab

**涉及文件**: `src/transbridge/ui/tools/ai_translator/_translation_report_dialog.py`

**实现要点**:
- 使用 `QGridLayout` 排列统计卡片
- 每张卡片：`_make_stat_card(label, value, color)` → QFrame 内含大号 QLabel(value) + 小号 QLabel(label)
- 翻译模式卡片布局（3行×5列）：
  - 行1：总条目 / 成功 / 失败 / 跳过 / 新增术语
  - 行2：检查数 / 错误 / 警告 / 信息 / 需审核
  - 行3：通过 / 打回 / 待审 / 修复数 / 润色数
- 润色模式卡片布局（1行×5列）：
  - 润色总数 / 接受 / 拒绝 / 失败 / 信心度均值
- 颜色：成功=绿色(#4CAF50)、失败=红色(#F44336)、跳过=灰色(#9E9E9E)、警告=橙色(#FF9800)

**边界条件**:
- 后处理未启用 → 翻译模式第二行/第三行卡片隐藏或显示 N/A
- 全部失败 → 成功=0（绿色显示0）、失败=N（红色）

**伪代码**:
```python
def _make_stat_card(self, label, value, color="#333"):
    card = QFrame()
    card.setFrameShape(QFrame.Shape.StyledPanel)
    card.setStyleSheet(f"QFrame {{ background: white; border-radius: 4px; padding: 8px; }}")
    lay = QVBoxLayout(card)
    val_lbl = QLabel(str(value))
    val_lbl.setStyleSheet(f"font-size: 22px; font-weight: bold; color: {color};")
    val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    desc_lbl = QLabel(label)
    desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    desc_lbl.setStyleSheet("color: #666; font-size: 11px;")
    lay.addWidget(val_lbl)
    lay.addWidget(desc_lbl)
    return card

def _build_summary_tab(self):
    widget = QWidget()
    layout = QVBoxLayout(widget)

    if self._translate_mode:
        r = self._translate_result
        pp = r.post_process_result

        cards1 = [
            ("总条目", r.success_count + r.failed_count + r.skipped_count, "#333"),
            ("成功", r.success_count, "#4CAF50"),
            ("失败", r.failed_count, "#F44336"),
            ("跳过", r.skipped_count, "#9E9E9E"),
            ("新增术语", r.new_dynamic_terms, "#2196F3"),
        ]
        grid = QGridLayout()
        for i, (label, val, color) in enumerate(cards1):
            grid.addWidget(self._make_stat_card(label, val, color), 0, i)

        if pp:
            # 后处理卡片
            err = sum(1 for x in pp.issues if x.severity == "error")
            wrn = sum(1 for x in pp.issues if x.severity == "warning")
            inf = sum(1 for x in pp.issues if x.severity == "info")
            passed = sum(1 for d in self._decisions.values() if d.verdict == "pass")
            rejected = sum(1 for d in self._decisions.values() if d.verdict == "reject")
            pending = sum(1 for d in self._decisions.values() if d.verdict == "pending")
            cards2 = [
                ("检查数", pp.total_checked, "#333"),
                ("错误", err, "#F44336"),
                ("警告", wrn, "#FF9800"),
                ("信息", inf, "#9E9E9E"),
                ("需审核", len(pp.needs_review), "#FF9800"),
            ]
            for i, (label, val, color) in enumerate(cards2):
                grid.addWidget(self._make_stat_card(label, val, color), 1, i)
            cards3 = [
                ("通过", passed, "#4CAF50"),
                ("打回", rejected, "#F44336"),
                ("待审", pending, "#FF9800"),
                ("修复数", pp.auto_fixed, "#2196F3"),
                ("润色数", len(self._polish_results), "#9C27B0"),
            ]
            for i, (label, val, color) in enumerate(cards3):
                grid.addWidget(self._make_stat_card(label, val, color), 2, i)

        layout.addLayout(grid)
    else:
        # 润色模式
        s = self._polish_stats
        cards = [
            ("润色总数", s.get("total", 0), "#333"),
            ("接受", s.get("accepted", 0), "#4CAF50"),
            ("拒绝", s.get("rejected", 0), "#F44336"),
            ("失败", s.get("failed", 0), "#9E9E9E"),
            ("信心度均值", f"{s.get('avg_confidence', 0):.1%}", "#2196F3"),
        ]
        grid = QGridLayout()
        for i, (label, val, color) in enumerate(cards):
            grid.addWidget(self._make_stat_card(label, val, color), 0, i)
        layout.addLayout(grid)

    layout.addStretch()
    return widget
```

**测试策略**: 构造带后处理数据的 TranslationResult，验证卡片数值和颜色正确。构造全0结果，验证卡片数值均为0。

### 步骤 3: 实现条目详情 Tab

**涉及文件**: `src/transbridge/ui/tools/ai_translator/_translation_report_dialog.py`

**实现要点**:
- QTableWidget，列定义因模式不同
- 翻译模式列：原文 / 原译文 / 最终译文 / 裁决结果 / 信心度 / 问题数
- 润色模式列：原文 / 原译文 / 润色结果 / 接受? / 信心度 / 变更摘要
- 顶部筛选栏 (QHBoxLayout)：
  - 翻译模式：QLabel("裁决结果:") + QComboBox(["全部", "通过", "打回", "待审"])
  - 润色模式：QLabel("状态:") + QComboBox(["全部", "已接受", "已拒绝"])
- QComboBox.currentTextChanged → `_apply_entry_filter()`
- 信心度列设置 `setData(Qt.ItemDataRole.UserRole, float_value)` 支持排序
- 启用 `setSortingEnabled(True)`
- 列宽模式：原文/译文 Stretch，裁决/信心度 ResizeToContents

**边界条件**:
- 数据量 > 1000 → 正常渲染，QTableWidget 原生支持
- 裁决结果为空字符串 → 显示"-"
- 信心度为 0.0 → 显示"0%"

**伪代码**:
```python
def _build_entries_tab(self):
    widget = QWidget()
    layout = QVBoxLayout(widget)

    # 筛选栏
    filter_bar = QHBoxLayout()
    if self._translate_mode:
        filter_bar.addWidget(QLabel("裁决结果:"))
        self._entry_filter = QComboBox()
        self._entry_filter.addItems(["全部", "通过", "打回", "待审"])
    else:
        filter_bar.addWidget(QLabel("状态:"))
        self._entry_filter = QComboBox()
        self._entry_filter.addItems(["全部", "已接受", "已拒绝"])
    self._entry_filter.currentTextChanged.connect(self._apply_entry_filter)
    filter_bar.addWidget(self._entry_filter)
    filter_bar.addStretch()
    layout.addLayout(filter_bar)

    # 表格
    if self._translate_mode:
        headers = ["原文", "原译文", "最终译文", "裁决结果", "信心度", "问题数"]
    else:
        headers = ["原文", "原译文", "润色结果", "接受?", "信心度", "变更摘要"]

    self._entry_table = QTableWidget()
    self._entry_table.setColumnCount(len(headers))
    self._entry_table.setHorizontalHeaderLabels(headers)
    self._entry_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    self._entry_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    self._entry_table.setSortingEnabled(True)
    self._entry_table.cellDoubleClicked.connect(self._on_entry_double_clicked)
    # 保存 entry_id 的映射: row → entry_id
    self._entry_row_ids: dict[int, str] = {}

    # 填充数据
    self._populate_entries()
    layout.addWidget(self._entry_table)
    return widget

def _populate_entries(self):
    """填充条目表数据。"""
    self._entry_table.setRowCount(0)
    self._entry_row_ids.clear()

    if self._translate_mode:
        # 数据来源: decisions + refine_results + polish_results
        for i, (entry_id, decision) in enumerate(self._decisions.items()):
            self._entry_table.insertRow(i)
            self._entry_row_ids[i] = entry_id
            refined = self._refine_results.get(entry_id)
            polished = self._polish_results.get(entry_id)
            # 计算最终译文
            final = entry.translation  # 需要从 collection 查找
            # ... 填充单元格
            # 信心度列存入 UserRole float 值以支持排序
            conf_item = QTableWidgetItem(f"{decision.confidence:.0%}")
            conf_item.setData(Qt.ItemDataRole.UserRole, decision.confidence)
            self._entry_table.setItem(i, 4, conf_item)
    else:
        for i, entry in enumerate(self._polish_entries):
            self._entry_table.insertRow(i)
            self._entry_row_ids[i] = entry.id
            pr = self._polish_results_dict.get(entry.id)
            # ... 填充单元格
```

**测试策略**: 构造10条条目数据，验证表格行数、筛选后行数、信心度排序正确。

### 步骤 4: 实现问题明细 Tab（仅翻译模式）

**涉及文件**: `src/transbridge/ui/tools/ai_translator/_translation_report_dialog.py`

**实现要点**:
- 仅翻译模式构建（构造函数中根据模式决定是否 addTab）
- QTableWidget 列：条目ID / 问题类型 / 严重度 / 描述 / 建议
- 严重度列颜色：error=红色(#F44336)、warning=橙色(#FF9800)、info=灰色(#9E9E9E)
- 顶部筛选：QLabel("严重度:") + QComboBox(["全部", "错误", "警告", "信息"])
- 数据来源：`translate_result.post_process_result.issues`

**边界条件**:
- issues 列表为空 → 表格空，显示"无问题"占位文字
- 后处理未启用 → issues 列表为空

**伪代码**:
```python
def _build_issues_tab(self):
    widget = QWidget()
    layout = QVBoxLayout(widget)

    # 筛选栏
    filter_bar = QHBoxLayout()
    filter_bar.addWidget(QLabel("严重度:"))
    self._issue_filter = QComboBox()
    self._issue_filter.addItems(["全部", "错误", "警告", "信息"])
    self._issue_filter.currentTextChanged.connect(self._apply_issue_filter)
    filter_bar.addWidget(self._issue_filter)
    filter_bar.addStretch()
    layout.addLayout(filter_bar)

    # 表格
    headers = ["条目ID", "问题类型", "严重度", "描述", "建议"]
    self._issue_table = QTableWidget()
    self._issue_table.setColumnCount(5)
    self._issue_table.setHorizontalHeaderLabels(headers)
    self._issue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    self._issue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

    issues = self._translate_result.post_process_result.issues if self._translate_result.post_process_result else []
    self._issue_table.setRowCount(len(issues))
    severity_colors = {"error": "#F44336", "warning": "#FF9800", "info": "#9E9E9E"}
    for i, issue in enumerate(issues):
        self._issue_table.setItem(i, 0, QTableWidgetItem(issue.entry_id))
        self._issue_table.setItem(i, 1, QTableWidgetItem(issue.issue_type))
        sev_item = QTableWidgetItem(issue.severity)
        sev_item.setForeground(QColor(severity_colors.get(issue.severity, "#333")))
        self._issue_table.setItem(i, 2, sev_item)
        self._issue_table.setItem(i, 3, QTableWidgetItem(issue.message))
        self._issue_table.setItem(i, 4, QTableWidgetItem(issue.suggestion))

    self._issue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    layout.addWidget(self._issue_table)
    return widget
```

**测试策略**: 构造含 error/warning/info 各1条的 issues 列表，验证颜色和筛选。

### 步骤 5: 实现交互功能

**涉及文件**: `src/transbridge/ui/tools/ai_translator/_translation_report_dialog.py`

**实现要点**:
- `_init_bottom_bar()`：创建 QHBoxLayout，包含「打开 Excel」按钮（左侧）和「关闭」按钮（右侧）
- `_on_entry_double_clicked(item)`：获取行号 → 从 `_entry_row_ids` 获取 entry_id → `self.entry_activated.emit(entry_id)`
- `_on_open_excel()`：`os.startfile(self._report_path)`
- 对话框使用 `self.show()` 而非 `self.exec()`，非模态
- `_apply_entry_filter(filter_value)`：遍历所有行，根据筛选条件 `setRowHidden`
- `_apply_issue_filter(filter_value)`：同上，按严重度过滤

**边界条件**:
- report_path 为 None → 「打开 Excel」按钮 `setEnabled(False)`
- 双击行号超出范围 → 忽略（通过信号槽机制避免）
- Excel 文件已被删除 → `os.startfile` 会弹出系统错误（由OS处理）

**伪代码**:
```python
def _init_bottom_bar(self, parent_layout):
    bar = QHBoxLayout()
    self._btn_excel = QPushButton("打开 Excel")
    self._btn_excel.setEnabled(self._report_path is not None)
    self._btn_excel.clicked.connect(self._on_open_excel)
    bar.addWidget(self._btn_excel)
    bar.addStretch()
    btn_close = QPushButton("关闭")
    btn_close.clicked.connect(self.accept)
    bar.addWidget(btn_close)
    parent_layout.addLayout(bar)

def _on_entry_double_clicked(self, row, col):
    entry_id = self._entry_row_ids.get(row)
    if entry_id:
        self.entry_activated.emit(entry_id)

def _on_open_excel(self):
    if self._report_path and os.path.exists(self._report_path):
        os.startfile(self._report_path)

def _apply_entry_filter(self, filter_value):
    if self._translate_mode:
        verdict_map = {"通过": "pass", "打回": "reject", "待审": "pending"}
        target = verdict_map.get(filter_value)
        for row in range(self._entry_table.rowCount()):
            if target is None:  # "全部"
                self._entry_table.setRowHidden(row, False)
            else:
                item = self._entry_table.item(row, 3)  # 裁决结果列
                self._entry_table.setRowHidden(row, item.text() != filter_value)
    else:
        # 润色模式：按接受状态筛选
        ...
```

**测试策略**: 手动测试：创建对话框 → 切换筛选 → 双击行 → 验证信号发射。验证非模态（对话框显示时仍可操作主窗口）。

### 步骤 6: 条目跳转集成

**涉及文件**: `src/transbridge/ui/main_window.py`（修改）

**实现要点**:
- 在 `MainWindow` 中新增 `_on_report_entry_activated(entry_id: str)` 槽方法
- 切换到 Step2 tab
- 通过 `_ctx.collection` 查找条目
- 调用 `Step2PreviewWidget` 的定位方法（需新增或复用现有 `_locate_and_select`）
- 若条目不存在 → `self.statusBar().showMessage("条目不存在或已被删除", 5000)`

**边界条件**:
- 集合未加载 → 状态栏提示"请先加载翻译集合"
- entry_id 在集合中不存在 → 提示"条目不存在或已被删除"
- Step2 未初始化 → 先初始化

**伪代码**:
```python
# main_window.py
def _on_report_entry_activated(self, entry_id: str):
    """报告对话框中双击条目后跳转到Step2定位。"""
    if not self._ctx.collection:
        self.statusBar().showMessage("请先加载翻译集合", 5000)
        return
    entry = self._ctx.collection.get(entry_id)
    if entry is None:
        self.statusBar().showMessage(f"条目不存在或已被删除: {entry_id}", 5000)
        return
    # 切换到 Step2 tab
    self._workbench.setCurrentIndex(1)  # Step2 的 tab index
    # 通知 Step2 定位
    self._step2.locate_entry(entry_id)
```

**测试策略**: 翻译完成后报告对话框中双击条目 → 验证主窗口切换到Step2并高亮对应行。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/ai_translator/_translation_report_dialog.py` | 新建 | _TranslationReportDialog 完整实现（~350行） |
| `src/transbridge/ui/main_window.py` | 修改 | 新增 `_on_report_entry_activated(entry_id)` 槽方法（~15行） |

## 风险与注意事项

- **风险1**: 报告中条目数据与主表集合不同步（报告快照 vs 实时数据）→ 缓解：双击跳转时实时从 collection 查找，找不到则提示用户
- **注意1**: `_entry_row_ids` 映射在排序后不会自动更新——QTableWidget 排序仅改变视觉行顺序，`row()` 获取的是视觉行号。需通过 `visualRow()` / `item.row()` 保持一致
- **注意2**: 统计卡片使用固定布局（QGridLayout），卡片数因模式不同而变化——确保布局正确拉伸
- **注意3**: 报告对话框非模态（`show()`），需要管理生命周期。可由父窗口持有引用，或使用 `setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)`

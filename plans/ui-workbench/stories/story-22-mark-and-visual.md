# Story 22: 标记列与可视化系统

**所属方案**: `plans/ui-workbench/plan.md`
**技术模块**: UI (PyQt6)
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-20（同 plan）：筛选系统统一化 → 多选标签、状态标签、搜索栏、`_apply_all_filters()` 已就绪
- Story-21（同 plan）：表格交互升级 → 行内编辑、`_on_cell_clicked`、`_on_item_changed` 已就绪

### 引用的架构决策
- ADR-004: QThread + 信号总线异步模式（本 Story 不涉及后台线程）

## 验收标准

- [ ] 第 0 列为标记列，显示图标：无标记="" / ★待处理 / ?有疑问 / ✓已确认
- [ ] 点击标记列循环切换：无→★→?→✓→无；标记存储在 `_entry_marks: dict[str, str]`
- [ ] 行背景色按翻译阶段：未翻译白色、已翻译浅绿 #E8F5E9、有疑问浅黄 #FFF8E1
- [ ] 标记筛选标签行（★待处理/?有疑问/✓已确认），点击筛选对应标记条目，与现有筛选 AND 叠加
- [ ] 「👁 只看已标记」切换按钮，一键过滤出所有有标记的条目
- [ ] 底部计数显示「★ N / ? N / ✓ N | 显示 M 条（共 K 条）」
- [ ] `get_selected_entries()` 返回 ★ 标记条目（向后兼容 AI 翻译窗口）
- [ ] 标记在筛选/搜索/切换集合间持久化（会话内）

## 数据流

```
┌─ 点击标记列 (col 0) ───────────────────────────────┐
│  _on_cell_clicked(row, 0)                           │
│  → 取当前 entry → 查 _entry_marks 当前标记           │
│  → 按无→★→?→✓→无 循环到下一态                       │
│  → 更新 _entry_marks[entry.id] = new_mark           │
│  → 刷新该单元格 setText(新字符) + setForeground(颜色) │
│  → _update_count_label()                            │
└─────────────────────────────────────────────────────┘

┌─ 标记筛选 ─────────────────────────────────────────┐
│  点击「★待处理」标签                                  │
│  → _mark_filters.add("star") 或 discard             │
│  → _build_mark_tags() 重建标签样式                   │
│  → _populate_table()                                │
│  → _apply_all_filters() 叠加 _mark_filters          │
└─────────────────────────────────────────────────────┘

┌─ 聚焦开关 ─────────────────────────────────────────┐
│  点击「👁 只看已标记」                                │
│  → _focus_marked = not _focus_marked               │
│  → 按钮样式切换（普通/高亮）                         │
│  → _populate_table()                                │
│  → _apply_all_filters() 若 _focus_marked=True       │
│    则只保留 _entry_marks 中有标记的条目               │
└─────────────────────────────────────────────────────┘
```

## 关键接口

### 数据结构

```python
# 标记映射 — 替代 _selected_entry_ids
_entry_marks: dict[str, str]
# key: TranslationEntry.id
# value: "star" | "question" | "confirmed"
# 无标记的条目不在 dict 中（不存在即 None）

# 标记类型常量
_MARK_TYPES = {"star": "★", "question": "?", "confirmed": "✓"}
_MARK_CYCLE = [None, "star", "question", "confirmed"]  # 点击循环
_MARK_COLORS = {"star": "#2196F3", "question": "#FF9800", "confirmed": "#4CAF50"}
_MARK_LABELS = {"star": "★待处理", "question": "?有疑问", "confirmed": "✓已确认"}
```

### 修改的方法

```python
# Step2PreviewWidget 新增/修改的方法

def _get_mark_char(self, entry_id: str | None) -> str:
    """返回标记字符："" / "★" / "?" / "✓" """
    ...

def _get_next_mark(self, current: str | None) -> str | None:
    """循环：None→"star"→"question"→"confirmed"→None"""
    ...

def _on_cell_clicked(self, row: int, col: int) -> None:
    """重写：col==0 时循环切换标记；其他列保持现有逻辑"""
    ...

def _build_mark_tags(self) -> None:
    """新建标记筛选标签行（★待处理/?有疑问/✓已确认）"""
    ...

def _apply_all_filters(self) -> list[TranslationEntry]:
    """扩展：叠加 _mark_filters 和 _focus_marked 筛选"""
    ...

def _populate_table(self) -> None:
    """扩展：标记列显示字符+颜色；所有列设置行背景色"""
    ...

def get_selected_entries(self) -> list[TranslationEntry]:
    """改为返回所有 ★ 标记条目"""
    ...

def _update_count_label(self) -> None:
    """改为显示标记计数"""
    ...

def refresh(self, collection) -> None:
    """扩展：清理失效的 entry_id、重建标记标签、重置聚焦"""
    ...
```

## 实现步骤

### 步骤 1: 标记列替换复选框列

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- `_selected_entry_ids: set[str]` → `_entry_marks: dict[str, str]`
- `_COL_CHECK` 保留列索引，但语义从"复选框"变为"标记列"
- `_init_ui` 表头 `["", "Key", ...]` → `["标记", "Key", ...]`，列宽 ~36px
- `_populate_table` 中 Col 0 不再创建 `QTableWidgetItem` 带 `ItemIsUserCheckable` flag，而是普通文本项（`~ItemIsEditable`），内容为标记字符
- `_on_cell_clicked` 中 col==0 分支重写为循环切换逻辑
- `_on_item_changed` 移除 `_COL_CHECK` 分支（复选框变化不再存在）

**边界条件**:
- entry.id 为空 → 标记列显示空字符串，不可点击
- 集合刷新 → `_entry_marks` 清理 key 不在当前 entries 中的项
- 第一次点击无标记的条目 → 标记设为 "star"

**伪代码**:
```python
_MARK_CYCLE = [None, "star", "question", "confirmed"]

def _on_cell_clicked(self, row, col):
    if col == _COL_CHECK:
        item = self._table.item(row, _COL_CHECK)
        entry = item.data(UserRole)
        if not entry or not entry.id:
            return
        current = self._entry_marks.get(entry.id)  # None / "star" / ...
        idx = _MARK_CYCLE.index(current) if current in _MARK_CYCLE else 0
        next_mark = _MARK_CYCLE[(idx + 1) % len(_MARK_CYCLE)]
        if next_mark:
            self._entry_marks[entry.id] = next_mark
            item.setText(_MARK_TYPES[next_mark])
            item.setForeground(QColor(_MARK_COLORS[next_mark]))
        else:
            self._entry_marks.pop(entry.id, None)
            item.setText("")
        self._last_clicked_row = row  # Shift 范围锚点
        self._update_count_label()
    elif col != _COL_TRANS:
        # 其他列：现有 Ctrl/Shift 单选逻辑（保持不变，但不操作复选框，而是操作标记）
        ...
```

**测试策略**:
- 点击标记列 → 循环切换 ★→?→✓→空→★
- 标记后刷新筛选 → 标记保留
- 切换集合 → 标记不混

### 步骤 2: 行背景色

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- `_populate_table` 中为每行的所有 item 设置 `setBackground(QColor(...))`
- 颜色：未翻译=白色（默认），stage=1=浅黄 #FFF8E1，有译文=浅绿 #E8F5E9
- 标记列和译文列同样着色

**边界条件**:
- stage=0 但有 translation → 视为已翻译（绿色）
- stage=0 且无 translation → 白色（默认，不显式设置）
- 编辑译文后 → `_on_item_changed` 中重新设置该行背景色

**伪代码**:
```python
def _row_bg_color(entry) -> QColor | None:
    if entry.stage == 1:
        return QColor("#FFF8E1")  # 有疑问 → 浅黄
    if entry.translation or entry.stage >= 2:
        return QColor("#E8F5E9")  # 已翻译 → 浅绿
    return None  # 未翻译 → 默认白色

# 在 _populate_table 循环中：
bg = _row_bg_color(entry)
if bg:
    for col_item in [key_item, orig_item, trans_item, ctx_item]:
        col_item.setBackground(bg)
```

**测试策略**:
- 加载混合状态集合 → 验证三种颜色正确
- 行内编辑译文后 → 背景色从白变绿

### 步骤 3: 标记筛选标签

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- `_init_ui` 中在搜索栏下方新增 `_mark_tags_widget`（结构与分类标签行相同）
- 三个标签按钮：「★待处理」「?有疑问」「✓已确认」
- 复用 `_TAG_NORMAL/_TAG_ACTIVE` 样式
- `_mark_filters: set[str]` — 选中状态（"star"/"question"/"confirmed"）
- `_build_mark_tags()` — 统计各标记数量并创建标签
- `_apply_all_filters()` — 叠加 `_mark_filters` 筛选

**边界条件**:
- 标签计数为 0 → 该标签不显示
- 与分类/状态/搜索 AND 叠加
- 集合刷新 → 重建标签
- 标记变化（添加/移除标记）→ 需重建标签以更新计数

**伪代码**:
```python
_mark_filters: set[str] = set()

def _build_mark_tags(self):
    # 统计 _entry_marks 中各类型数量
    counter = {"star": 0, "question": 0, "confirmed": 0}
    for mark in self._entry_marks.values():
        counter[mark] += 1
    # 创建标签按钮（复用分类标签的样式和交互模式）
    for mk in ["star", "question", "confirmed"]:
        if counter[mk] == 0:
            continue
        btn = QPushButton(f"{_MARK_LABELS[mk]} {counter[mk]}")
        btn.clicked.connect(lambda checked, m=mk: self._on_mark_tag_clicked(m))
        ...

# _apply_all_filters 扩展：
if self._mark_filters:
    result = [e for e in result
              if self._entry_marks.get(e.id) in self._mark_filters]
```

**测试策略**:
- 标记几个条目为 ★ → ★标签显示数量 → 点击筛选 → 表格仅显示 ★ 条目
- 叠加分类筛选 → 只显示同时满足的条目

### 步骤 4: 聚焦开关

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- 在标记标签行末尾添加 QPushButton「👁 只看已标记」
- `_focus_marked: bool = False`
- 点击切换 → 按钮样式 toggle（高亮/普通）
- `_apply_all_filters()` 若 `_focus_marked` 为 True，只保留有标记的条目
- 无任何标记时按钮 `setEnabled(False)`

**边界条件**:
- 聚焦模式 + 标记筛选 → AND 叠加（聚焦是全局过滤）
- 聚焦模式下无结果 → 表格显示空
- 聚焦切换后计数标签更新

**伪代码**:
```python
_focus_marked: bool = False

# _apply_all_filters 扩展：
if self._focus_marked:
    result = [e for e in result if e.id and e.id in self._entry_marks]

# 聚焦按钮 toggle：
def _on_focus_toggle(self):
    self._focus_marked = not self._focus_marked
    self._focus_btn.setStyleSheet(
        _FOCUS_ACTIVE if self._focus_marked else _TAG_NORMAL
    )
    self._populate_table()
```

**测试策略**:
- 无标记时按钮灰色不可用
- 标记 3 条 → 聚焦 → 表格只显示 3 条
- 聚焦 + 分类筛选 → 叠加

### 步骤 5: 标记计数

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- `_update_count_label` 改为显示三种标记计数
- 格式：「★ {star_count} / ? {question_count} / ✓ {confirmed_count} | 显示 {shown} 条（共 {total} 条）」

**边界条件**:
- 所有标记为 0 → 显示 "★ 0 / ? 0 / ✓ 0"
- 筛选后标记计数 → 显示的是全量计数（非筛选后）

**伪代码**:
```python
def _update_count_label(self):
    star = sum(1 for v in self._entry_marks.values() if v == "star")
    q = sum(1 for v in self._entry_marks.values() if v == "question")
    conf = sum(1 for v in self._entry_marks.values() if v == "confirmed")
    shown = self._table.rowCount()
    total = len(self._entries)
    if shown == total:
        self._count_lbl.setText(f"★ {star} / ? {q} / ✓ {conf} | 共 {total} 条")
    else:
        self._count_lbl.setText(f"★ {star} / ? {q} / ✓ {conf} | 显示 {shown} 条（共 {total} 条）")
```

**测试策略**:
- 标记条目 → 计数更新
- 取消标记 → 计数减少
- 筛选后计数仍反映全量

### 步骤 6: get_selected_entries() 适配

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- `get_selected_entries()` 改为返回所有 ★（"star"）标记的条目
- 接口签名不变：`def get_selected_entries(self) -> list[TranslationEntry]`
- AI 翻译窗口等调用方无需修改

**边界条件**:
- 无 ★ 标记 → 返回空列表
- 调用了但条目已从集合移除 → 自动过滤

**伪代码**:
```python
def get_selected_entries(self) -> list[TranslationEntry]:
    result = []
    id_to_entry = {e.id: e for e in self._entries if e.id}
    for entry_id, mark in self._entry_marks.items():
        if mark == "star" and entry_id in id_to_entry:
            result.append(id_to_entry[entry_id])
    return result
```

**测试策略**:
- 标记 3 条 ★ + 2 条 ? + 1 条 ✓ → `get_selected_entries()` 返回 3 条
- AI 翻译窗口正常获取条目

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/workbench/step2.py` | 修改 | 标记列、行背景色、标记筛选、聚焦、计数、get_selected_entries |

## 风险与注意事项

- **风险 1**: `_on_cell_clicked` 中标记列点击与现有 Ctrl/Shift 行选逻辑冲突 → 缓解：标记列 col==0 独立处理，不走行选逻辑；其他列保持原有行选行为
- **风险 2**: 标记字符使用 Unicode（★/?/✓），某些字体可能不支持 → 缓解：这三个是通用 Unicode 字符（U+2605, U+003F, U+2713），在所有平台均有字体覆盖
- **注意 1**: `_apply_all_filters` 方法扩展后需保持各筛选条件的顺序：分类 → 状态 → 搜索 → 标记 → 聚焦。标记筛选和聚焦放在最后，因为前三个是数据属性，后两个是用户工作流属性
- **注意 2**: `_build_mark_tags` 需要在以下时机重建：1) `refresh()` 2) 标记变更后 `_on_cell_clicked` 3) `_on_category_tag_clicked`/`_on_stage_tag_clicked` 不需要（标记计数不变）

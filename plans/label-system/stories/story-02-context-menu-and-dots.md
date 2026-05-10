# Story 02: 表格交互集成

**所属方案**: `plans/label-system/plan.md`
**技术模块**: ui (PyQt6)
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-01（label-system）：标签库模型与管理 → `_label_library`, `_entry_labels`, `_LabelManagerDialog` 已就绪

### 引用的架构决策
- ADR-004: QThread + 信号总线异步模式

## 验收标准

- [ ] 右键点击行弹出标签列表菜单（勾选=已分配）
- [ ] 菜单底部「管理标签…」「+ 新建标签…」
- [ ] 标记列显示彩色圆点（每个标签一个圆点）
- [ ] 悬停圆点区域显示 tooltip（标签名列表）
- [ ] 移除旧的单击循环切换标记逻辑

## 数据流

```
右键行 → customContextMenuRequested
  → _build_context_menu(row)
  → QMenu: [✓] 待处理, [ ] 有疑问, [✓] 已确认, ─, 管理标签…, + 新建标签…
  → 用户点击 → _on_label_toggle(entry_id, label_id, checked)
  → 更新 _entry_labels[entry_id]
  → _populate_table() 刷新圆点
  → _update_count_label()

_populate_table 中 Col 0 渲染:
  → labels = _entry_labels.get(entry.id, set())
  → 无标签: 空文本
  → 有标签: 每标签一个 ● 字符，setForeground 为标签色
  → setToolTip("待处理\n有疑问")
```

## 关键接口

```python
def _build_context_menu(self, row: int) -> QMenu:
    """为指定行构建右键菜单"""

def _on_label_toggle(self, entry_id: str, label_id: str, checked: bool):
    """标签勾选/取消"""

def _on_quick_create_label(self):
    """右键菜单快速创建标签 → 弹出简单输入框 → 加入 _label_library"""

def _render_label_dots(self, entry_id: str) -> tuple[str, str]:
    """返回 (圆点字符串, tooltip文本)"""
```

## 实现步骤

### 步骤 1: 右键菜单

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- `_init_ui` 中设置 `self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)`
- 连接 `self._table.customContextMenuRequested.connect(self._on_context_menu)`
- `_on_context_menu(pos)` → `row = self._table.rowAt(pos.y())` → `_build_context_menu(row).exec(pos)`
- 菜单：每个标签一个 `QAction(checkable=True)`, 分隔线, 「管理标签…」「+ 新建标签…」

**边界条件**:
- 无标签时菜单显示提示
- 点空白行(row=-1)不弹菜单

**伪代码**:
```python
def _on_context_menu(self, pos):
    row = self._table.rowAt(pos.y())
    if row < 0:
        return
    menu = self._build_context_menu(row)
    menu.exec(self._table.viewport().mapToGlobal(pos))

def _build_context_menu(self, row):
    item = self._table.item(row, _COL_KEY)
    entry = item.data(UserRole)
    if not entry or not entry.id:
        return QMenu()
    
    menu = QMenu(self)
    labels = self._entry_labels.get(entry.id, set())
    
    if not self._label_library:
        menu.addAction("暂无标签，请先创建").setEnabled(False)
    else:
        for lid, info in self._label_library.items():
            action = menu.addAction(f"● {info['name']}")
            action.setCheckable(True)
            action.setChecked(lid in labels)
            action.toggled.connect(lambda checked, eid=entry.id, lid=lid: self._on_label_toggle(eid, lid, checked))
    
    menu.addSeparator()
    menu.addAction("管理标签…", self._on_manage_labels)
    menu.addAction("+ 新建标签…", self._on_quick_create_label)
    return menu

def _on_label_toggle(self, entry_id, label_id, checked):
    if entry_id not in self._entry_labels:
        self._entry_labels[entry_id] = set()
    if checked:
        self._entry_labels[entry_id].add(label_id)
    else:
        self._entry_labels[entry_id].discard(label_id)
    self._populate_table()
```

**测试策略**: 右键条目 → 勾选标签 → 关闭菜单 → 再次右键确认状态

### 步骤 2: 彩色圆点显示

**涉及文件**: 同上

**实现要点**:
- `_populate_table` 中 Col 0 改为显示彩色圆点
- 从 `_entry_labels` 取标签 ID 集合，从 `_label_library` 取颜色
- 用 Unicode ● (U+25CF) 字符拼接，每个单独设置颜色

**边界条件**:
- 超过 5 个标签 → 显示前 5 个 + "…+N"
- 无标签 → 空文本

**伪代码**:
```python
def _render_label_dots(self, entry):
    labels = self._entry_labels.get(entry.id, set())
    if not labels:
        return "", ""
    
    dots = []
    names = []
    for lid in list(labels)[:5]:  # 最多5个
        info = self._label_library.get(lid)
        if info:
            dots.append(("●", info["color"]))
            names.append(info["name"])
    
    if len(labels) > 5:
        dots.append((f"+{len(labels)-5}", "#999"))
    
    return dots, "\n".join(names)

# 在 _populate_table 中：
dots, tooltip = self._render_label_dots(entry)
mark_item = QTableWidgetItem(" ".join(d[0] for d in dots))
mark_item.setToolTip(tooltip)
# 注意: 单个 QTableWidgetItem 只能有一种 foreground 颜色
# 多色圆点需要用 QWidget 或富文本
```

**注意**: `QTableWidgetItem` 不支持多色文本。实际实现可能需要用 `QLabel` + `setCellWidget` 或使用 HTML 富文本 `<span style='color:...'>●</span>`。

**富文本替代方案**:
```python
html_parts = [f"<span style='color:{color}'>&#9679;</span>" for _, color in dots]
mark_item = QTableWidgetItem()
# 使用 QLabel as cell widget
label = QLabel("".join(html_parts))
self._table.setCellWidget(row, _COL_MARK, label)
```

**测试策略**: 给条目打上 3 个不同颜色标签 → 确认 3 个彩色圆点正确显示

### 步骤 3: Tooltip

**涉及文件**: 同上

**实现要点**:
- `mark_item.setToolTip(tooltip_text)` 设置悬停提示
- 格式：每行一个标签名

**边界条件**:
- 无标签 → 不设置 tooltip

### 步骤 4: 移除旧交互逻辑

**涉及文件**: 同上

**实现要点**:
- `_on_cell_clicked` 中移除标记切换逻辑（`col == _COL_MARK` 分支）
- 保留译文列保护（`col == _COL_TRANS` return）

**伪代码**:
```python
def _on_cell_clicked(self, row, col):
    if col == _COL_TRANS:
        return  # 双击编辑
    # 其他列点击不处理（标记由右键菜单管理）
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/workbench/step2.py` | 修改 | 右键菜单 + 圆点 + tooltip + 移除旧交互 |

## 风险与注意事项

- **风险 1**: `setCellWidget` 比 `setItem` 性能差（大量行时）。缓解：仅在条目有标签且数量 ≤5 时使用 QLabel；否则用 setItem + 纯文本
- **注意 1**: 彩色圆点渲染必须在 `_populate_table` 的 `blockSignals(True)` 块内完成

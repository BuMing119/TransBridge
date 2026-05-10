# Story 03: 筛选与聚焦更新

**所属方案**: `plans/label-system/plan.md`
**技术模块**: ui (PyQt6)
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-01（label-system）：`_label_library`, `_entry_labels`, `_label_filters` 已就绪
- Story-02（label-system）：右键菜单 + 圆点显示已实现

## 验收标准

- [ ] `_build_label_tags` 替代 `_build_mark_tags`，动态从 `_label_library` 构建
- [ ] `_apply_all_filters` 中标记筛选改为标签筛选
- [ ] 聚焦按钮改为 `any(_entry_labels.values())`
- [ ] 底部计数改为标签总数

## 数据流

```
_build_label_tags()
  → 遍历 _label_library
  → 统计 _entry_labels 中每个标签使用次数
  → 创建标签按钮（名称 + 计数 + 颜色）
  → 点击 → _label_filters toggle → _populate_table()

_apply_all_filters():
  → if _label_filters: result = [e for e in result if 
      e.id and any(lid in _entry_labels.get(e.id, set()) for lid in _label_filters)]
  → if _focus_labeled: result = [e for e in result if 
      e.id and e.id in _entry_labels and _entry_labels[e.id]]
```

## 关键接口

```python
_label_filters: set[str] = set()  # 替代 _mark_filters
_focus_labeled: bool = False       # 替代 _focus_marked

def _build_label_tags(self):
    """从 _label_library 动态构建筛选标签"""

def _on_label_tag_clicked(self, label_id: str | None):
    """标签筛选点击"""

def _on_focus_labeled(self):
    """聚焦：只看有标签条目"""

def _update_count_label(self):
    """底部计数：有标签条目数 / 总条目数"""
```

## 实现步骤

### 步骤 1: _build_label_tags

**涉及文件**: `src/transbridge/ui/workbench/step2.py`（修改）

**实现要点**:
- 替代 `_build_mark_tags`
- 从 `_label_library` 迭代，统计 `_entry_labels` 中每个标签的使用次数
- 标签按钮显示标签名 + 计数，圆点颜色作为按钮前景色

**边界条件**:
- 标签库为空 → 隐藏整个标签筛选行
- 标签使用计数为 0 → 不显示按钮（除非已选中）

**伪代码**:
```python
def _build_label_tags(self):
    while self._mark_tags_container.count():
        item = self._mark_tags_container.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
    
    if not self._label_library:
        self._mark_tags_widget.hide()
        return
    
    counter = Counter()
    for labels in self._entry_labels.values():
        for lid in labels:
            counter[lid] += 1
    
    # 「全部」标签
    all_btn = QPushButton(f"全部 {len(self._entry_labels)}")
    all_btn.clicked.connect(lambda: self._on_label_tag_clicked(None))
    self._mark_tags_container.addWidget(all_btn)
    
    for lid, info in self._label_library.items():
        count = counter.get(lid, 0)
        if count == 0 and lid not in self._label_filters:
            continue
        btn = QPushButton(f"● {info['name']} {count}")
        btn.setStyleSheet(f"color: {info['color']};" + base_style)
        btn.clicked.connect(lambda checked, l=lid: self._on_label_tag_clicked(l))
        self._mark_tags_container.addWidget(btn)
    
    self._mark_tags_widget.show()
```

### 步骤 2: _apply_all_filters 适配

**涉及文件**: 同上

**实现要点**:
- 将 `_mark_filters` 筛选逻辑改为 `_label_filters`
- 筛选条件：条目的 `_entry_labels` 中包含任一筛选标签

**伪代码**:
```python
# 在 _apply_all_filters 中：
if self._label_filters:
    result = [e for e in result 
              if e.id and _entry_labels.get(e.id, set()) & self._label_filters]

# 聚焦：
if self._focus_labeled:
    result = [e for e in result 
              if e.id and e.id in _entry_labels and _entry_labels[e.id]]
```

### 步骤 3: 聚焦按钮适配

**涉及文件**: 同上

**实现要点**:
- `_focus_marked` → `_focus_labeled`
- 按钮文本保持 `[已标记]`
- 启用条件：`any(labels for labels in _entry_labels.values())`

### 步骤 4: 计数更新

**涉及文件**: 同上

**实现要点**:
- `_update_count_label` 改为统计有标签条目数
- 显示「有标签 N 条 | 显示 M 条（共 K 条）」

**伪代码**:
```python
def _update_count_label(self):
    labeled = len([1 for labels in _entry_labels.values() if labels])
    shown = self._table.rowCount()
    total = len(self._entries)
    if shown == total:
        self._count_lbl.setText(f"有标签 {labeled} 条 | 共 {total} 条")
    else:
        self._count_lbl.setText(f"有标签 {labeled} 条 | 显示 {shown} 条（共 {total} 条）")
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/workbench/step2.py` | 修改 | 筛选 + 聚焦 + 计数更新 |

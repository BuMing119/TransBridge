# Story 04: AI 翻译作用域适配

**所属方案**: `plans/label-system/plan.md`
**技术模块**: ai_translator
**状态**: 已确认
**创建日期**: 2026-05-07

## 前置依赖

### 上游 Story
- Story-01~03（label-system）：标签库 + 右键菜单 + 筛选全部就绪

### 跨 Plan 依赖
- `ai-translation/plan.md` Story-09：组合式作用域选择器 → `_scope_mark_filters` 需适配

## 验收标准

- [ ] `_scope_mark_filters` → `_scope_label_filters`
- [ ] `_rebuild_scope_tags` 中标记维度从 `_step2._label_library` 读取
- [ ] `_build_scope_candidates` 中标记筛选改为标签筛选
- [ ] `get_selected_entries()` 保持兼容

## 数据流

```
AI 翻译窗口打开 → _rebuild_scope_tags()
  → 标记维度：从 _step2._label_library 读取标签列表
  → 每个标签创建按钮（名称 + 计数 + 颜色）
  → _scope_label_filters 替代 _scope_mark_filters

_build_scope_candidates():
  → if _scope_label_filters:
      candidates = [e for e in candidates if 
        e.id and _step2._entry_labels.get(e.id, set()) & _scope_label_filters]
```

## 关键接口

```python
# 替换
_scope_label_filters: set[str] = set()  # 原 _scope_mark_filters

# _rebuild_scope_tags 中标记维度
label_library = self._step2._label_library  # 从主表读取标签库
entry_labels = self._step2._entry_labels     # 从主表读取标签分配

# _build_scope_candidates 中标签筛选
if self._scope_label_filters:
    entry_labels = self._step2._entry_labels if hasattr(self._step2, '_entry_labels') else {}
    candidates = [e for e in candidates 
                  if e.id and entry_labels.get(e.id, set()) & self._scope_label_filters]
```

## 实现步骤

### 步骤 1: 替换数据结构

**涉及文件**: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`（修改）

**实现要点**:
- `_scope_mark_filters: set[str]` → `_scope_label_filters: set[str]`
- `_scope_mark_btns: dict[str, QPushButton]` → `_scope_label_btns: dict[str, QPushButton]`
- `_scope_mark_all_btn` → `_scope_label_all_btn`
- `_on_scope_mark_clicked` → `_on_scope_label_clicked`

**边界条件**:
- `_step2._label_library` 可能不存在（向后兼容）→ hasattr 检查
- 标签库为空 → 标记维度隐藏

### 步骤 2: _rebuild_scope_tags 适配

**涉及文件**: 同上

**实现要点**:
- 标记维度改为从 `_step2._label_library` 读取标签列表
- 统计从 `_step2._entry_labels` 读取使用次数
- 标签按钮显示标签名 + 颜色

### 步骤 3: _build_scope_candidates 适配

**涉及文件**: 同上

**实现要点**:
- 标签筛选从 `_step2._entry_labels` 读取
- 逻辑：条目的标签集合与筛选标签集合有交集即通过

### 步骤 4: get_selected_entries 兼容

**涉及文件**: 同上 + `step2.py`

**实现要点**:
- `Step2PreviewWidget.get_selected_entries()` 改为返回有标签的条目（`_entry_labels` 非空）
- AI 翻译窗口不直接调用（已在 S09 解耦），但保留接口兼容

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/ai_translator/ai_translator_window.py` | 修改 | 标记→标签适配 |
| `src/transbridge/ui/workbench/step2.py` | 修改 | get_selected_entries 兼容 |

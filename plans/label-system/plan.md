# 自定义标签系统

**对应需求**: FR7.11（替代 FR7.10）
**技术模块**: ui, ai_translator
**状态**: ✔️ 已实现
**创建日期**: 2026-05-07

## 概述

用用户自定义的多标签系统替代 FR7.10 的固定三态标记（★/?/✓）。用户创建任意数量和名称的标签（带颜色），每个条目可打上多个标签，右键菜单分配，彩色圆点显示。AI 翻译作用域同步适配。

## 功能边界

### 范围内
- 标签库 CRUD（创建/编辑/删除，名称+颜色）
- 右键菜单勾选分配标签（多标签、快速创建）
- 彩色圆点显示 + tooltip
- 动态标签筛选 + 聚焦
- AI 翻译作用域适配
- 替换旧 `_entry_marks`/`_MARK_*` 代码

### 范围外
- 标签跨会话持久化（FR8）
- 标签导入导出
- 标签与 ParaTranz 同步

## Story 清单

| Story | 标题 | 归属 Epic | 状态 |
|-------|------|---------|------|
| Story-01 | 标签库模型与管理 | ui-workbench | ✔️ · [详细](stories/story-01-label-model-and-dialog.md) |
| Story-02 | 表格交互集成（右键+圆点+tooltip） | ui-workbench | ✔️ · [详细](stories/story-02-context-menu-and-dots.md) |
| Story-03 | 筛选与聚焦更新 | ui-workbench | ✔️ · [详细](stories/story-03-filter-and-focus.md) |
| Story-04 | AI 翻译作用域适配 | ai-translation | ✔️ · [详细](stories/story-04-ai-scope-adapter.md) |

---

## Story-01: 标签库模型与管理

**对应需求**: FR7.11.1, FR7.11.3
**归属 Epic**: ui-workbench（追加 Story-23）
**状态**: ✅ 已确认
**验收标准**:
- [ ] `_label_library: dict[str, dict]` 和 `_entry_labels: dict[str, set[str]]` 数据结构
- [ ] `_LabelManagerDialog` 对话框（标签列表 + 添加/编辑/删除 + 颜色选择）
- [ ] 工具栏「管理标签」按钮打开对话框
- [ ] 移除旧 `_entry_marks`、`_MARK_TYPES`、`_MARK_CYCLE`、`_MARK_COLORS`、`_MARK_LABELS`

**实现步骤**:
1. 定义数据模型：`_label_library`（label_id→{name, color}）、`_entry_labels`（entry_id→set[label_id]）；生成唯一 label_id（`uuid.uuid4().hex[:8]`）；预设 3 个默认标签 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. 创建 `_LabelManagerDialog(QDialog)`：标签列表（QListWidget）、名称编辑（QLineEdit）、颜色选择（预设 8 色按钮）、添加/删除按钮 → 涉及文件: `src/transbridge/ui/workbench/step2.py`（或 `_label_dialog.py`）
3. 工具栏集成：「管理标签」按钮 → `_on_manage_labels()` → 弹出对话框 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
4. 移除旧标记代码：删除 `_entry_marks`、`_MARK_TYPES` 等属性和常量 → 涉及文件: `src/transbridge/ui/workbench/step2.py`

---

## Story-02: 表格交互集成

**对应需求**: FR7.11.2, FR7.11.4
**归属 Epic**: ui-workbench（追加 Story-24）
**状态**: ✅ 已确认
**验收标准**:
- [ ] 右键点击行弹出标签列表菜单（勾选=已分配）
- [ ] 菜单底部「管理标签…」「+ 新建标签…」
- [ ] 标记列显示彩色圆点（每个标签一个圆点）
- [ ] 悬停圆点区域显示 tooltip（标签名列表）
- [ ] 移除旧的单击循环切换标记逻辑

**实现步骤**:
1. 右键菜单：连接 `_table.setContextMenuPolicy(CustomContextMenu)` + `customContextMenuRequested` 信号；`_build_context_menu(row)` 构建 QMenu——遍历 `_label_library`，每个标签一个 `QAction`（checkable=True, checked=entry has label）；底部「管理标签…」「+ 新建标签…」→ 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. 彩色圆点显示：`_populate_table` 中 Col 0 改为渲染彩色圆点——遍历 `_entry_labels[entry.id]`，每个标签创建一个 QWidget 或其颜色直接设置到 cell 的 foreground/icon；多圆点用 Unicode ● 字符 + 各标签颜色 → 涉及文件: `src/transbridge/ui/workbench/step2.py`
3. Tooltip：标记列 item 设置 `setToolTip` 为所有标签名（换行分隔）→ 涉及文件: `src/transbridge/ui/workbench/step2.py`
4. 移除旧逻辑：`_on_cell_clicked` 移除标记切换逻辑 → 涉及文件: `src/transbridge/ui/workbench/step2.py`

---

## Story-03: 筛选与聚焦更新

**对应需求**: FR7.11.5, FR7.11.6
**归属 Epic**: ui-workbench（追加 Story-25）
**状态**: ✅ 已确认
**验收标准**:
- [ ] `_build_label_tags` 替代 `_build_mark_tags`，动态从 `_label_library` 构建
- [ ] `_apply_all_filters` 中标记筛选改为标签筛选
- [ ] 聚焦按钮改为 `any(_entry_labels.values())`
- [ ] 底部计数改为标签总数

**实现步骤**:
1. 重写标签筛选行：`_build_label_tags()` 从 `_label_library` 迭代生成按钮；`_label_filters: set[str]` 替代 `_mark_filters` → 涉及文件: `src/transbridge/ui/workbench/step2.py`
2. 筛选逻辑适配：`_apply_all_filters` 中标签筛选改为 `e.id and any(lid in _entry_labels.get(e.id, set()) for lid in _label_filters)` → 涉及文件: `src/transbridge/ui/workbench/step2.py`
3. 聚焦按钮适配：`_focus_marked` → `_focus_labeled`；条件 `any(labels for labels in _entry_labels.values())` → 涉及文件: `src/transbridge/ui/workbench/step2.py`
4. 计数更新：`_update_count_label` 改为显示有标签条目数 → 涉及文件: `src/transbridge/ui/workbench/step2.py`

---

## Story-04: AI 翻译作用域适配

**对应需求**: FR5.10 标签维度对接
**归属 Epic**: ai-translation（追加 Story-10）
**状态**: ✅ 已确认
**验收标准**:
- [ ] `_scope_mark_filters` → `_scope_label_filters`
- [ ] `_rebuild_scope_tags` 中标记维度从 `_step2._label_library` 读取
- [ ] `_build_scope_candidates` 中标记筛选改为标签筛选
- [ ] `get_selected_entries()` 保持兼容（返回有标签条目或不影响现有逻辑）

**实现步骤**:
1. 替换数据结构：`_scope_mark_filters: set[str]` → `_scope_label_filters: set[str]` → 涉及文件: `src/transbridge/ui/tools/ai_translator/ai_translator_window.py`
2. `_rebuild_scope_tags` 中标记维度改为从 `_step2._label_library` 读取标签列表 → 涉及文件: 同上
3. `_build_scope_candidates` 中筛选条件改为 `any(lid in _step2._entry_labels.get(e.id, set()) for lid in _scope_label_filters)` → 涉及文件: 同上

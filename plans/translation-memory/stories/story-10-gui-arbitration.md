# Story 10: GUI 面板改造 + 冲突可视化仲裁界面

**所属方案**: `plans/translation-memory/plan.md`
**技术模块**: ui
**状态**: 已确认
**创建日期**: 2026-08-14

## 前置依赖

### 上游 Story
- Story 08（已完成）：多词典 query 返回 `conflicts`（冲突候选）
- Story 09（已完成）：scope 切换 + 导入/导出按钮已就位
- Story 07（已完成）：存词典改用 `mod_file_id`

### 跨 Plan 依赖
- `ui/tools/dictionary_dialog.py`（现有存词典对话框）、`ui/tools/dictionary_panel.py`（现有面板）

### 引用的架构决策
- ADR-014 更新节（2026-08-14）：激活集默认自动（同名 mod → project → global）、冲突可视化仲裁界面、存词典从打开文件路径推断 mod 名

## 验收标准

- [ ] 词典面板适配 mod 粒度：词典列表按 mod_file_id 展示、显示 scope 标签
- [ ] 套用词典时激活集规则（同名 mod → project → global）默认自动，无需手动勾选
- [ ] 冲突仲裁对话框：列出命中冲突的词条（原文 + 多个候选译文 + 来源词典），用户逐条采纳/拒绝
- [ ] 存词典对话框改为「从打开文件路径推断 mod 名」，支持用户确认/修改 mod 名

## 数据流

```
词典面板（mod 粒度列表 + scope 标签）
    │「套用词典」
    ▼
apply_to_collection(collection, context{mod_file_id=当前 mod})
    │ 激活集自动（同名 mod → project → global），无需手动勾选
    ▼
ApplyResult{applied, conflicts}   ← Story 08 已填充 conflicts
    │
    ▼
冲突仲裁对话框（若 conflicts 非空）:
    每条：原文 + 候选译文列表（含来源词典 + scope）+ 采纳/拒绝
    → 用户选择后回写 collection
```

```
存词典对话框:
    打开文件路径（如 /path/LegacyPatch.esp）→ 推断 mod 名 = LegacyPatch
    用户可确认/修改 → save_from_collection(collection, mod_file_id, scope)
```

## 关键接口

### 数据结构

```python
class DictionaryConflictDialog(QDialog):
    """冲突可视化仲裁：逐条展示冲突词条，用户采纳/拒绝候选译文。"""
    def __init__(self, conflicts: list[Conflict], parent=None): ...
    def result(self) -> list[tuple[str, str]]:  # [(entry_id, 选中的译文), ...]
```

## 实现步骤

### 步骤 1: 词典面板 mod 粒度改造

**涉及文件**: `src/transbridge/ui/tools/dictionary_panel.py`（修改）

**实现要点**:
- 词典下拉/列表数据源从 `(scope, scope_id)` → `mod_file_id`（`manager.dictionaries` 键）
- 每本词典显示 mod 名 + scope 标签（如 `LegacyPatch [global]`）
- 复用 Story 09 的 scope 切换入口（右键或下拉）

**边界条件**:
- 无词典 → 列表空，提示「暂无词典，可导入或存为词典」
- mod 名重复（同名）→ 因唯一性已由 Story 07 保证，不出现

**伪代码**:
```python
def _rebuild_combo(self):
    self._dict_combo.clear()
    for mod_id, d in self._manager.dictionaries.items():
        label = f"{mod_id} [{d.scope}]"
        self._dict_combo.addItem(label, mod_id)
```

### 步骤 2: 套用词典激活集默认 + 冲突仲裁对话框

**涉及文件**: `src/transbridge/ui/tools/dictionary_panel.py`（修改）+ 新建 `conflict_dialog.py`

**实现要点**:
- `_on_apply_dict` 构造 `QueryContext(mod_file_id=当前 mod 名)`，不再手动让用户选词典
- `apply_to_collection` 返回 `conflicts` 非空 → 弹 `DictionaryConflictDialog`
- 对话框逐条列出：原文 + 各候选译文 + 来源 mod + scope，用户选采纳/拒绝
- 用户确认后，将被采纳译文回写到 collection（覆盖自动套用的胜者译文，或保留）

**边界条件**:
- 无冲突 → 不弹对话框，直接完成
- 冲突为 0 条但 applied>0 → 静默完成，展示统计
- 用户取消对话框 → 保留自动套用的胜者译文

**伪代码**:
```python
def _on_apply_dict(self):
    ctx = QueryContext(mod_file_id=self._current_mod_id())
    result = self._manager.apply_to_collection(self._ctx.collection, ctx)
    if result.conflicts:
        dlg = DictionaryConflictDialog(result.conflicts, self)
        if dlg.exec() == QDialog.Accepted:
            for entry_id, chosen in dlg.result():
                entry = self._ctx.collection.get(entry_id)
                if entry:
                    entry.translation = chosen
    self._refresh()
```

### 步骤 3: 冲突仲裁对话框实现

**涉及文件**: `src/transbridge/ui/tools/conflict_dialog.py`（新建）

**实现要点**:
- 列表式展示，每条冲突一行：原文（截断）+ 候选译文下拉（默认选中胜者）
- 每条提供「采纳当前选中」按钮或直接提交时按选中值
- 底部「确定」「取消」

**边界条件**:
- 空 conflicts → 对话框直接关闭
- 候选译文为空 → 跳过该条

**伪代码**:
```python
class DictionaryConflictDialog(QDialog):
    def __init__(self, conflicts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("译文冲突仲裁")
        # 每条: QLabel(原文) + QComboBox(候选译文，含来源标注) + 胜者默认选中
```

### 步骤 4: 存词典对话框推断 mod 名

**涉及文件**: `src/transbridge/ui/tools/dictionary_dialog.py`（修改）

**实现要点**:
- 对话框新增「mod 名」输入框，预填「从打开文件路径推断的 mod 名」（`Path(source_path).stem`）
- scope 选择保留（默认 global，沿用现有预填逻辑）
- 确认后以 `mod_file_id` 调 `save_from_collection`

**边界条件**:
- 无打开文件路径（无法推断）→ mod 名留空，要求用户手填
- 用户修改 mod 名 → 以用户输入为准

**伪代码**:
```python
def __init__(self, parent=None, *, source_path: str = "", ...):
    self._mod_name_edit.setText(Path(source_path).stem if source_path else "")
```

**测试策略**:
- GUI 测试（offscreen）：面板 mod 粒度列表、套用无冲突静默、套用有冲突弹对话框、存词典 mod 名预填。

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/dictionary_panel.py` | 修改 | mod 粒度列表 + 激活集默认 + 冲突对话框接入 |
| `src/transbridge/ui/tools/conflict_dialog.py` | 新建 | 冲突仲裁对话框 |
| `src/transbridge/ui/tools/dictionary_dialog.py` | 修改 | mod 名推断预填 |
| `tests/test_translation_memory_gui.py` | 修改 | 面板/对话框逻辑测试 |

## 风险与注意事项

- **风险 1**: 冲突仲裁对话框在套用流程中打断，若冲突过多体验重。缓解：提供「全部采用胜者」快捷按钮，一键接受默认仲裁结果。
- **风险 2**: 存词典 mod 名推断依赖「打开文件路径」的可用性；批量解析多文件时 mod 名可能不唯一。缓解：批量场景要求用户显式指定，或按「当前活动 slot 的源文件」推断。
- **注意 1**: 激活集默认「无需手动勾选」是产品决策（避免体验倒退），但仍需保留「手动指定词典」的进阶入口，供特殊场景（如只想查某本字典）。
- **注意 2**: `QueryContext.mod_file_id` 的「当前 mod 名」来源需从解析上下文（ctx.active_project / 当前源文件）获取，与 Story 07 的 `source_mod` 推断共用同一 helper。

# Story 10: ActionRule模型+规则编辑器

**所属方案**: `plans/ai-translation/plan.md`
**状态**: 🚧 待编码
**对应需求**: FR5.11.2, FR5.11.3
**引用 ADR**: ADR-007

## 概述

实现 `ActionRule` 数据模型（规则映射表）和规则编辑器 UI。规则用于在混合模式下为条目分配翻译/润色/跳过动作。

## 验收标准

- [ ] `ActionRule` dataclass 定义：rule_id, priority, status/label/category filters, action
- [ ] `LLMConfig` 支持 `action_rules` 列表的 INI 序列化/反序列化
- [ ] `_RuleEditorWidget`：QTableWidget 展示规则列表，支持添加/删除/上移/下移
- [ ] 每行显示：优先级、状态筛选(多选标签)、标记筛选、分类筛选、动作(下拉)
- [ ] 智能默认规则：未译→翻译、已译→润色、存疑→跳过
- [ ] 未匹配条目默认动作为「跳过」

## 实现步骤

### 步骤 1: ActionRule 数据模型
- 在 `config_manager.py` 中定义 `ActionRule` dataclass
- 字段：`rule_id: str`, `priority: int`, `status_filter: set[int] | None`, `label_filter: set[str] | None`, `category_filter: set[str] | None`, `action: str`
- 涉及文件: `src/transbridge/paratranz/config_manager.py`

### 步骤 2: LLMConfig 持久化
- `LLMConfig` 新增 `action_rules: list[ActionRule] = field(default_factory=list)`
- `save_to_file()` / `load_from_file()` 中序列化规则（JSON 字符串存入 INI）
- 涉及文件: `src/transbridge/paratranz/config_manager.py`

### 步骤 3: _RuleEditorWidget UI
- 新建 `_rule_editor_widget.py`
- QTableWidget 列：优先级 / 状态筛选 / 标记筛选 / 分类筛选 / 动作
- 工具栏：添加规则 / 删除选中 / 上移 / 下移 / 重置为默认
- 动作列使用 QComboBox（翻译/润色/跳过）
- 筛选列点击弹出多选标签面板（复用现有标签组件）
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_rule_editor_widget.py` (新)

### 步骤 4: 规则匹配引擎
- `ActionRule.match(entry) -> bool`：检查条目是否匹配该规则的所有筛选条件
- `apply_rules(rules, entries) -> dict[str, str]`：按优先级匹配，返回 {entry_id: action}
- 涉及文件: `src/transbridge/paratranz/config_manager.py`

## 涉及文件

| 文件 | 操作 |
|------|------|
| `src/transbridge/paratranz/config_manager.py` | 修改 |
| `src/transbridge/ui/tools/ai_translator/_rule_editor_widget.py` | 新建 |

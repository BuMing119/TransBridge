# 004: Story-10 编码 — ActionRule模型+规则编辑器

**日期**: 2026-05-09
**类型**: 增/改
**关联**: Epic: AI 自动翻译 > Story 10: ActionRule模型+规则编辑器

## 修改文件

### `src/transbridge/paratranz/config_manager.py` (改)
- ActionRule dataclass: rule_id/priority/status_filter/label_filter/category_filter/action，含 match()/to_dict()/from_dict()
- apply_rules(rules, entries, entry_labels): 规则引擎，按优先级匹配返回 {entry_id: action}
- LLMConfig 新增 action_rules 和 mixed_execution_order 字段，含 INI JSON 序列化

### `src/transbridge/ui/tools/ai_translator/_rule_editor_widget.py` (增)
- _RuleEditorWidget: QTableWidget 4列（优先级/状态/分类/动作QComboBox），工具栏（添加/删除/上移/下移/重置默认），3条智能默认规则

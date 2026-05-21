# Story 21: 工具描述强化 + 系统 prompt + 测试补全

**Epic**: agent-tool-expansion
**优先级**: P1
**风险**: 低
**依赖**: S17-20（所有合并 Story 完成后）
**状态**: 已方案

## 范围

合并后工具描述重写（三原则）、系统 prompt 增加工具选择指南、参数矩阵测试覆盖。

## 验收标准

- [ ] 合并后 5 个新工具（`set_filters`/`stop_task`/`write_back`/`manage_entry_labels`）的 description 重写：
  - ① 一句话说清"何时用我，不用别的工具"
  - ② 参数 enum 值前置
  - ③ 典型组合示例
- [ ] `prompts.py` 系统 prompt 增加工具选择指南段落：
  - 常见用户场景 → 对应工具
  - 易混淆工具对 → 如何区分
- [ ] 测试矩阵：
  - `set_filters`: 10 个 case（单维度设置/多维度叠加/clear 组合/边界值/None 语义）
  - `write_back`: 9 个 case（4 种 target × 有无 path + 无效 target）
  - `manage_entry_labels`: 6 个 case（4 种 action × 正常/异常参数）
  - `stop_task`: 5 个 case（指定/不指定/无效 id/已停止/无活跃任务）
- [ ] LLM 工具选择回归：验证合并后新工具名在 schema 中、旧工具名不在
- [ ] 合并后工具总数：56 → 42（-14）

## 实现步骤

1. **工具描述重写**（4 个合并后工具）：
   - `tool_editor.py#set_filters` — 遵循三原则
   - `tool_editor.py#manage_entry_labels` — 遵循三原则（已在 S20 中定义，本 Story review）
   - `tool_translator.py#stop_task` — 遵循三原则
   - `tool_writer.py#write_back` — 遵循三原则（已在 S19 中定义，本 Story review）
2. **系统 prompt 增强** — 在 `prompts.py` 的 `build_system_prompt()` 中追加工具选择指南段落：
   ```
   ## 工具选择指南
   - 筛选条目 → set_filters（不要自己遍历 get_visible_entries）
   - 管理标签 → manage_entry_labels（action 参数选择操作类型）
   - 写回翻译 → write_back（根据已加载文件类型选 target）
   - 停止任务 → stop_task（不传 task_id 则停止全部）
   - 易混淆: set_filters vs manage_entry_labels
     - set_filters: 控制"显示哪些条目"（筛选）
     - manage_entry_labels: 控制"条目有什么标签"（数据）
   ```
3. **测试文件创建** — `tests/smart_assistant/test_tool_consolidation.py`：
   - 使用 `MockAppContext` + 预设 `filter_state` / `label_library` / `entry_labels`
   - `TestSetFilters` (10 cases)
   - `TestWriteBack` (9 cases)
   - `TestManageEntryLabels` (6 cases)
   - `TestStopTask` (5 cases)
   - `TestDeprecatedWrappers` — 验证旧工具名不在 ToolRegistry 中
4. **LLM schema 回归测试** — 调用 `build_tool_schema_for_prompt(None)`，验证：
   - 新工具名存在
   - 旧工具名不存在
   - 工具总数 = 42
5. 运行全量测试 `pytest tests/smart_assistant/ -v`，确保回归通过

## 涉及文件

- `tools/tool_editor.py` — review set_filters + manage_entry_labels 描述
- `tools/tool_translator.py` — 重写 stop_task 描述
- `tools/tool_writer.py` — review write_back 描述
- `prompts/prompts.py` — 追加工具选择指南段落
- 新建 `tests/smart_assistant/test_tool_consolidation.py`

## 测试用例详细

### set_filters (10 cases)
1. 设置单一维度 stages
2. 设置多维度 stages + categories
3. 设置搜索 query + field
4. clear=True + 新值
5. clear=True 单独（清除所有）
6. labels=[] 清除标签筛选
7. 所有参数 None → 无操作
8. search_field 无效值 → 拒绝
9. stages 含无效值 → 拒绝
10. 连续两次调用叠加（clear=False 默认）

### write_back (9 cases)
1. target=esp, path 已传
2. target=eet, path 已传
3. target=xt, path 不传（使用源路径）
4. target=strings, path 已传
5. target=esp, path 不传 + 无 ESP 加载 → 错误
6. target 无效值 → 错误
7. collection 为空 → 拒绝
8. 写回成功计数验证
9. 4 个 deprecated wrapper 转发正确

### manage_entry_labels (6 cases)
1. action=create, 新标签
2. action=create, 标签名已存在 → 错误
3. action=assign, 正常
4. action=unassign, 正常
5. action=batch_assign, require_confirmation=True
6. action=batch_assign, 筛选范围为空

### stop_task (5 cases)
1. 传有效 task_id → 停止单个
2. 不传 task_id → 停止所有
3. task_id="" → 停止所有
4. task_id 不存在 → 错误
5. 无活跃任务时不传 → 提示无任务

## 量化验收门槛

- 合并后工具数: 42（56 - 14）
- 旧工具名残留: 0（仅在 schema 中检查，deprecated wrapper 代码可保留）
- 合并函数参数组合覆盖率: 100%
- 现有回归测试 100% 通过
- `register_tools()` 使用率: 7/7 模块

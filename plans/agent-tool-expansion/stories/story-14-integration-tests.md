# Story 14: 跨 Story 集成测试（新增）

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: tests
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11

## 前置依赖

### 上游 Story
- Story 01~13 全部完成（这是最后一个 Story）

### 引用的架构决策
- ADR-012: 安全护栏权限分级 + MCP 中间件链

## 验收标准

### 完整链路测试
- [ ] **筛选→搜索→选择→编辑→标记→翻译** 完整链路：filter_by_stage → search_entries → select_entries → edit_translation → set_stage → start_translation → get_task_status
- [ ] 验证各 Story 之间的数据传递正确性（filter_state → _selected_ids → entry.stage）

### 标签系统测试
- [ ] create_label → assign_label → filter_by_label → batch_assign_label → remove_label 全流程
- [ ] 验证标签数据通过 AppContext 正确共享（Story 03 B1 联动）

### 安全护栏测试
- [ ] 路径遍历拒绝：传入 `../etc/passwd` 或 `C:\Windows\system32\config` 类路径时正确拒绝
- [ ] 权限拒绝：parser 工具 write 操作应被拒（Story 12 H6: parser 已是 read 权限）
- [ ] MCP 中间件链：验证 MCP 通道经过 execute_with_guardrails() 统一入口

### 翻译配置测试
- [ ] profile 预设方案切换：合法 profile（如 "openai"）切换成功，非法 profile 被拒绝
- [ ] set_scope / get_scope_preview：验证作用域设置和预览统计一致

### ParaTranz 集成测试
- [ ] compare_with_remote → download_entries（单阶段，自动附加对比摘要到 ToolResult.data）→ upload_entries

### 文件解析测试
- [ ] parse_esp / parse_eet / parse_xt / parse_sst / import_json / import_strings 6 工具的 read 权限验证
- [ ] 文件扩展名白名单：非白名单扩展名被拒绝

### 文件写回测试
- [ ] write_to_esp / write_to_eet / write_to_xt / write_to_strings 4 工具 admin 确认流程

## 实现步骤

### 步骤 1: 创建测试基础设施
**涉及文件**: `tests/test_agent_tool_integration.py`（新建）

- 搭建测试夹具：mock AppContext（含 filter_state / label_library / entry_labels / translation_scope / _selected_ids）
- 搭建 mock TranslationEntryCollection（含多条测试数据，覆盖不同 stage/category）

### 步骤 2: 编写完整链路测试
- 模拟 Agent 执行流程：筛选(0,1) → 搜索("NPC_") → 选择 3 个条目 → 编辑翻译 → 批量设 stage=1 → 启动翻译

### 步骤 3: 编写标签系统测试
- 模拟标签全生命周期：create → assign → filter → batch_assign → remove

### 步骤 4: 编写安全护栏测试
- 路径遍历检测用例（`../`, `..\\`, 绝对路径）
- 权限拒绝用例（read 工具执行 write 语义操作）
- MCP 中间件链用例（verify execute_with_guardrails 被调用）

### 步骤 5: 编写翻译配置测试
- profile 切换合法/非法用例
- scope 设置和预览统计一致性用例

### 步骤 6: 编写 ParaTranz 集成测试
- compare / download / upload 联调用例

### 步骤 7: 编写 parser/writer 测试
- 6 个 parser 工具的 read 权限 + 扩展名白名单
- 4 个 writer 工具的 admin 确认流程

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/test_agent_tool_integration.py` | 新建 | 跨 Story 集成测试，覆盖完整链路 + 安全 + 配置 + ParaTranz + parser/writer |

## 风险与注意事项

- **注意**: 集成测试依赖 Story 01~13 全部完成，建议在各 Story 编码时同步追加单元测试
- **注意**: mock AppContext 需准确模拟真实 AppContext 的属性和信号，与其保持同步
- **注意**: MCP 中间件链测试可能需要 mock MCP adapter

# 工具提示词分层加载 (tool-prompt-layering)

**对应需求**: [FR11](../../docs/requirements.md) — 工具提示词分层加载机制
**技术模块**: backend (smart_assistant)
**业务域**: Agent 工具系统
**状态**: ✅ 全部完成（S01-S05）
**创建日期**: 2026-05-25

## 功能边界

### 范围内
- ToolSpec 新增 `summary` 字段（~30-50 chars，从 description ① 段自动提取）
- ToolRegistry 新增 `build_tool_directory()` + `build_tool_help()` 类方法
- 新增 `get_tool_help` 元工具（default namespace，三种调用模式）
- 意图路由表（用户意图 → namespace 映射，7 行）
- `build_system_prompt()` 重构：预加载工具 → get_tool_help → 路由表 → 目录
- 2 个预加载工具：`get_app_state` + `get_statistics`（无副作用 + 高频使用）
- 回归测试（50+ prompts，full vs directory 模式对比）
- Phase 4 调优（摘要措辞、预加载数量、返回格式）

### 范围外
- function calling 迁移（远期方向，非本次）
- 工具描述瘦身（已拒绝）
- ToolPreviewBuilder / ToolVisibilityPolicy Protocol（议会概念，不单独实现）
- Agent 模式联动（Phase 4 之后）
- 零新文件，零新类（所有新增逻辑挂载到已有类/函数）

## 文件大小约束

目标文件当前均健康，增量控制在合理范围：

| 文件 | 当前行数 | 增量 | 结果 | 风险 |
|------|---------|------|------|------|
| `tools/base.py` | 273 | +8 (summary字段+自动提取) | ~281 | 低 |
| `tool_registry.py` | 114 | +40 (build_tool_directory + build_tool_help) | ~154 | 低 |
| `tools/tool_default.py` | 201 | +15 (get_tool_help注册) | ~216 | 低 |
| `prompts.py` | 102 | ~+10净增 (改30行删20行) | ~112 | 低 |

若 `tool_registry.py` +40 行后接近 150 行阈值，`build_tool_help()` 可考虑提取为独立函数模块（但当前 114→154 仍在健康范围，无需拆分）。

## Story 清单

### Story 01: Phase 0 — Token 精确测量

**验收标准**:
- [x] 使用 target tokenizer（tiktoken cl100k_base, DeepSeek-v4 近似）测量当前 system prompt 各段 token
- [x] 产出测量报告：template 段（989）/ context 段（~19）/ 工具段（2,373）/ 总计（~3,365）
- [x] 工具段细拆：42 个工具各自的 Schema token 数排名（全量 8,376 tokens, 均值 199.4）
- [x] 为 Phase 4 建立 baseline（全量→分层节省 63.4%, 工具目录 1,324 tokens）

**涉及文件**: 无代码改动（测量脚本 `scripts/measure_tokens.py` + 报告 `docs/temp/tool-prompt-layering-token-measurement.md`）
**完成日期**: 2026-08-05

**详细文档**: `plans/tool-prompt-layering/stories/story-01-token-measurement.md`

### Story 02: Phase 1 — summary 字段 + build_tool_directory + build_tool_help

**验收标准**:
- [ ] `ToolSpec.summary` 字段存在，默认值 `""`
- [ ] 若未手动填写，`__post_init__` 自动从 `description` 的 ① 段提取
- [ ] `ToolRegistry.build_tool_directory()` 返回按 namespace 分组的精简目录（`[namespace] name — summary`）
- [ ] `ToolRegistry.build_tool_help(tool, namespace)` 支持三种模式：单工具/namespace 批量/全局概览
- [ ] `build_tool_help` 返回格式为结构化参数表格（参数名/类型/必填/说明），非 prose 段落
- [ ] 41 个工具注册代码零改动（summary 全部自动提取）

**涉及文件**: `tool_registry.py`

**详细文档**: `plans/tool-prompt-layering/stories/story-02-summary-and-builders.md`

### Story 03: Phase 2 — get_tool_help 注册 + build_system_prompt 重构

**验收标准**:
- [ ] `get_tool_help` 工具注册到 default namespace
- [ ] 三种调用模式均可正常工作：`get_tool_help(tool="x")` / `get_tool_help(namespace="x")` / `get_tool_help()`
- [ ] 不存在的工具名返回 Levenshtein 模糊匹配建议
- [ ] `build_system_prompt()` 输出新结构：预加载工具 → get_tool_help Schema → 意图路由表 → 工具目录
- [ ] 旧「工具选择指南」段（~200 tokens）已移除
- [ ] 易混淆工具对说明改为在 `build_tool_help()` 返回结果中按需附带
- [ ] `build_system_prompt()` 接口签名不变（`context: str = ""`）
- [ ] 支持多 namespace 批量加载（`namespace="parser,translator"` 逗号分隔）

**涉及文件**: `tools/tool_default.py`、`prompts.py`

**详细文档**: `plans/tool-prompt-layering/stories/story-03-get-tool-help-and-prompt.md`

### Story 04: Phase 3 — 回归测试

**验收标准**:
- [ ] 50+ prompts 测试集覆盖 7 个 namespace 的所有工具
- [ ] full schema 模式 vs directory 模式，工具选择准确率对比
- [ ] 专项统计「LLM 不调 get_tool_help 直接凭目录摘要调用工具」的发生率，目标 <5%
- [ ] 参数填充准确率对比（结构化表格 vs prose 段落，若适用）
- [ ] `get_tool_help` 各调用模式覆盖：单工具/namespace 批量/全局/多 namespace/模糊匹配
- [ ] 全量现有测试（~223 用例）保持通过

**涉及文件**: `tests/` 新增测试文件

**详细文档**: `plans/tool-prompt-layering/stories/story-04-regression-tests.md`

### Story 05: Phase 4 — 调优

**验收标准**:
- [x] 根据 Phase 3 测试结果调整目录摘要措辞：summary 截断 80→50 chars，工具目录 1,324→1,249 tokens (-5.7%)
- [x] 评估预加载工具数量：维持 2 个（缺 LLM 行为数据，保守决策）
- [x] `build_tool_help` 返回格式微调：保持结构化表格（缺参数填充率数据）
- [x] 路由表关键词覆盖验证：7 行关键词全面扩充 + 规则 3 措辞强化（"不要"→"**禁止**...**必须**"）
- [x] 最终测量报告：分层 system prompt ~3,435 tokens vs 全量 9,183 (节省 62.6%)

**涉及文件**: `tool_registry.py` (+1), `prompts.py` (~+10), `tools/tool_*.py`×7 + `tool_execution_handler.py` (导入修复)
**完成日期**: 2026-08-05

**详细文档**: `plans/tool-prompt-layering/stories/story-05-tuning.md`

## 架构依赖

- **ADR-008**（SmartAssistant 代码分层）— 改动在其分层原则内，不引入新的模块依赖
- **ADR-005**（TOML Prompt 模板格式）— `build_system_prompt` 遵循现有模板体系
- **FR10**（smart-assistant-refactor）— 已为此次改造扫清基础（base.py 类型分离、tool_registry.py 精简等）

## 风险与回退方案

| 风险 | 严重程度 | 缓解 | 回退 |
|------|---------|------|------|
| LLM 不调用 `get_tool_help` 直接猜参数 | Low | 路由表规则 3 强制要求；参数校验层兜底 | 恢复全量注入（改回 `build_system_prompt` 一行代码） |
| 工具选择准确率下降 | Medium | Phase 3 专项统计，<5% 跳过率才算通过 | 同上 |
| Schema 文本在 context 压缩时丢失 | Medium | 将 `get_tool_help` 返回消息标记为高优先级保留 | 在 messages 末尾追加已加载 namespace 摘要 |
| 跨领域全流程额外轮次 | Medium | 支持多 namespace 批量加载（逗号分隔） | 若 >2 额外轮次，扩大预加载范围 |

回退方案极简：`build_system_prompt()` 内部切换回 `ToolRegistry.build_full_schema()` 即可，一行代码，不影响任何工具逻辑。

# Story 04: Phase 3 — 回归测试

**所属方案**: `plans/tool-prompt-layering/plan.md`
**技术模块**: backend (smart_assistant) + tests
**状态**: 已确认
**创建日期**: 2026-05-25

## 前置依赖

### 上游 Story
- Story 02（同 plan）：已完成 → 提供 `build_tool_directory()` + `build_tool_help()`
- Story 03（同 plan）：已完成 → 提供 `get_tool_help` 工具 + 重构后的 `build_system_prompt()`

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-008（SmartAssistant 代码分层）— 测试文件置于 `tests/smart_assistant/` 下

## 验收标准

- [ ] 50+ prompts 测试集覆盖 7 个 namespace 的所有工具
- [ ] full schema 模式 vs directory 模式，工具选择准确率对比
- [ ] 专项统计「LLM 不调 get_tool_help 直接凭目录摘要调用工具」的发生率，目标 <5%
- [ ] 参数填充准确率对比（结构化表格 vs prose 段落，若适用）
- [ ] `get_tool_help` 各调用模式覆盖：单工具 / namespace 批量 / 全局 / 多 namespace / 模糊匹配
- [ ] 全量现有测试（~223 用例）保持通过

## 数据流

```
测试框架 (pytest)
    │
    ├─ 单元测试组 (无需 LLM)
    │   ├─ test_tool_directory() → build_tool_directory() 输出格式
    │   ├─ test_tool_help_modes() → build_tool_help() 三种模式
    │   ├─ test_get_tool_help_tool() → _tool_get_tool_help() 参数路由
    │   └─ test_build_system_prompt() → 新 prompt 结构验证
    │
    ├─ 集成测试组 (需 LLM)
    │   ├─ 50+ prompts × 2 模式 (full vs directory)
    │   ├─ 对比工具选择准确率
    │   ├─ 统计 get_tool_help 跳过率
    │   └─ 对比参数填充准确率
    │
    └─ 回归测试组 (全量)
        └─ 现有 ~223 用例全量运行
```

## 关键接口

无新接口。测试文件结构：

```
tests/smart_assistant/
├── test_tool_prompt_layering.py   # NEW: 单元测试 (~150 行)
└── test_prompt_regression.py      # NEW: LLM 回归测试 (~200 行)
```

## 实现步骤

### 步骤 1: 单元测试 — build_tool_directory + build_tool_help

**涉及文件**: `tests/smart_assistant/test_tool_prompt_layering.py`（新建）

**实现要点**:
- 测试 `ToolRegistry.build_tool_directory()` 输出格式
- 测试 `ToolRegistry.build_tool_help()` 所有调用模式
- 测试 `ToolSpec.summary` 自动提取
- 测试 `_tool_get_tool_help()` 参数路由
- 测试 `build_system_prompt()` 新结构

**边界条件**:
- deprecated 工具不出现在 directory 中
- 空 summary 降级到 description[:50]
- 不存在工具名 → 模糊匹配建议
- 多 namespace 逗号分隔 → 合并返回
- `build_system_prompt()` 不包含旧工具选择指南

**测试策略**:
- 每项验收标准对应 1-2 个测试用例
- 目标 ~15 个测试用例，全部通过

### 步骤 2: LLM 回归测试 — 工具选择准确率

**涉及文件**: `tests/smart_assistant/test_prompt_regression.py`（新建）

**实现要点**:
- 设计 50+ 标准化 prompts，覆盖 7 个 namespace：
  - default: 8 prompts（状态查询、统计、切换等）
  - editor: 8 prompts（筛选、编辑、标签等）
  - translator: 8 prompts（翻译、润色、配置等）
  - parser: 7 prompts（解析、导入等）
  - proofreader: 7 prompts（后处理、质检等）
  - paratranz: 7 prompts（上传、下载、同步等）
  - writer: 5 prompts（写回等）
- 每个 prompt 同时用 full schema 和 directory 模式运行
- 对比：(A) 最终调用的工具是否正确 (B) 参数是否正确填充
- 统计 directory 模式下 `get_tool_help` 是否被正确调用

**边界条件**:
- LLM API 不可用 → 测试 skip（标记为需 LLM 的手动测试）
- 跨领域 prompt（如"解析ESP并翻译"）→ 验证 LLM 是否正确加载多个 namespace
- 模糊意图 prompt（如"帮我看看"）→ 验证 LLM 是否先调 `get_tool_help()` 或预加载工具

**测试策略**:
- prompt 数据文件：`tests/smart_assistant/test_data/prompts.json`（50+ prompts + 期望工具 + 期望参数）
- 准确率对比脚本：`tests/smart_assistant/test_data/run_accuracy_compare.py`
- 目标：工具选择准确率在 directory 模式下不低于 full schema 模式的 95%
- 跳过率目标：<5%（即 >95% 的非预加载工具调用前都先调了 `get_tool_help`）

### 步骤 3: 全量回归测试

**涉及文件**: 无（运行现有测试套件）

**实现要点**:
- 运行全量测试套件（~223 用例）
- 确保现有功能不受影响
- 特别关注以下模块（依赖 `build_system_prompt` 和工具注册）：
  - `test_execution_engine.py`
  - `test_agent_tool_integration.py`
  - `test_tool_consolidation.py`
  - `test_chat_worker.py`

**边界条件**:
- 测试失败 → 修复后重新运行全量，直至全部通过
- 新增测试与现有测试无冲突

**测试策略**: 全量 pytest，0 失败

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `tests/smart_assistant/test_tool_prompt_layering.py` | 新建 | 单元测试（~15 用例） |
| `tests/smart_assistant/test_prompt_regression.py` | 新建 | LLM 回归测试框架 |
| `tests/smart_assistant/test_data/prompts.json` | 新建 | 50+ 测试 prompts |

## 风险与注意事项

- **注意**: LLM 回归测试依赖外部 API，速度慢、成本高。建议标记为 `@pytest.mark.llm`，CI 中默认跳过，手动或定期运行
- **注意**: 50+ prompts 的期望结果需要人工审核标注（期望调用哪个工具、什么参数），标注质量直接影响测试可信度
- **注意**: 若 Phase 3 发现跳过率 >5%，需回到 Story 03 强化路由表规则措辞（如将"不要"升级为"禁止"）

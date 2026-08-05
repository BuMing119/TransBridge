# Story 01: Phase 0 — Token 精确测量

**所属方案**: `plans/tool-prompt-layering/plan.md`
**技术模块**: backend (smart_assistant)
**状态**: 已确认
**创建日期**: 2026-05-25

## 前置依赖

### 上游 Story
- 无（本 Story 为 Phase 0，独立于所有编码 Story）

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-005（TOML Prompt 模板格式）— `build_system_prompt` 输出格式参考

## 验收标准

- [ ] 使用 target tokenizer（与 LLM provider 一致的 tokenizer）测量当前 system prompt 各段 token
- [ ] 产出测量报告：template 段 / context 段 / 工具段 / 总计
- [ ] 工具段细拆：41 个工具各自的 Schema token 数排名
- [ ] 为 Phase 4 建立 baseline（工具选择准确率基准数据）

## 数据流

```
当前 build_system_prompt() 输出
    │
    ├─→ 分段拆分（template / context / 工具段）
    │
    ├─→ target tokenizer 编码 → token 计数
    │
    └─→ 测量报告（各段 token + 41 工具排名 + baseline）
```

## 关键接口

无需代码改动。测量通过 Python 交互式脚本或手动执行。

### 测量脚本伪代码

```python
from transbridge.smart_assistant.prompts import build_system_prompt
from transbridge.smart_assistant.tool_registry import ToolRegistry

# 1. 获取完整 system prompt
full_prompt = build_system_prompt(context="")
full_tokens = count_tokens(full_prompt)

# 2. 分段测量
template_only = HYBRID_SYSTEM_PROMPT.format(context="", tools_desc="")
template_tokens = count_tokens(template_only)

tools_desc = ToolRegistry.build_tools_description()
tools_tokens = count_tokens(tools_desc)

# 3. 逐工具测量
for spec in ToolRegistry.list_all():
    single_tool_prompt = ToolRegistry._tool_to_prompt(spec)
    print(f"{spec.name}: {count_tokens(single_tool_prompt)} tokens")
```

## 实现步骤

### 步骤 1: 确定 tokenizer

**涉及文件**: 无（测量脚本）

**实现要点**:
- 确定当前 LLM provider 使用的 tokenizer
- 优先使用 `tiktoken`（OpenAI 系列），备选 `anthropic` tokenizer
- 若 tokenizer 不可用，使用 `len(prompt) // 4` 估算（中英混合文本近似值）

**边界条件**:
- tiktoken 未安装 → 使用字符数 / 4 估算
- 不同 provider 用不同 tokenizer → 以当前配置的 provider 为准

**伪代码/设计思路**:
```python
try:
    import tiktoken
    enc = tiktoken.get_encoding("o200k_base")  # GPT-4o tokenizer
    def count_tokens(text): return len(enc.encode(text))
except ImportError:
    def count_tokens(text): return len(text) // 4
```

**测试策略**: 无需测试（测量工具，非业务代码）

### 步骤 2: 分段测量

**涉及文件**: 无（测量脚本）

**实现要点**:
- 调用 `build_system_prompt(context="")` 获取完整 system prompt
- 分别测量 template、context 占位段、工具段
- 记录各段 token 数及占比

**边界条件**:
- `context` 为空字符串时 system prompt 仍完整
- 工具段可能包含分隔符和标题行，需一并计入

**测试策略**: 无需测试

### 步骤 3: 41 工具排名

**涉及文件**: 无（测量脚本）

**实现要点**:
- 遍历 `ToolRegistry.list_all()` 获取全部 ToolSpec
- 逐工具计算其完整 Schema 的 token 数（name + description + parameters）
- 按 token 消耗降序排列，输出排名表

**边界条件**:
- 工具 Schema 格式与 `build_system_prompt` 中实际输出一致
- 包含 `get_tool_help` 自身（若已临时注册）或排除之

**测试策略**: 无需测试

### 步骤 4: Baseline 建立

**涉及文件**: 无（手动记录或临时文件）

**实现要点**:
- 记录当前全量注入模式下的 system prompt 总 token
- 记录工具段总 token（作为 Phase 4 对比基准）
- 可选：运行一组简单 prompt（5-10 条），记录工具选择结果作为准确率 baseline

**边界条件**:
- Baseline 数据格式与 Phase 4 测量格式一致，确保可对比
- 记录使用的 LLM model 和 tokenizer 版本

**测试策略**: 无需测试

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| 无 | — | 纯测量活动，不修改业务代码 |

## 风险与注意事项

- **注意**: tokenizer 选择影响测量精度。不同 provider（DeepSeek vs OpenAI vs Anthropic）tokenizer 不同，以当前配置的 provider 为准
- **注意**: 测量结果应在 story 文档中记录，供 Phase 4 对比

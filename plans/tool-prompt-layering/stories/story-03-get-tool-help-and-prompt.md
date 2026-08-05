# Story 03: Phase 2 — get_tool_help 注册 + build_system_prompt 重构

**所属方案**: `plans/tool-prompt-layering/plan.md`
**技术模块**: backend (smart_assistant)
**状态**: 已确认
**创建日期**: 2026-05-25

## 前置依赖

### 上游 Story
- Story 02（同 plan）：已完成 → 提供 `ToolRegistry.build_tool_directory()` + `build_tool_help()`

### 跨 Plan 依赖
- 无

### 引用的架构决策
- ADR-005（TOML Prompt 模板格式）— `HYBRID_SYSTEM_PROMPT` 模板遵循现有格式

## 验收标准

- [ ] `get_tool_help` 工具注册到 default namespace
- [ ] 三种调用模式均可正常工作：`get_tool_help(tool="x")` / `get_tool_help(namespace="x")` / `get_tool_help()`
- [ ] 不存在的工具名返回 Levenshtein 模糊匹配建议
- [ ] `build_system_prompt()` 输出新结构：预加载工具 → get_tool_help Schema → 意图路由表 → 工具目录
- [ ] 旧「工具选择指南」段（~200 tokens）已移除
- [ ] 易混淆工具对说明改为在 `build_tool_help()` 返回结果中按需附带
- [ ] `build_system_prompt()` 接口签名不变（`context: str = ""`）
- [ ] 支持多 namespace 批量加载（`namespace="parser,translator"` 逗号分隔）

## 数据流

```
用户消息
    │
    ▼
build_system_prompt(context="...")
    │
    ├─ HYBRID_SYSTEM_PROMPT.format(
    │      context=...,
    │      tools_desc=<分层工具段>
    │  )
    │
    │  分层工具段结构:
    │  ┌─────────────────────────────────────────┐
    │  │ ## 核心工具（始终可用）                    │
    │  │ get_app_state 完整 Schema (~150 tokens)  │
    │  │ get_statistics 完整 Schema (~150 tokens) │
    │  ├─────────────────────────────────────────┤
    │  │ ## 工具发现                              │
    │  │ get_tool_help 完整 Schema (~60 tokens)   │
    │  │ + 使用规则说明                            │
    │  ├─────────────────────────────────────────┤
    │  │ ## 工具路由                              │
    │  │ 意图 → namespace 映射表 (~180 tokens)     │
    │  ├─────────────────────────────────────────┤
    │  │ ## 可用工具目录                          │
    │  │ [ns] name — summary (~500 tokens)        │
    │  └─────────────────────────────────────────┘
    │
    ▼
LLM 收到 system prompt
    │
    ├─ 匹配路由表 → 确定 namespace
    ├─ 调用 get_tool_help(namespace="xxx")
    │
    ▼
_tool_get_tool_help(args, ctx)
    │
    ├─ tool 非空 → ToolRegistry.build_tool_help(tool=...)
    ├─ namespace 非空 → ToolRegistry.build_tool_help(namespace=...)
    └─ 皆空 → ToolRegistry.build_tool_help()
    │
    ▼
ToolResult.ok(完整 Schema 文本) → 注入 messages → LLM 使用
```

## 关键接口

### _tool_get_tool_help (新增)

```python
def _tool_get_tool_help(args: dict, ctx) -> ToolResult:
    """获取工具的完整定义（参数Schema、返回值、规则）。
    
    三种模式：
    - get_tool_help(tool="start_translation") → 单工具
    - get_tool_help(namespace="translator") → 整组工具（推荐）
    - get_tool_help() → 全局概览
    """
    tool_name = args.get("tool")
    namespace = args.get("namespace")
    result = ToolRegistry.build_tool_help(tool=tool_name, namespace=namespace)
    return ToolResult.ok(result)
```

### build_system_prompt (重构)

```python
def build_system_prompt(context: str = "", namespace: str | None = None) -> str:
    """构建分层 system prompt。
    
    namespace 参数保留（向后兼容），但默认行为改为分层模式：
    - 预加载: get_app_state + get_statistics 完整 Schema
    - 元工具: get_tool_help 完整 Schema
    - 路由表: 意图 → namespace 映射
    - 目录: 41 工具精简摘要
    """
    preloaded = _build_preloaded_tools()       # get_app_state + get_statistics
    help_schema = _build_get_tool_help_schema() # get_tool_help 完整定义
    routing = _build_routing_table()            # 意图路由表
    directory = ToolRegistry.build_tool_directory()  # 精简目录
    
    tools_desc = f"""{preloaded}

{help_schema}

{routing}

{directory}"""
    
    return HYBRID_SYSTEM_PROMPT.format(context=context, tools_desc=tools_desc)
```

### HYBRID_SYSTEM_PROMPT 模板变更

移除的段（~200 tokens）：
- `## 工具选择指南` + `**常见场景 → 对应工具**` + `**易混淆工具对**` + `**持久化操作需确认**`

## 实现步骤

### 步骤 1: 注册 get_tool_help 工具

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_default.py`（修改）

**实现要点**:
- 新增 `_tool_get_tool_help(args, ctx)` 函数
- 在 `_register_default_tools()` 的 `register_tools("default", [...])` 列表末尾添加注册条目
- permission="read"，无特殊参数校验

**边界条件**:
- tool 和 namespace 均为 None → 调用 `build_tool_help()` 返回全局概览
- tool 提供但为空字符串 → 视为 None
- namespace 提供但为空字符串 → 视为 None
- ExecutionContext 异常不可用 → get_tool_help 不依赖 ctx（纯查询），忽略 ctx 异常

**伪代码/设计思路**:
```python
def _tool_get_tool_help(args: dict, ctx) -> ToolResult:
    tool_name = args.get("tool") or None
    namespace = args.get("namespace") or None
    result = ToolRegistry.build_tool_help(tool=tool_name, namespace=namespace)
    return ToolResult.ok(result)


# 在 _register_default_tools() 的 register_tools 列表末尾追加:
{
    "name": "get_tool_help",
    "display_name": "工具帮助",
    "description": (
        "①获取工具的完整定义（参数Schema、返回值、规则）。"
        "②参数: tool(str,可选,工具名如'start_translation'); "
        "namespace(str,可选,命名空间如'translator',返回该空间所有工具完整定义,支持逗号分隔多个namespace)。"
        "③返回: 指定工具或namespace的完整参数表格与规则说明。"
        "规则: 1.推荐使用namespace批量查询,一次获取整组工具; "
        "2.不要凭目录摘要直接调用非预加载工具,必须通过本工具获取完整定义后再调用。"
    ),
    "execute": _tool_get_tool_help,
    "permission": "read",
    "parameters": {
        "tool": {"type": "str", "required": False, "description": "工具名，如'start_translation'"},
        "namespace": {"type": "str", "required": False, "description": "命名空间，如'translator'。支持逗号分隔多个，如'parser,translator'"},
    },
},
```

**测试策略**:
- 单测：`get_tool_help(tool="start_translation")` → 返回包含参数表格的文本
- 单测：`get_tool_help(namespace="translator")` → 返回该 ns 全部工具
- 单测：`get_tool_help()` → 返回全局概览
- 单测：`get_tool_help(tool="nonexistent")` → 返回模糊匹配建议

### 步骤 2: 构建辅助函数

**涉及文件**: `src/transbridge/smart_assistant/prompts.py`（修改）

**实现要点**:
- `_build_preloaded_tools()`: 提取 get_app_state 和 get_statistics 的完整 Schema
- `_build_get_tool_help_schema()`: 生成 get_tool_help 的完整定义 + 使用规则
- `_build_routing_table()`: 生成 7 行意图 → namespace 映射表

**边界条件**:
- 预加载工具不存在或被 deprecated → 降级到空字符串
- 路由表与目录 namespace 列表保持一致

**伪代码/设计思路**:
```python
def _build_preloaded_tools() -> str:
    """构建预加载工具完整 Schema（get_app_state + get_statistics）。"""
    return ToolRegistry.build_tool_help(tool="get_app_state") + "\n\n" + \
           ToolRegistry.build_tool_help(tool="get_statistics")

def _build_get_tool_help_schema() -> str:
    """get_tool_help 完整定义 + 使用规则。"""
    schema = ToolRegistry.build_tool_help(tool="get_tool_help")
    return "## 工具发现\n\n" + schema + \
           "\n\n**使用规则**:\n" + \
           "1. 收到用户消息后，先匹配路由表确定主 namespace\n" + \
           "2. 调用 get_tool_help(namespace=\"匹配的namespace\") 获取完整定义\n" + \
           "3. 禁止凭目录摘要直接调用非预加载工具\n" + \
           "4. 跨领域任务按顺序逐一加载"

def _build_routing_table() -> str:
    """意图路由表。"""
    return """## 工具路由

根据用户意图确定需要加载的工具组，调用 get_tool_help(namespace="xxx") 获取完整定义：

| 用户意图关键词 | 加载命名空间 | 典型任务 |
|--------------|------------|---------|
| 状态、统计、切换集合/项目 | default | 全局状态概览、统计、集合/项目切换 |
| 翻译、润色、术语 | translator | AI翻译/润色、术语库配置、翻译进度 |
| 解析、导入、ESP/EET/XT/SST | parser | 文件解析、JSON导入 |
| 筛选、编辑、标签、译文修改 | editor | 条目筛选、译文编辑、标签管理 |
| PT/Paratranz、上传、下载、同步 | paratranz | ParaTranz同步、项目切换 |
| 后处理、质检、质量报告 | proofreader | 六阶段后处理、质量报告 |
| 写回、保存到文件 | writer | 译文写回ESP/EET/XT |

**规则**：
1. 收到用户消息后，先匹配上表确定主 namespace
2. 调用 get_tool_help(namespace="匹配的namespace") 获取该组完整工具定义
3. 不要凭目录摘要直接调用工具（预加载的两个除外）
4. 跨领域任务按顺序逐一加载："解析并翻译" → 先加载 parser，完成后再加载 translator"""
```

### 步骤 3: 重构 build_system_prompt

**涉及文件**: `src/transbridge/smart_assistant/prompts.py`（修改）

**实现要点**:
- 组装 4 段为新 tools_desc
- 保留 `namespace` 参数（向后兼容），但不再直接传给 `build_tool_schema_for_prompt`
- 保留 `context` 参数不变

**边界条件**:
- `context=""` → system prompt 仍完整，context 段显示为空
- 向后兼容：外部调用方 `build_system_prompt(context=..., namespace=...)` 不报错

**测试策略**:
- 单测：`build_system_prompt()` 输出包含预加载工具 Schema
- 单测：输出包含路由表
- 单测：输出包含工具目录
- 单测：输出不包含旧「工具选择指南」
- 单测：`build_system_prompt(namespace="translator")` 不报错（兼容性）

### 步骤 4: 移除旧工具选择指南

**涉及文件**: `src/transbridge/smart_assistant/prompts.py`（修改）

**实现要点**:
- 从 `HYBRID_SYSTEM_PROMPT` 模板中删除第 74-95 行（`## 工具选择指南` 到 `**持久化操作需确认**...`）
- 易混淆工具对说明改为在 Story 02 的 `_format_tool_schema()` 输出中按需附带

**边界条件**:
- 移除后回复风格和执行策略不受影响

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/tools/tool_default.py` | 修改 | 新增 `_tool_get_tool_help` 函数 + 注册条目（+15 行） |
| `src/transbridge/smart_assistant/prompts.py` | 修改 | 重构 `build_system_prompt()` + 3 辅助函数 + 删除旧指南（~+30/-20 行） |

## 风险与注意事项

- **注意**: `get_tool_help` 注册在 default namespace，但它的 description 中会引用其他 namespace 名称，确保 namespace 更新时同步更新该描述
- **注意**: `_build_preloaded_tools()` 调用 `build_tool_help(tool=...)` 依赖 Story 02 已实现，编码时按 Story 顺序执行
- **注意**: 路由表关键词必须与目录中 namespace 标签一致，确保 LLM 查表后能精确定位
- **注意**: 移除旧工具选择指南后，易混淆工具对说明的按需附带机制在 Story 02 的 `_format_tool_schema()` 中实现——当 `build_tool_help(namespace="editor")` 返回时，自动在末尾追加相关易混淆对说明

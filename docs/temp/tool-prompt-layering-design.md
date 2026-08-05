# Smart Assistant 工具提示词分层方案（草稿）

> 2026-05-25 | 状态: 草案 | 讨论: 技术议会 4 人 × 2 轮

## 1. 问题

当前 `build_system_prompt()` 将全部 41 个工具的完整 Schema（name + description + parameters）注入 system prompt，工具段约占 **12,000–14,000 tokens**。Template (~800) + context (~300) + 工具段，system prompt 总计 ~14,500 tokens。

- 对 128K 窗口模型：尚可接受，但无必要
- 对 8K 窗口模型：工具段占 55% context，严重挤压对话历史
- 对不支持 prompt caching 的 provider：每轮都全额计费
- 加新工具 → 线性膨胀，无节制机制

## 2. 核心约束

- **纯文本 prompt 注入**，非 API 原生 tool use / function calling。LLM 返回 JSON `{mode, thought, steps}`
- **无 Anthropic `tool_reference` 专有 API**。任何懒加载都必须作为常规对话轮次实现
- **AI 缺少 TransBridge 项目背景知识**，不知道 namespace 的划分逻辑。namespace 适合做组织标签，不适合做过滤维度
- **用户可接受额外 API 轮次延迟**（~2–5 秒/轮）

## 3. 方案：目录 + 按需展开

### 3.1 核心思路

System prompt 中仅注入**精简工具目录**（name + namespace 标签 + 一句话摘要），LLM 通过 `get_tool_help` 元工具按需获取完整 Schema。

- namespace 标签让 AI **自我学习**工具组织方式
- `get_tool_help` 支持单工具、按 namespace 批量、全局三种查询模式
- 极少数绝对高频工具预加载完整 Schema，消除最常见路径的额外延迟

### 3.2 Token 预算

| 组成部分 | Token 估算 | 说明 |
|----------|-----------|------|
| 工具目录（41 条摘要） | ~500 | 每条 ~50 chars，含 namespace 标签 |
| 意图路由表 | ~180 | 7 行映射表 + 使用规则 |
| `get_tool_help` 完整 Schema | ~60 | 参数极简（tool / namespace，均可选） |
| 预加载工具完整 Schema | ~300 | `get_app_state` + `get_statistics` 两个 |
| **工具段合计** | **~1,040** | vs 现状 ~14,000（**92.5% 节省**） |
| System prompt 总计 | **~2,200** | template + context + 工具段 |

### 3.3 工具目录格式

```
## 可用工具目录
[default] get_app_state — 一站式全局状态概览
[default] get_statistics — 全量翻译统计
[default] list_collections — 列出所有已加载集合
[default] switch_collection — 切换活跃集合
[default] get_current_filters — 当前筛选条件快照
[default] list_local_projects — 列出本地项目
[default] get_current_project — 轻量当前项目查询
[editor] set_filters — 设置条目筛选条件，多维度自由组合
[editor] get_visible_entries — 获取可见条目(分页)
[editor] select_entries — 选中/取消选中条目
[editor] edit_translation — 修改单条译文
[editor] set_stage — 批量设置翻译阶段
[editor] list_labels — 列出所有标签
[editor] manage_entry_labels — 管理标签(创建/分配/移除)
[translator] start_translation — 启动AI翻译(长运行)
[translator] start_polish — 启动AI润色(长运行)
[translator] stop_task — 停止/暂停/恢复任务
[translator] get_task_status — 查询任务进度
[translator] get_translation_config — 翻译配置快照
[translator] set_translation_config — 更新翻译参数
[translator] set_scope — 设置翻译作用域
[translator] get_scope_preview — 作用域条目计数
[translator] set_term_config — 术语来源配置
[parser] parse_esp | parse_eet | parse_xt | parse_sst — 解析翻译文件
[parser] import_json — 导入JSON条目
[proofreader] run_postprocess — 六阶段后处理(长运行)
[proofreader] get_quality_report — 最近质量报告摘要
[proofreader] list_quality_reports — 历史报告文件列表
[paratranz] list_projects — 列出PT项目
[paratranz] get_project_info — 项目详情
[paratranz] compare_with_remote — 对比本地与远程
[paratranz] upload_entries — 上传到PT(长运行)
[paratranz] download_entries — 从PT下载(长运行)
[paratranz] export_artifact — 导出工件包(长运行)
[paratranz] get_upload_history — 上传历史
[paratranz] get_paratranz_project — 当前PT项目
[paratranz] switch_paratranz_project — 切换PT项目
[writer] write_back — 写回译文到源文件(需确认)
```

### 3.4 意图路由表

**核心问题**：目录摘要（30–50 chars）不足以让 LLM 区分相似工具或判断"这个任务属于哪个 namespace"。LLM 选错 namespace → 多一轮往返纠错；LLM 跳过 `get_tool_help` 直接猜参数 → 参数校验拦截 → 多一轮往返。

**解决**：在 system prompt 中直接给出**用户意图 → namespace** 的映射表。LLM 无需推理 namespace 体系，只需查表匹配意图：

```markdown
## 工具路由

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
4. 跨领域任务按顺序逐一加载："解析并翻译" → 先加载 parser，完成后再加载 translator
```

**设计要点**：
- **关键词匹配而非语义理解**：表用用户会说的词（"翻译""解析""上传"），而非 namespace 名。LLM 通过关键词映射，不需要理解 TransBridge 的领域划分
- **get_tool_help 的发现性自然解决**：路由表本身就是 `get_tool_help` 的使用文档，LLM 读到表就知道了调用方式
- **namespace 仍然出现在目录中**：目录中的 `[namespace]` 标签作为路由表的补充——当用户意图模糊时，LLM 可对比目录中的 namespace 分布辅助判断

### 3.5 `get_tool_help` 元工具

**Schema**：

```json
{
  "name": "get_tool_help",
  "description": "获取工具的完整定义（参数Schema、返回值、规则）。用于在使用工具前了解其详细参数。",
  "parameters": {
    "tool": {"type": "str", "required": false, "description": "工具名，如'start_translation'"},
    "namespace": {"type": "str", "required": false, "description": "命名空间，如'translator'。返回该空间所有工具的完整定义"}
  }
}
```

**三种调用模式**：

| 调用方式 | 返回内容 | 使用场景 |
|----------|---------|---------|
| `get_tool_help(tool="start_translation")` | 单个工具的完整 description + parameters | 偶尔使用一个低频工具 |
| `get_tool_help(namespace="translator")` | 该 namespace 所有工具的完整 Schema | **推荐用法**：任务开始时批量加载 |
| `get_tool_help()` | 按 namespace 分组的工具概览 | 探索/不确定该用哪个 namespace |

**实现**：~30 行，从 `ToolRegistry` 中按 name 或 namespace 查找 ToolSpec，返回其完整 definition。

### 3.6 预加载工具（仅 2 个）

| 工具 | 预加载理由 | 额外开销 |
|------|-----------|---------|
| `get_app_state` | 几乎所有会话第一步——了解当前文件加载状态、PT 配置、项目信息 | ~150 tokens |
| `get_statistics` | 翻译进度概览，会话开始和翻译后常用 | ~150 tokens |

这两个工具的完整 Schema 直接写在 system prompt 中（或通过 `HYBRID_SYSTEM_PROMPT` 模板的预加载段注入）。它们是事实上的"准核心工具"——最接近 CC 中 Read/Glob 的定位（感知型、无副作用、几乎每轮都可能用到）。

其余 39 个工具仅目录中出现，按需通过 `get_tool_help` 展开。

## 4. 交互流程示例

### 4.1 简单查询（零额外延迟）

```
用户: "现在什么状态？"

Round 1:
  System: 目录(~940) + get_app_state(full) + get_statistics(full) + get_tool_help(full)
  → AI: get_app_state() → 返回完整状态
  → AI: "当前加载了 xxx.esp，共 1523 条，已翻译 890 条..."

总轮次: 1 | 额外轮次: 0
```

### 4.2 翻译任务（1 次批量加载）

```
用户: "帮我把未翻译的NPC对话翻译了"

Round 1:
  System: 目录 + 路由表 + get_app_state(full) + get_statistics(full) + get_tool_help(full)
  → AI: 路由表匹配："翻译" → namespace="translator"
  → AI: get_tool_help(namespace="translator") → 获取 9 个翻译工具完整 Schema
  → AI: get_app_state() → 确认已加载集合

Round 2:
  System: 追加了 translator 全部工具完整 Schema
  → AI: get_statistics() → 确认未翻译数量
  → AI: set_scope(stages=[0], categories=["NPC_"]) → 设置作用域
  → AI: start_translation(entry_ids=null) → 启动翻译
  → AI: "已启动翻译任务，共 120 条，任务ID: xxx"

总轮次: 2 | 额外轮次: 0（vs 全量注入也是 2 轮）
```

### 4.3 跨领域任务（2 次批量加载）

```
用户: "解析这个ESP，翻译NPC对话，然后上传到ParaTranz"

Round 1:
  System: 目录 + 路由表 + 预加载
  → AI: 路由表匹配："解析" → namespace="parser"
  → AI: get_tool_help(namespace="parser") → 获取解析工具
  → AI: parse_esp(path="xxx.esp") → 解析完成

Round 2:
  System: 追加 parser 完整工具
  → AI: 路由表匹配："翻译" → namespace="translator"
  → AI: get_tool_help(namespace="translator") → 获取翻译工具
  → AI: set_scope(...) → start_translation(...) → 翻译完成

Round 3:
  System: 追加 translator 完整工具
  → AI: 路由表匹配："上传" → namespace="paratranz"
  → AI: get_tool_help(namespace="paratranz") → 获取PT工具
  → AI: upload_entries(...) → 上传完成

总轮次: 3 | 额外轮次: 0（vs 全量注入也是 3 轮）
```

**关键观察**：因为全量注入方案中 LLM 也需要多轮才能完成复杂任务，`get_tool_help` 的额外轮次通常**被正常的多轮交互自然吸收**。只是在最简单的单工具场景（如唯一一次调用 `write_back`）中会多 1 轮。

## 5. 风险与缓解

| 风险 | 严重程度 | 缓解 |
|------|---------|------|
| LLM 不调用 `get_tool_help` 直接猜测参数 | Low | **意图路由表**将 `get_tool_help` 作为查表后的唯一后续动作——LLM 读到路由表就学会了调用方式。兜底：参数校验层（`InputValidationGuard`）拦截错误 → 返回友好错误 → LLM 看到错误后自然调用 `get_tool_help` 修正 |
| 路由表意图匹配失败（用户表达不在关键词范围内） | Low | LLM 可从目录中的 `[namespace]` 标签辅助判断。最坏情况：LLM 加载了错误的 namespace → 发现工具不匹配 → 下一轮重新加载正确的 namespace（代价 = 1 轮往返） |
| LLM 频繁调用 `get_tool_help`（每个工具调一次） | Low | Prompt 中路由表规则引导优先使用 namespace 批量查询。`get_tool_help(namespace="translator")` 一次返回 9 个工具 |
| Plan 模式下多工具引用，逐个查询增加轮次 | Medium | Orchestrator 解析 plan steps 后，自动汇总所有被引用的 namespace，在下一轮 system prompt 中一次性注入。或 Prompt 中引导：plan 模式下先用 `get_tool_help()` 获取全局概览再制定 plan |
| 目录摘要不足以区分易混淆工具（如 `set_filters` vs `manage_entry_labels`） | Low | 路由表已将两者归入同一 namespace（`editor`），`get_tool_help(namespace="editor")` 返回完整 Schema 后再区分。必要时在 `get_tool_help` 返回结果中附带易混淆工具对说明 |
| LLM 幻觉工具名 | Low | `get_tool_help(tool="non_existent")` 返回模糊匹配建议（Levenshtein ≤ 3），如"未找到 'start_translate'，您是否要找: start_translation？" |
| **显式加载协议的认知负担**（vs CC 的根本差异） | **High** | CC 的 `tool_reference` 是 API 原生特性，框架透明拦截，模型无需知道加载机制。本方案中 LLM 必须**显式推理**"查路由表 → 调 `get_tool_help` → 再用真实工具"，这是一个 CC 没有的额外认知步骤。路由表将推理降级为查表，但无法消除。缓解：Phase 3 回归测试中专项统计"跳过 `get_tool_help` 直接调用工具"的发生率，若 > 5% 则需在路由表规则中加强硬约束（如"**禁止**凭目录摘要直接调用工具"） |
| **Schema 作为文本注入，压缩时可能丢失** | Medium | CC 的 MCP Schema 在框架层有内存缓存，压缩后自动重新注入。本方案中 `get_tool_help` 的返回结果是普通 tool_result 文本，混在对话历史中。Context 压缩时已加载的 Schema 可能与普通对话内容一起被丢弃，LLM 需要**重新调用 `get_tool_help`** 才能恢复。缓解：在 ConversationManager 的压缩策略中，将 `get_tool_help` 的返回消息标记为高优先级保留（与 system prompt 同级）；或在 messages 末尾追加"已加载 namespace"摘要提示，让 LLM 知道自己拥有哪些完整 Schema |
| **跨领域全流程的轮次累积** | Medium | 单领域任务中额外轮次被正常交互吸收，但"解析 → 筛选 → 编辑 → 翻译 → 质检 → 上传 → 写回"这种 7 领域全流程，每次切换都需 `get_tool_help` 调用，在已有 7+ 轮的基础上再叠加。缓解：支持多 namespace 批量加载（`get_tool_help(namespace="parser,translator,paratranz")`），让 LLM 在跨领域任务开始时一次性加载 2-3 个相关 namespace，而不是逐个切换 |
| **`get_tool_help` 返回格式影响参数填充准确率** | Medium | CC 中工具 Schema 在 API 层 `tools` 数组中以结构化 JSON 呈现，模型被训练优化来处理这种格式。本方案中 Schema 是注入 messages 的**纯文本**，结构化程度直接决定 LLM 能否正确填参。缓解：`build_tool_help()` 返回格式必须标准化——每个工具以参数表格（参数名 / 类型 / 必填 / 说明）呈现，而非 prose 段落；在 Phase 4 回归测试中对比 prose vs 表格两种格式的参数填充准确率 |

## 6. 实现任务

### 6.1 `ToolSpec` 新增 `summary` 字段

```python
@dataclass
class ToolSpec:
    name: str
    display_name: str
    description: str          # 完整描述（供 get_tool_help 返回）
    summary: str = ""         # NEW: 一句话摘要（~30-50 chars，供目录使用）
    parameters: dict = ...
    ...
```

现有 description 采用 `①功能简述。②参数...③返回...④规则...` 格式，`summary` 可从第①条自动提取，无需手工填写 41 条。

**自动提取逻辑**（`ToolSpec.__post_init__` 或注册时）：
```python
if not self.summary and self.description:
    # 提取 ① 到第一个 ② 之间的内容
    import re
    m = re.match(r'①(.+?)(?:②|$)', self.description)
    if m:
        self.summary = m.group(1).strip()
```

### 6.2 `ToolRegistry` 新增方法

```python
@classmethod
def build_tool_directory(cls) -> str:
    """构建精简工具目录（namespace 标签 + name + summary）。"""
    ...

@classmethod
def build_tool_help(cls, tool: str | None, namespace: str | None) -> str:
    """返回指定工具或 namespace 的完整 Schema。"""
    ...
```

### 6.3 新增 `get_tool_help` 工具

注册到 `default` namespace，标记为不可废弃。Framework 拦截调用时走本地查询（不走 LLM API，不产生额外计费）——但这在当前纯文本架构下无法实现。实际实现：作为普通工具，由 ExecutionEngine 执行，返回文本结果注入下一轮 messages。

```python
def _tool_get_tool_help(args: dict, ctx) -> ToolResult:
    tool_name = args.get("tool")
    namespace = args.get("namespace")
    result = ToolRegistry.build_tool_help(tool=tool_name, namespace=namespace)
    return ToolResult.ok(result)
```

### 6.4 `prompts.py` 修改

```python
def build_system_prompt(context: str = "") -> str:
    directory = ToolRegistry.build_tool_directory()        # ~500 tokens
    routing_table = _build_routing_table()                  # ~180 tokens
    preloaded = _build_preloaded_tools()                    # get_app_state + get_statistics
    help_schema = _build_get_tool_help_schema()             # ~60 tokens

    tools_desc = f"""{preloaded}

{help_schema}

{routing_table}

{directory}"""

    return HYBRID_SYSTEM_PROMPT.format(context=context, tools_desc=tools_desc)
```

`get_tool_help` 的完整 Schema 放在路由表**之前**，让 LLM 先看到元工具的定义，再看到路由表指引如何使用它。

旧 system prompt 中的「工具选择指南」段（易混淆工具对说明，~200 tokens）移除，改为在 `get_tool_help` 的返回结果中按需附带。

### 6.5 改动汇总

| 文件 | 改动 | 行数估计 |
|------|------|---------|
| `tools/base.py` (ToolSpec) | 新增 `summary` 字段 + 自动提取 | +8 |
| `tool_registry.py` | 新增 `build_tool_directory()` + `build_tool_help()` | +40 |
| `tools/tool_default.py` | 注册 `get_tool_help` 工具 | +15 |
| `prompts.py` | 重构 `build_system_prompt()` | ~30（改）+ ~20（删） |
| `conversation_orchestrator.py` | 无需改动（`build_system_prompt` 接口不变） | 0 |
| 41 条工具注册 | 无需改动（summary 自动提取） | 0 |
| **总计** | | **~110 行** |

### 6.6 实施顺序

1. **Phase 0**（前置）：用 target tokenizer 精确测量当前 system prompt 各段 token
2. **Phase 1**：实现 `summary` + `build_tool_directory()` + `build_tool_help()`
3. **Phase 2**：注册 `get_tool_help` + 修改 `build_system_prompt()`
4. **Phase 3**：建立工具选择准确率回归测试（50+ prompts，对比 full vs directory 模式）
5. **Phase 4**：按测试结果调优——调整目录摘要措辞、预加载工具数量、`get_tool_help` 返回格式

## 7. 待定问题

- [ ] **Plan 模式特殊处理**：LLM 制定 plan 时如果引用未加载的 namespace 工具，应提示先调 `get_tool_help()` 再制定 plan？还是容忍 plan 中参数细节不完整、执行阶段再修正？推荐后者（与 CC "宽进严出"一致）
- [ ] **易混淆工具对说明**：已从 system prompt 移除（~200 tokens）。改为在 `get_tool_help` 的返回结果中按需附带。实际效果需测试验证
- [x] **预加载工具数量**：当前建议 2 个（get_app_state + get_statistics）。**不建议加 `get_task_status`**。预加载的原则是"无副作用 + 几乎所有会话都会用"，`get_task_status` 仅翻译任务中途调用，不符合"几乎所有会话"。保持 2 个是正确的。若 Phase 4 测试发现某工具在 >80% 会话中被调用，再考虑加入预加载列表
- [ ] **`get_tool_help()` 返回缓存**：同一 namespace 在会话中多次查询时，是否在 system prompt 中缓存已加载的 Schema（避免重复注入）？当前 ConversationManager 的 messages 中已自然缓存。补充：需确认 ConversationManager 的压缩策略不会裁剪掉 `get_tool_help` 返回的工具定义消息。若会裁剪，需将该类消息标记为高优先级保留
- [ ] **Agent 模式联动**：当 session 处于 translator Agent 时，是否默认 preload translator namespace 完整 Schema（跳过 `get_tool_help` 查询）？**建议在 Phase 4 之后再做**，过早优化会增加变量。先验证基础方案的正确性，确认 token 节省和准确率达标后，再按 Agent 模式做针对性优化
- [ ] **路由表是否覆盖全部用户表达**：7 行映射表是否足够覆盖实际用户意图？Phase 4 按测试数据决定是否扩充关键词列
- [ ] **跨领域任务的批量加载**：`get_tool_help` 是否支持多 namespace 批量查询（如 `namespace="parser,translator,paratranz"`）？对于"解析→翻译→上传"这类明确的跨领域全流程，LLM 在一次调用中预加载 2-3 个 namespace 可消除逐一切换的额外轮次。建议在 Phase 2 实现时预留逗号分隔的多 namespace 语法
- [ ] **`get_tool_help` 返回格式标准化**：返回的完整 Schema 应使用**结构化参数表格**（参数名 / 类型 / 必填 / 说明）而非 prose 段落。CC 中模型被训练优化来处理 API 层 `tools` 数组中的结构化 JSON，本方案中 Schema 以文本形式注入 messages，结构化程度直接决定参数填充准确率。建议在 Phase 1 实现 `build_tool_help()` 时就采用表格格式，Phase 4 回归测试中对比 prose vs 表格两种格式的准确率
- [ ] **显式加载跳过率监控**：Phase 3 回归测试中需专项统计"LLM 不调 `get_tool_help` 直接凭目录摘要调用工具"的发生率。若 >5%，需在路由表规则中加强硬约束（如将"规则 3"从"不要凭目录摘要直接调用工具"升级为"**禁止**凭目录摘要直接调用非预加载工具，必须通过 get_tool_help 获取完整定义后再调用"）
- [ ] **Context 压缩兼容性**：确认 ConversationManager 的压缩策略不会裁剪 `get_tool_help` 的返回消息。若会裁剪，方案为：(a) 将该类消息标记为与 system prompt 同级的高优先级保留；(b) 或在 messages 末尾追加"已加载 namespace: [translator, parser]"摘要提示，让压缩后的 LLM 知道自己拥有哪些完整 Schema

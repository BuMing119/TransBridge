# 工具描述审核规范

> 用于子 Agent 审核 `docs/temp/batch*.md` 中的 LLM 工具描述文档。

---

## 审核流程

### 第一步：读取上下文

1. 读取待审核的 batch 文件（`docs/temp/batchN-*.md`）
2. 读取对应的源码文件，对照验证参数准确性：
   - Batch 1 → `src/transbridge/smart_assistant/tools/tool_editor.py`
   - Batch 2 → `src/transbridge/smart_assistant/tools/tool_translator.py`
   - Batch 3 → `src/transbridge/smart_assistant/tools/tool_writer.py` + `tool_parser.py`
   - Batch 4 → `src/transbridge/smart_assistant/tools/tool_proofreader.py` + `tool_paratranz.py`
   - Batch 5 → `src/transbridge/smart_assistant/tools/tool_default.py`
3. 对照源码中的 `_PARAM_SCHEMAS` 字典和函数实现验证每一项

### 第二步：逐工具检查（15 项）

对每个工具，逐项检查以下内容：

#### A. 参数准确性（4 项）

| # | 检查项 | 验证方法 |
|---|--------|---------|
| A1 | **参数名一致** | 文档中的参数名是否与 `_PARAM_SCHEMAS` 中的 key 完全一致 |
| A2 | **必填/可选一致** | 文档标注的必填/可选是否与 schema 中的 `required` 字段一致 |
| A3 | **枚举值完整** | 如果参数有枚举值（如 `stages`、`action`、`target`），文档是否列出所有合法值 |
| A4 | **默认值正确** | 如果参数有默认值，文档是否标注正确（对照函数实现中的 `args.get("xxx", default)`） |

#### B. 遗漏与多余（3 项）

| # | 检查项 | 验证方法 |
|---|--------|---------|
| B1 | **遗漏参数** | schema 中有的参数，文档是否全部列出 |
| B2 | **多余参数** | 文档中有的参数，schema 中是否存在 |
| B3 | **遗漏工具** | 该 namespace 下的所有非废弃工具是否都在文档中（对照 `_register_*_tools()` 注册列表） |

#### C. 返回与副作用（3 项）

| # | 检查项 | 验证方法 |
|---|--------|---------|
| C1 | **副作用准确** | 如果工具有副作用（写文件/改状态/启后台任务），文档是否用使用面语言描述。对照函数实现确认副作用存在且描述正确 |
| C2 | **返回字段准确** | 文档描述的返回字段是否与 `ToolResult.ok(data={...})` 中的实际字段一致 |
| C3 | **副作用翻译** | 副作用描述是否使用了使用面语言而非开发面术语——见下方「禁止术语表」 |

#### D. 使用面语言（3 项）

| # | 检查项 | 验证方法 |
|---|--------|---------|
| D1 | **无开发面术语** | 是否包含以下禁止术语：`ctx.`、`TaskManager`、`_entries`、`_id_index`、`AppContext`、`safe_mutate`、`INI`、`save_to_file`、`threading`、`pyqtSignal`、`slot`（指内部槽位时） |
| D2 | **无 Story 编号** | 是否包含 `Story XX`、`已废弃`、`合并 X→Y`、`ADR-` 等开发历史信息 |
| D3 | **上下文完整** | 需要"选择"的参数是否告诉 LLM 如何获取可选值——要么列出枚举，要么指明先调哪个工具查询（如"先调用 `list_labels` 获取可用标签名"） |

#### E. 格式（2 项）

| # | 检查项 | 验证方法 |
|---|--------|---------|
| E1 | **三段结构** | 每个工具是否包含"描述→参数→使用规则"三段（副作用/返回可嵌入使用规则中） |
| E2 | **条目标识** | 如果工具有 `entry_id` / `entry_ids` 参数，是否标注"使用 `get_visible_entries` 返回的 `key` 字段值" |

---

## 问题严重级别

| 级别 | 定义 | 示例 |
|------|------|------|
| **致命** | LLM 按文档调用会直接报错 | 参数必填但文档写成可选 |
| **重要** | LLM 的行为理解与实际不符 | 副作用描述反了（"追加到集合"实际不追加） |
| **遗漏** | 缺少信息，LLM 无法正确使用 | 漏了参数、漏了返回字段 |
| **不精确** | 信息有偏差但不致命 | 返回字段名有误、默认值标注不对 |
| **语言** | 使用了开发面术语 | 出现 `ctx.filter_state` 等内部名称 |

---

## 禁止术语表

以下术语在文档中**不允许出现**，必须替换为使用面语言：

| 开发面术语 | 使用面替代 |
|-----------|-----------|
| `ctx.filter_state` | "表格筛选条件" |
| `ctx.active_slot` | "当前活跃的翻译集合" |
| `ctx.esp_path` / `ctx.eet_path` / `ctx.xt_path` | "已解析的 ESP/EET/XT 源文件路径" |
| `ctx.label_library` | "标签库" |
| `ctx.entry_labels` | "条目标签关系" |
| `ctx.translation_scope` | "翻译作用域" |
| `ctx._selected_ids` | "临时选择集合" |
| `TaskManager` | "后台任务管理系统" |
| `TaskManager.cancel()` | "发送任务停止信号" |
| `INI` / `INI 配置文件` | "配置文件" 或直接说"配置被保存" |
| `save_to_file()` | 同上，说"持久化"或"被保存" |
| `threading` / `后台线程` | "后台任务" |
| `_load_llm_config()` | "读取当前配置" |
| `collection.get()` | "在集合中查找条目" |
| `_PARAM_SCHEMAS` | （不应出现在文档中） |
| `@require_collection` | （不应出现在文档中） |
| `pyqtSignal` | （不应出现在文档中） |
| `safe_mutate` | （不应出现在文档中） |

---

## 对照源码的关键位置

每个工具模块中，审核时需要读取的代码位置：

```
1. _PARAM_SCHEMAS 字典 — 参数定义（名称、类型、required、description）
2. 函数签名 + 实现 — 副作用行为 + args.get() 默认值
3. ToolResult.ok() 调用 — 返回的 data 字段
4. _register_*_tools() — 工具注册列表（确认无遗漏）
```

---

## 输出格式

每个工具的审核结论使用以下表格格式：

```
| 工具名 | 级别 | 检查项编号 | 问题描述 |
|--------|------|-----------|---------|
| xxx    | 致命/重要/遗漏/不精确/语言 | A1/B1/... | 具体问题 + 修正建议 |
```

每个 batch 审核完成后，输出汇总：

```
## Batch N 审核结果

### 问题清单
（表格）

### 统计
- 致命: N 项
- 重要: N 项
- 遗漏: N 项
- 不精确: N 项
- 语言: N 项
- 通过: N 个工具

### 需人工确认
（如有不确定的项，列出请人工判断）
```

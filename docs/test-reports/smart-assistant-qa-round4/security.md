# Smart Assistant -- 安全审查报告

**日期**: 2026-05-13
**审查人**: QA Agent (安全维度)
**审查范围**: `src/transbridge/smart_assistant/` 全量 + 关联 infra/ui/config 模块
**审查方法**: 全新独立审查，未参考任何历史审查报告。逐文件阅读 + 全路径追踪。

---

## 发现的问题

### Blocker 级

无。未发现可被远程利用的代码执行、认证完全绕过或大规模数据泄露问题。

---

### Critical 级

| # | 问题 | 文件:行号 | 攻击面 | 修复建议 |
|---|------|----------|--------|---------|
| C1 | **MCP Server 默认无认证** — `auth_token` 默认值为空字符串 `""`，`_authenticate` 方法在 token 为空时无条件放行所有请求 | `config/llm.py:79-83` (默认值), `mcp/server.py:55-56` (放行逻辑) | 若 MCP 被启用（`mcp_enabled=True`）但未配置 auth_token，任何能向进程 stdio 写入的本地程序均可调用所有 exposed 工具，包括 write 级（若 write_policy 非 deny）工具 | 当 `mcp_enabled=True` 且 `auth_token` 为空时，Server 启动阶段应报错或强制要求配置 token。不应允许空 token 运行 |
| C2 | **API Key 明文存储** — `LLMConfig.api_key` 和 `LLMConfig.embedding.api_key` 以明文写入 `data/paratranz_config.ini` 文件 | `config/llm.py:96` (save api_key), `config/llm.py:118` (save embedding_api_key), `config/llm.py:178` (load api_key), `config/llm.py:201` (load embedding_api_key) | 任何有本地文件系统读取权限的进程或用户均可直接读取 API Key。可用于消耗配额、访问 LLM 服务或窃取 embedding 能力 | 建议使用操作系统密钥链 (Windows Credential Manager / macOS Keychain / Linux Secret Service) 加密存储。若短期无法实现，至少使用密钥派生 (PBKDF2) + AES 加密 INI 中的敏感字段 |

---

### Major 级

| # | 问题 | 文件:行号 | 攻击面 | 修复建议 |
|---|------|----------|--------|---------|
| M1 | **Markdown 渲染器未校验链接协议** — `_apply_inline` 将 `[text](url)` 直接转为 `<a href="url">text</a>`，不对 URL 协议进行白名单校验 | `infra/markdown_renderer.py:49` (链接 regex 替换) | LLM 返回内容中若含 `[click](javascript:alert(1))` 或 `[click](data:text/html,...)` 链接，点击后 `QDesktopServices.openUrl()` 可能触发恶意行为。取决于 Qt 版本，部分旧版 Qt 会执行 `javascript:` 协议 | 在链接替换前校验 URL 协议，仅允许 `http:`、`https:` 及内部锚点 `#`。对不允许的协议替换为 `about:blank` 或删除 href 属性 |
| M2 | **InputValidationGuard 路径遍历检查的 key 白名单不完整** — `_detect_path_traversal` 仅硬编码了 9 个参数名 | `guardrails/input_validator.py:66-69` | 若未来新增工具使用不同的路径参数名（如 `dest`, `output`, `file`, `directory`, `save_to`），该层的路径遍历检测将静默跳过。当前无此类工具，但缺少防御性保障 | 改为从 ToolSpec.parameters 中自动识别 `path`/`file` 类参数，或采用更通用的启发式检测（任何含 `path` 或 `file` 子串的参数名均检查） |
| M3 | **MCP admin_tool_whitelist 配置实质无效** — `MCPAdapter._is_exposed` whitelist admin 工具后，`PermissionGuard` 在 `execute_with_guardrails` 中仍会因 `admin_confirm_required` 而阻断执行 | `mcp/adapter.py:52-58` (is_exposed), `guardrails/permission.py:29-32` (admin confirmation) | MCP 通道无法完成 HITL 确认，admin 工具即使被 whitelist 也无法实际执行。`_admin_whitelist` 配置误导用户认为工具可用 | 在 MCP 通道中，若 admin_confirm 启用且工具在 whitelist 中，应跳过 PermissionGuard 的 admin 阻断（因为 whitelist 已代表显式授权）。或者明确文档化：MCP 不支持 admin 工具 |
| M4 | **上传文件无大小限制** — `_on_upload_file` 接受任意大小文件后同步解析 | `ui/tools/smart_assistant/chat_widget.py:717-740` (文件选择与解析) | 用户选择超大文件（如数 GB 的 PDF/Excel/CSV）可能导致内存耗尽 (OOM) 并导致应用崩溃。解析器 (openpyxl/pdfplumber/python-docx) 均为同步、无流式限制 | 添加文件大小前置检查（建议限制为 50MB），超过阈值时提示用户拒绝。对 PDF/DOCX 可考虑仅读取前 N 页 |
| M5 | **OutputValidationGuard 强制 data 为 dict，非 dict 非 None 即拒绝** — `after_execute` 第一行类型检查过于严格 | `guardrails/output_validator.py:33-34` | 若工具返回 `data` 为合法 list（如 `[{"a": 1}]`），OutputValidationGuard 将拒绝整个输出，即使其中无敏感信息。当前工具均返回 dict，但约束过紧限制了扩展性 | 将类型检查放宽为 `dict | list | None` 或允许序列化为 JSON 检查大小限制即可 |

---

### Minor 级

| # | 问题 | 文件:行号 | 攻击面 | 修复建议 |
|---|------|----------|--------|---------|
| m1 | **MCP auth_token 非恒定时间比较** — `req_token == auth_token` | `mcp/server.py:60` | 理论上可被时序攻击探测 token 长度或前缀。攻击面受限于本地 stdio 通信，实际利用难度极高 | 使用 `hmac.compare_digest()` 或 `secrets.compare_digest()` |
| m2 | **MemoryStore 会话内容明文存储** — 对话记录以 JSON 明文写入磁盘 | `memory/memory_store.py:73-82` (flush 写入 metadata JSON) | 用户对话、LLM 回复、工具执行结果均以明文 JSON 存储。若磁盘被第三方访问，可还原完整对话历史 | 考虑对记忆文件进行应用级加密（或至少让用户知情并有选择退出选项） |
| m3 | **MCP Server stdin 读取无长度限制** — `for line in sys.stdin:` 逐行读取但未限制单行最大长度 | `mcp/server.py:23` | 恶意本地进程可发送超大 JSON-RPC 消息（如数 GB）导致内存耗尽 | 读取前检查行长度上限（如 10MB），超限时断开连接 |
| m4 | **错误消息可能泄露文件路径** — 部分异常处理中 exc 信息可能包含绝对路径 | 多处 (`chat_widget.py:738`, `agent_worker.py:60` 等) | 若异常信息中包含本地绝对文件路径，通过工具返回或日志输出暴露给 LLM 或日志文件，间接泄露环境信息 | 在 `ToolResult.fail(str(exc))` 前过滤 exc 中的路径信息，或统一通过日志记录 exc 详情但不返回给 LLM/tool 消费者 |
| m5 | **`ParatranzParser._parse_zip` 重复打开 zip 文件** — `zipfile.ZipFile(path, "r")` 在方法内被调用两次 | `file_parser/paratranz_parser.py:37, 45` | 资源泄漏（非安全漏洞，但不良实践）。第一次打开读取内容后关闭（via `with`），第二次打开仅用于 `namelist()` 元数据 | 缓存第一次打开的 `namelist()` 结果，或在上下文管理器内一次性完成所有操作 |
| m6 | **Skill prompt_template 无长度限制** — 从 TOML 加载的 `prompt_template` 可任意长，直接注入 system prompt | `skills/skill_loader.py:56`, `skills/skill_executor.py:18-20` | 若恶意 TOML 文件被放入 skills 目录（需文件系统写权限），可注入超长 prompt 引发 token 浪费或 prompt 注入。攻击前提是敌手有文件系统写权限 | 为 `prompt_template` 添加长度上限（如 4096 字符），超过则截断并警告 |
| m7 | **System prompt 工具 schema 包含完整参数信息** — `build_tool_schema_for_prompt` 将全部工具参数信息发送给外部 LLM 提供商 | `prompts.py:78-79`, `tool_registry.py:65-79` | 工具内部数据结构（参数名、类型）暴露给第三方 LLM 服务。对使用外部 API 的用户而言属于信息泄露，但用户自行选择的 LLM 提供商 | 可接受风险（用户选择自己的 LLM 提供商），但应在文档中提及此行为 |

---

## 护栏覆盖率矩阵

表格标记说明：
- `+` = 护栏在此路径生效
- `-` = 护栏未在此路径生效（或绕过）
- `N/A` = 不适用

### 执行路径 x 护栏类型

| 执行路径 | PermissionGuard | InputValidationGuard | OutputValidationGuard | 工具级路径校验 |
|----------|:---:|:---:|:---:|:---:|
| **GUI ReAct 模式** (`_on_tool_executed` → `execute_with_guardrails`) | + | +(1) | +(1) | +(2) |
| **GUI Plan 模式** (`ExecutionEngine._run_single` → before_execute) | + | + | + | +(2) |
| **GUI Auto 模式** (`_auto_execute_steps` → `_on_tool_executed`) | +(3) | +(1) | +(1) | +(2) |
| **Agent Worker** (`AgentWorker.run` → `execute_with_guardrails`) | + | + | + | +(2) |
| **MCP 通道** (`MCPAdapter.call_tool` → `execute_with_guardrails`) | +(4) | + | + | +(2) |
| **ExecutionEngine Graph** (`execute_graph` → `_run_single`) | + | + | + | +(2) |
| **直接调用 v1 工具** (`tool_registry.ToolRegistry.get().execute()`) | -(5) | -(5) | -(5) | +(6) |

**注释**:
1. GUI 通道中，Input/OutputValidationGuard 是否生效取决于用户配置 `guardrails_enable_input_validation` / `guardrails_enable_output_validation`。详见 `_ensure_middlewares` (chat_widget.py:504-527)。PermissionGuard 始终生效。
2. 工具级路径校验指 `tool_writer.py:_validate_output_path` 及 v1 工具的同等调用。此校验位于工具函数体内，无论护栏状态均执行。
3. Auto 模式下，`_auto_execute_steps` 会检查 admin/require_confirmation 工具并回退到手动确认卡片，不会强制执行。需确认的工具最终仍走 `_on_tool_executed`。
4. MCP 通道中，PermissionGuard 的 `admin_confirm_required` / `write_confirm_required` 阻断无法通过 HITL 解除，导致 admin 工具即使在 whitelist 中也无法执行（见 Major M3）。
5. 若绕过 `execute_with_guardrails` 直接调用 `ToolSpec.execute`，三个护栏均被完全绕过。当前代码路径中未发现此类调用，但注册表接口暴露了直接调用能力。
6. v1 废弃工具的写回/导出函数（`_tool_write_back`, `_tool_export_json`）内部调用了 `_validate_output_path`，即使护栏被绕过也有工具级防护。

### 写入路径 x 路径遍历防护

| 写入工具 | InputValidationGuard (路径遍历 key 检查) | 工具级 `_validate_output_path` | 扩展名白名单 |
|----------|:---:|:---:|:---:|
| `write_to_esp` (tool_writer.py) | +(key=`path`) | + | - |
| `write_to_eet` (tool_writer.py) | +(key=`path`) | + | - |
| `write_to_xt` (tool_writer.py) | +(key=`path`) | + | - |
| `write_to_strings` (tool_writer.py) | +(key=`path`, `output_dir`) | + | - |
| `write_back` v1 废弃 | +(key=`target_path`) | + | +(.esp/.esm/.esl/.xml/.strings) |
| `export_json` v1 废弃 | +(key=`output_path`) | + | - |

---

## 安全维度评分

**总分: 48 / 60**

### 扣分明细

| 扣分项 | 扣分 | 说明 |
|--------|:---:|------|
| C1: MCP 默认无认证运行 | -4 | Critical — 可导致本地非授权工具调用 |
| C2: API Key 明文存储 | -3 | Critical — 敏感凭据明文暴露 |
| M1: Markdown 链接协议未校验 | -2 | Major — 潜在 XSS 面 |
| M2: 路径遍历 key 白名单不完整 | -1 | Major — 防御深度缺失 |
| M3: MCP admin whitelist 实质无效 | -1 | Major — 配置误导 |
| M5: OutputValidation 类型过严 | -0.5 | Major — 潜在功能阻断 |
| M4: 上传文件无大小限制 | -0.5 | Major — 潜在 DoS |

**加分项** (已体现于总分):
- PermissionGuard 在所有 GUI 和 MCP 路径中**始终生效**（不可被用户配置关闭）
- 三护栏 (`PermissionGuard` + `InputValidationGuard` + `OutputValidationGuard`) 在 `execute_with_guardrails` 统一入口处形成完整洋葱模型
- 工具级路径校验 (`_validate_output_path`) 提供深度防御，即使 InputValidationGuard 被用户关闭，writer 工具仍有文件级保护
- `ExecutionContext.__getattr__` 代理确保 v1/v2 工具无缝兼容
- `_checkpoint_path` 使用 `re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)` 防止路径注入
- 条件求值 (`_eval_ast_node`) 使用 AST 白名单模式，不允许任意代码执行
- `_tool_get_translation_config` 仅返回 `api_key_configured` 布尔值，不暴露实际 key
- `_tool_get_app_state` 仅暴露文件名 (`os.path.basename`)，不暴露绝对路径
- ContextBuilder 仅注入文件摘要信息，不注入原始上传文件内容

---

## 附录: 已审查文件清单

| 文件 | 行数 | 审查要点 |
|------|:---:|---------|
| `guardrails/permission.py` | 36 | admin/write/read 三级权限模型 |
| `guardrails/input_validator.py` | 102 | 注入检测 + 路径遍历 + 大小限制 |
| `guardrails/output_validator.py` | 92 | 脱敏 + 大小截断 + 递归清洗 |
| `guardrails/base.py` | 22 | GuardResult / GuardMiddleware 接口 |
| `tools/base.py` | 344 | execute_with_guardrails / ExecutionContext |
| `tools/tool_writer.py` | 129 | 写回工具 + _validate_output_path |
| `tools/tool_translator.py` | 463 | 翻译任务 + 配置暴露 |
| `tools/tool_default.py` | 195 | 状态查询 + 路径掩盖 |
| `tools/tool_v1.py` | 145 | 废弃工具 + 扩展名白名单 |
| `tool_registry.py` | 149 | ToolSpec / 注册表 / prompt schema |
| `execution_engine.py` | 494 | Plan/Graph 执行 + AST 条件 + checkpoint |
| `mcp/server.py` | 78 | JSON-RPC + 认证 |
| `mcp/adapter.py` | 69 | 工具暴露策略 + MCP→护栏桥接 |
| `agents/agent_worker.py` | 63 | QThread + 护栏调用 |
| `context_builder.py` | 63 | 上下文注入 + 文件摘要 |
| `prompts.py` | 79 | System prompt 模板 |
| `conversation_manager.py` | 95 | 消息轮次管理 + 截断 |
| `chat_worker.py` | 62 | LLM 流式调用 + 取消 |
| `memory/memory_store.py` | 252 | 记忆 CRUD + LRU + 异步刷盘 |
| `file_parser/base.py` | 43 | 解析器基类 |
| `file_parser/text_parser.py` | 104 | Excel/CSV/MD/TXT/JSON |
| `file_parser/binary_parser.py` | 51 | PDF/DOCX |
| `file_parser/paratranz_parser.py` | 46 | ParaTranz JSON/ZIP |
| `skills/skill_loader.py` | 74 | TOML Skill 定义 |
| `skills/skill_executor.py` | 27 | Skill prompt 注入 |
| `skills/skill_registry.py` | 44 | Skill 注册热加载 |
| `observability/collector.py` | 95 | 对话追踪 + trace 存储 |
| `config/llm.py` | 250 | LLM/Guardrails/MCP 配置持久化 |
| `infra/llm_client.py` | 179 | OpenAI/Anthropic 客户端 |
| `infra/markdown_renderer.py` | 394 | Markdown→QWidget 渲染 |
| `ui/tools/smart_assistant/chat_widget.py` | 824 | 聊天主界面 + 护栏配置 |
| `ui/tools/smart_assistant/message_bubble.py` | 96 | 消息气泡 + Markdown 渲染 |
| `ui/tools/smart_assistant/panel.py` | 74 | 面板生命周期 + 资源清理 |

**总计审查文件: 32 个，约 4600 行代码**

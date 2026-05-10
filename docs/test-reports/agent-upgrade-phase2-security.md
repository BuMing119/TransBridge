# 安全审查报告

> 审查目标: TransBridge FR7.13 Phase 2 后端代码
> 审查日期: 2026-05-10
> 审查人: QA 安全审查员
> 审查范围: execution_engine.py, guardrails/*, mcp/adapter.py, graph_types.py

---

## 发现的问题

| 严重级别 | 问题 | 位置 | 修复建议 |
|---------|------|------|---------|
| **高** | Checkpoint 路径穿越：graph_id 直接拼入文件路径，无校验 | `execution_engine.py:437-440` `_checkpoint_path` | 使用 `re.sub(r'[^a-zA-Z0-9_-]', '_', graph_id)` 清理非法字符，或校验 graph_id 不含 `..` 和 `/`、`\` |
| **高** | eval 沙箱可绕过：`__builtins__: None` 无法阻止对象内省链绕过（如 `().__class__.__bases__[0].__subclasses__()`） | `execution_engine.py:384-398` `_eval_condition` | 彻底放弃 eval，改用 AST 白名单解析器或受限表达式引擎（如 `simpleeval`）；或将 results/result 替换为仅含基础类型的 dict 代理 |
| **中** | PermissionGuard 与 ExecutionEngine 的 namespace 查找不一致：PermissionGuard 用 `ToolRegistry.get(tool_name)`（无 namespace），执行引擎用 `ToolRegistry.get(tool_name, namespace=...)`。同名工具在不同命名空间权限可能被绕过 | `guardrails/permission.py:17` vs `execution_engine.py:125` | PermissionGuard 应接受 namespace 参数，并使用与执行引擎一致的命名空间查找方式 |
| **中** | 写入操作默认无需确认：`_write_require_confirm=False` 且当前工具均未设 `require_confirmation=True`，导致 write 级工具（如 translate_entries, export_json）静默执行 | `guardrails/permission.py:26-28` | 将 `_write_require_confirm` 默认改为 `True`，或为现有 write 工具设置 `require_confirmation=True` |
| **中** | 命令注入检测缺少 `$()` 命令替换、`&&`/`||` 链式执行、换行绕过（`%0a`） | `guardrails/input_validator.py:8-15` `_INJECTION_PATTERNS` | 增加 `\$\(.*\)`、`&&\s*\w`、`\|\|\s*\w` 正则；增加 `[\r\n]` 换行注入检测 |
| **中** | XSS 检测仅覆盖 `onerror` 事件，缺少 `onload`、`onclick`、`onmouseover`、`onfocus`、`<svg/onload` 等常见注入向量 | `guardrails/input_validator.py:11` | 增补通用事件处理器正则 `on\w+\s*=` 或关键事件列表 |
| **中** | SQL 注入检测缺少无引号变体（`OR 1=1`、`admin'--`）、UNION SELECT、注释符绕过（`/**/`）、时间盲注（`SLEEP`、`BENCHMARK`） | `guardrails/input_validator.py:9` | 增加 `UNION\s+SELECT`、`--\s*$`、`\/\*.*\*\/`、`\bOR\b.*=` 等模式 |
| **低** | 输出脱敏未递归处理 list 中的 dict/str：`_redact_dict` 遇到 list 值直接透传，不做脱敏 | `guardrails/output_validator.py:52-63` `_redact_dict` | 增加 `elif isinstance(v, list): result[k] = self._redact_list(v)` 方法 |
| **低** | 敏感信息正则缺少 Google API Key（`AIza...`）、AWS Key（`AKIA...`）、JWT（`eyJ...`）、PEM 私钥、GitHub Token、数据库连接串 | `guardrails/output_validator.py:9-13` `_SENSITIVE_PATTERNS` | 增补常用云服务密钥正则，至少覆盖 Google/AWS/GitHub |
| **低** | `_safe_serialize` 对 dict 只做类型检查不做值递归清洗：若 dict 内嵌套非 JSON 类型会导致 checkpoint 静默存储失败 | `execution_engine.py:442-449` `_safe_serialize` | 递归遍历 dict/list 并对非基础类型值执行 `str(v)[:200]` |

---

## 审查结论

### 1. eval 安全性: FAIL

**理由**: `_eval_condition` 使用 `eval(str(condition), {"__builtins__": None, ...})` 试图构建沙箱，但 Python eval 的 `__builtins__: None` 是已知可绕过的防护。通过对象内省链（dunder 属性遍历），攻击者可访问 `__subclasses__()`、`__import__` 等敏感能力，从而执行任意代码。

**证据 — 可绕过 payload 示例**:
```
True.__class__.__bases__[0].__subclasses__()[<index>].__init__.__globals__['sys'].modules['os'].system('calc')
```

**风险上下文**: 条件表达式来自 `ConditionNode.condition` 和 `LoopNode.exit_condition`（在 `graph_types.py` 中定义为空字符串默认值）。若条件由 LLM 生成的计划注入或用户可见的图配置传入，则存在远程代码执行风险。当前条件下，条件主要由内部规划器生成，攻击面较小但沙箱本身不可靠。

**建议**: 使用 AST 白名单解析（如 `ast.parse` + `ast.NodeVisitor` 仅允许 `Compare`、`BoolOp`、`Name`、`Constant`、`Subscript`、`Call`(仅限 `get`/`[]`) 等安全节点），或接入成熟的受限表达式库（如 `simpleeval`）。最低限度应将 `results`/`result` 包装为只读代理，禁止属性遍历。

---

### 2. 注入检测: WARN

**理由**: 具备基础防护但覆盖不完整。正则可被多种常见手段绕过：

- **SQL 注入**: 未检测 `OR 1=1`、`UNION SELECT`、`admin'--`、`/**/` 注释绕过、`SLEEP()`/`BENCHMARK()` 时间盲注。且要求 payload 必须以 `'` 开头后跟 `;`，错过大量变体。
- **XSS**: 仅检测 `<script>` 标签和 `onerror=` 事件。缺少 `onload`、`onclick`、`onmouseover`、`onfocus` 等十余种事件处理器，也未检测 `<svg/onload`、`javascript:` 伪协议、HTML 实体编码绕过（`&#x3C;`）。
- **命令注入**: 缺少 `$()` 命令替换、`&&`/`||` 链式执行、`\n`/`%0a` 换行分隔绕过。管道检测仅覆盖 `cat`/`rm`/`bash`，遗漏 `nc`、`python`、`perl` 等。

**注意**: 注入检测在此项目中的实际风险取决于 `args` 的来源。如果工具参数主要由内部 LLM 与系统生成（而非直接来自外部用户输入），则风险实际上限较低。但作为护栏层，其自身健壮性不足。

**建议**: 补充上述缺失模式。可参考 OWASP 的 Injection Prevention Cheat Sheet 作为最低覆盖基线。

---

### 3. 敏感信息脱敏: WARN

**理由**: 具备 OpenAI/Anthropic API Key 和 Bearer Token 的脱敏，但缺失多个关键场景：

- **未覆盖的密钥类型**: Google API Key (`AIza...`)、AWS Access Key (`AKIA...`)、GitHub Token (`ghp_...`/`github_pat_...`)、JWT Token (`eyJ...`三段式)、PEM 私钥（`-----BEGIN.*PRIVATE KEY-----`）。
- **未覆盖的数据格式**: 数据库连接串（`mongodb://user:pass@host`）、通用密码/secret 字段（`password=`、`secret=`）。
- **list 递归脱敏缺失**: `_redact_dict` 对 `isinstance(v, list)` 直接透传，若输出数据包含 `{"items": [{"api_key": "sk-..."}]}` 结构，内层不会被脱敏。`after_execute` 中对顶层 `result.data` 调用 `_redact_dict` 而非更通用的递归方法。

**建议**: 扩充 `_SENSITIVE_PATTERNS` 至少覆盖 Google/AWS/GitHub 三种主流密钥格式；为 `_redact_dict` 增加 list 分支处理。

---

### 4. 权限绕过风险: WARN

**理由**: 存在一个架构性问题和一个配置风险：

**问题 A — namespace 查找不一致**: 
- 执行引擎 `_run_single`（第 125 行）使用 `ToolRegistry.get(tool_name, namespace=agent_namespace)` 按 agent 命名空间查找工具。
- PermissionGuard（第 17 行）使用 `ToolRegistry.get(tool_name)` **不带 namespace**，遍历所有命名空间取第一个匹配。
- 若同一工具名在两个命名空间注册了不同权限级别（如 `translator` 命名空间中权限为 `read`，`admin` 命名空间中为 `admin`），PermissionGuard 可能允许但执行引擎可能拒绝，或反之。
- 当前注册的工具名唯一，暂无实际可利用场景，但这是结构缺陷。

**问题 B — write 操作静默执行**: `PermissionGuard.__init__` 默认 `_write_require_confirm=False`，且现有 2 个 write 工具均未设置 `require_confirmation=True`。这意味着 `translate_entries`（AI 翻译）和 `export_json`（导出 JSON）将**不经用户确认直接执行**。虽然护栏层仍会检查注入和权限，但缺少人机确认环节。

**建议**: 
- PermissionGuard 应接受 namespace 参数并与执行引擎保持一致。
- 考虑将 `_write_require_confirm` 默认改为 `True`，或至少为 `translate_entries` 设置 `require_confirmation=True`（因为涉及调用外部 LLM API 产生费用）。

---

### 5. MCP 安全约束: OK

**理由**: `MCPAdapter._is_exposed` 逻辑正确,审查要点全部通过：

- **admin 白名单为空时**: `self._admin_whitelist = []`，对任意 admin 工具 `spec.name in []` 恒为 `False`，**正确拒绝所有 admin 工具**。
- **write_policy=deny 时**: `self._write_policy = "deny"` 为默认值，对任意 write 工具返回 `False`，**正确拒绝所有 write 工具**。注意：此默认行为意味着 MCP 协议层面默认只暴露 read 工具，是一个安全保守的设计。
- **权限 fallback**: `getattr(spec, 'permission', 'read')` — 未设 permission 的工具按 read 处理，合理且保守。
- **无逻辑漏洞**: admin→whitelist→deny, write+deny→deny, 其他→allow 的三分支无 fall-through 或覆盖关系问题。`list_tools` 和 `call_tool` 的过滤逻辑一致。

**小建议**: admin 白名单解析使用 `cfg.get("admin_tool_whitelist", "").split(",")` — 如果配置值为空字符串，会得到 `[""]` 而非 `[]`。当前通过 `if t.strip()` 过滤掉了空字符串，处理正确，但值得在代码中加注释说明以防止将来重构引入 bug。

---

### 6. Checkpoint 序列化安全: FAIL

**理由**: 存在一个高严重性路径穿越漏洞和一个低严重性健壮性问题。

**漏洞 A — graph_id 路径穿越 (HIGH)**:
`_checkpoint_path`（第 437-440 行）将 `graph_id` 直接拼接到文件路径中：
```python
return Path(project_dir) / "checkpoints" / f"{graph_id}.json"
```
`graph_id` 来自 `GraphSpec.graph_id`，对包含 `..`、`/`、`\` 的值不做任何过滤。攻击者可构造 `graph_id = "../../AppData/Roaming/exploit"` 使 checkpoint 写入（或读取）项目目录之外的位置。同时 `path.parent.mkdir(parents=True, exist_ok=True)` 会自动创建任意父目录。

**攻击场景**:
1. 写入任意 `.json` 文件到文件系统（覆盖配置文件、注入恶意 JSON 被其他组件加载）
2. 读取任意 `.json` 文件（若 `_load_checkpoint` 被调用且目标路径存在有效 JSON）
3. 通过创建大量目录消耗磁盘空间

**问题 B — _safe_serialize 不递归 (LOW)**:
`_safe_serialize`（第 442-449 行）对 dict/list 值仅检查顶级类型并原样返回，不递归清理嵌套值。若 `StepResult.data` 包含嵌套的非 JSON 类型（如 datetime、自定义对象），`json.dumps` 会抛出 TypeError，导致 checkpoint **静默存储失败**（外层 `except Exception: pass`）。这不会造成安全漏洞但可能丢失执行进度。

**建议**:
- 对 `graph_id` 进行路径安全校验：使用 `re.sub(r'[^a-zA-Z0-9_.-]', '_', graph_id)` 清洗，或显式检查不包含 `os.sep`、`..`、`/`、`\`。
- 将 `_safe_serialize` 改为递归版本，对嵌套结构中的非基础类型值执行 `str(v)[:200]`。

---

## 综合评级

| 维度 | 评级 | 关键风险 |
|------|------|---------|
| eval 安全性 | **FAIL** | Python eval 沙箱可绕过，存在 RCE 风险 |
| 注入检测 | **WARN** | 基础覆盖存在，缺失多种常见绕过向量 |
| 敏感信息脱敏 | **WARN** | 仅覆盖 OpenAI/Anthropic/Bearer，遗漏主流云密钥和 list 递归 |
| 权限绕过风险 | **WARN** | namespace 查找不一致 + write 静默执行 |
| MCP 安全约束 | **OK** | 逻辑正确，白名单和 deny 策略有效 |
| Checkpoint 序列化 | **FAIL** | graph_id 路径穿越可写任意 .json 文件 |

**总体结论**: 框架安全架构方向正确（多层护栏 + 白名单 + 确认机制），但存在两个必须修复的高危漏洞（eval 沙箱逃逸、checkpoint 路径穿越）和多个中等风险的检测缺失。建议在 Phase 2 上线前至少修复两个 FAIL 项。

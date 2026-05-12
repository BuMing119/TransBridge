# Story 03: 安全加固

**所属方案**: `plans/smart-assistant-qa-fix/plan.md`
**技术模块**: `smart_assistant/`（context_builder, guardrails, mcp）、`smart_assistant/tools/`（tool_v1）
**状态**: 已确认
**创建日期**: 2026-05-12
**覆盖问题**: C6（Prompt注入）、C7（MCP无认证）、C8（v1无路径校验）、M15（翻译条目注入向量）、M16（校验过于激进）

## 前置依赖

### 上游 Story
- **Story-01**（安全护栏修复）: 已完成 → 提供 `execute_with_guardrails` 修复版，本 Story 的路径校验可与护栏联动

### 引用的架构决策
- **ADR-012 §3.4**（MCP 安全约束）: MCP 通道中 admin 工具默认不暴露，可配置 token 认证
- **ADR-012 §1.2**（InputValidationGuard）: 注入检测模式（`../`、`__`、危险关键词）
- **ADR-008 §2**（Import 规范）: UI → backend 单向依赖

## 验收标准

- [ ] 用户上传文件内容不再直接拼接到系统提示词，改为存储为内存条目后注入 `{uploaded_doc_summary}` 占位符（仅文件名+字符数摘要）
- [ ] MCP stdio 通道支持可选的 token 认证（INI 配置 `[mcp] auth_token`，空则不启用）
- [ ] `tool_v1.py` 的 `_tool_write_back` 和 `_tool_export_json` 添加 `_validate_output_path` 检查（与 namespace 工具一致）
- [ ] 输入校验正则 `_INJECTION_PATTERNS` 放宽：允许合法 HTML 标签和 SQL-like 关键词
- [ ] 翻译条目原文作为间接注入向量的风险记录在已知限制中（不修复，通过输出护栏兜底）

## 数据流

### C6 — Prompt 注入修复

```
当前:
  chat_widget._on_file_uploaded(path)
    → FileParser.parse(path) → raw_text
    → context_builder.build_system_prompt(..., uploaded_files=[
           {"filename": name, "preview": raw_text[:200]}    ← 直接拼入 prompt ❌
       ])
    → system_prompt = f"... 参考文件:\n{preview_1}\n{preview_2}..."

修复后:
  chat_widget._on_file_uploaded(path)
    → FileParser.parse(path) → raw_text
    → memory_store.add(title=filename, content=raw_text)     ← 存储到 MemoryStore
    → context_builder.build_system_prompt(..., uploaded_docs=[
           {"filename": name, "char_count": len(raw_text)}   ← 仅摘要信息
       ])
    → system_prompt = f"... 参考文件: {name}({char_count}字符) — 使用 search_memory 检索内容"
```

### C7 — MCP 认证

```
MCP Client → stdio JSON-RPC → MCPServer._handle_request(request)
  ├── 检查 [mcp] auth_token 是否配置
  │   ├── 空 → 跳过认证（向后兼容）
  │   └── 已配置 → 验证 request 中的 Authorization 头
  │       ├── 匹配 → 继续处理
  │       └── 不匹配 → 返回 error -32001 "Unauthorized"
  └── 正常处理 tools/list / tools/call / initialize
```

### C8 — v1 路径校验

```
_tool_export_json(args, ctx)
  → output_path = args.get("output_path") or ctx.esp_path.parent / "export.json"
  → _validate_output_path(output_path)
      ├── 不允许写入系统目录
      ├── 扩展名白名单 (.json, .txt, .csv)
      └── 不允许路径遍历 (../)
  → 写入文件

_tool_write_back(args, ctx)
  → target = ctx.esp_path / ctx.eet_path / ctx.xt_path
  → _validate_output_path(target)
      ├── 必须是已知项目路径的子路径
      └── 扩展名必须是 .esp/.esm/.esl/.xml/.strings
  → 执行写回
```

## 关键接口

### C6: ContextBuilder 签名变更

```python
# src/transbridge/smart_assistant/context_builder.py

class ContextBuilder:
    def build_system_prompt(
        self,
        tools_schema: str,
        agent_spec=None,
        uploaded_docs: list[dict] | None = None,  # [{"filename": str, "char_count": int}]
        memory_entries: list[dict] | None = None,
    ) -> str:
        """构建系统提示词。uploaded_docs 仅含摘要信息，不包含文件内容。"""
        ...
```

### C7: MCP server token 验证

```python
# src/transbridge/smart_assistant/mcp/server.py

class MCPServer:
    def _authenticate(self, request: dict) -> bool:
        """验证请求的 Authorization token。未配置 auth_token 时直接放行。"""
        auth_token = self._config.get("auth_token", "").strip()
        if not auth_token:
            return True  # 未配置，向后兼容
        req_token = request.get("params", {}).get("_meta", {}).get("authorization", "")
        return req_token == auth_token
```

### C8: 路径校验 (复用 namespace 工具已有函数)

```python
# src/transbridge/smart_assistant/tools/tool_v1.py

from src.transbridge.smart_assistant.tools.tool_writer import _validate_output_path

def _tool_export_json(args, ctx):
    output_path = Path(args.get("output_path", ctx.esp_path.parent / "export.json"))
    err = _validate_output_path(output_path, allowed_extensions={".json", ".txt", ".csv"})
    if err:
        return ToolResult.fail(f"路径校验失败: {err}")
    ...
```

## 实现步骤

### 步骤 1: C6 — 修复 Prompt 注入

**涉及文件**: `src/transbridge/smart_assistant/context_builder.py`（修改）、`src/transbridge/ui/tools/smart_assistant/chat_widget.py`（修改）

**实现要点**:
- `context_builder.py:46-48` — 移除 `raw_text[:200]` 直接拼接
- `build_system_prompt()` 的 `uploaded_docs` 参数改为接受摘要信息 `[{"filename": str, "char_count": int}]`
- 系统提示词模板中添加 `{uploaded_doc_summary}` 占位符
- 提示 LLM：使用 `search_memory` 工具检索上传文件的内容
- `chat_widget.py` — `_on_file_uploaded` 中将内容存入 MemoryStore

**边界条件**:
- 无上传文件 → `uploaded_docs=[]`，不渲染参考文件段
- 文件内容为空 → char_count=0，仍记录文件名
- MemoryStore 不可用 → 降级为仅文件名摘要（不含内容）

**测试策略**:
- 上传含恶意 prompt 的文件 → 验证系统提示词不含注入内容
- 上传正常文件 → 验证文件名+字符数出现在 prompt 中

---

### 步骤 2: C7 — MCP 添加 token 认证

**涉及文件**: `src/transbridge/smart_assistant/mcp/server.py`（修改）

**实现要点**:
- 在 `_handle_request()` 开头调用 `_authenticate(request)`
- `_authenticate` 从 `self._config.get("auth_token")` 读取
- auth_token 为空时放行（向后兼容）
- 认证失败返回 JSON-RPC error code -32001

**边界条件**:
- `[mcp]` INI 段不存在 → auth_token = ""，放行
- 请求不含 Authorization → 拒绝（若 auth_token 已配置）
- `tools/list` 是否也需要认证 → 是，所有 MCP 方法统一认证

**测试策略**:
- 配置 auth_token → 正确 token 通过 → 错误 token 拒绝
- 不配置 auth_token → 任何请求通过

---

### 步骤 3: C8 — v1 工具添加路径校验

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_v1.py`（修改）

**实现要点**:
- `_tool_export_json` 开头：调用 `_validate_output_path(output_path, {".json", ".txt", ".csv"})`
- `_tool_write_back` 开头：验证目标路径是已知项目路径的子路径，扩展名白名单
- 复用 namespace `tool_writer.py` 中已有的 `_validate_output_path` 函数

**边界条件**:
- ctx.esp_path 为 None → 拒绝，返回 ToolResult.fail
- 输出路径包含 `../` → 拒绝
- 输出路径指向系统目录 → 拒绝

**测试策略**:
- 合法路径 → 通过
- 含 `../` 的路径 → 拒绝
- 指向 `/etc/passwd` → 拒绝

---

### 步骤 4: M16 — 放宽输入校验正则

**涉及文件**: `src/transbridge/smart_assistant/guardrails/input_validator.py`（修改）

**实现要点**:
- `_INJECTION_PATTERNS` 中移除 SQL 关键词（SELECT/FROM/WHERE 等）
- 添加 HTML 标签白名单：`<font>`, `<b>`, `<i>`, `<u>`, `<br>`, `<p>`, `<h1>`-`<h6>`, `<div>`, `<span>`, `<img>`
- 仅保留真正危险的模式：`../`（路径遍历）、`__`（私有属性）、`eval(`、`exec(`、`import os`、`subprocess`

**边界条件**:
- 翻译文本 "SELECT * FROM items" → 不再被拒绝
- 翻译文本 "<font color='red'>警告</font>" → 不再被拒绝
- 含 `../` 的参数 → 仍然拒绝

**测试策略**:
- 合法游戏标记语言文本 → 通过
- 路径遍历 `../../../etc/passwd` → 拒绝
- 命令注入 `__import__('os')` → 拒绝

---

### 步骤 5: M15 — 记录已知限制

**涉及文件**: 无需代码修改，在 `docs/smart-assistant-knowledge-gaps.md` 中追加记录

**实现要点**:
- 添加 "已知安全限制" 章节
- 记录：翻译条目原文可能被恶意构造为 LLM 注入向量，当前通过 OutputValidationGuard 输出脱敏兜底
- 推荐：长期方案考虑在条目进入 LLM 上下文前进行语义级注入检测

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/context_builder.py` | 修改 | 移除 raw_text 直接拼接，改为摘要注入 |
| `src/transbridge/ui/tools/smart_assistant/chat_widget.py` | 修改 | `_on_file_uploaded` 将内容存入 MemoryStore |
| `src/transbridge/smart_assistant/mcp/server.py` | 修改 | 添加 `_authenticate()` + token 验证 |
| `src/transbridge/smart_assistant/tools/tool_v1.py` | 修改 | 添加路径校验 |
| `src/transbridge/smart_assistant/guardrails/input_validator.py` | 修改 | 放宽注入检测正则 |
| `docs/smart-assistant-knowledge-gaps.md` | 修改 | 追加 M15 已知限制 |

## 风险与注意事项

- **风险**: C6 修复后 LLM 无法直接看到上传文件内容 → 需 LLM 主动调用 `search_memory` 检索 → **缓解**: 系统提示词中明确说明"使用 search_memory 检索上传文件内容"
- **风险**: C7 token 认证增加 MCP 接入复杂度 → **缓解**: 默认不配置 token，仅在有安全需求的部署中启用
- **注意**: C8 的 `_validate_output_path` 从 `tool_writer.py` 导入，确保该函数是模块级函数（非类方法）
- **注意**: M16 放宽后仍需保留对真正危险模式的检测，不能完全移除注入检测

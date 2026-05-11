# Story 09: P1 翻译配置工具 (translator namespace)

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant/tools)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: 安全改造 — base_url自由输入→profile预设方案切换(H7用户方案) +_translation_scope正式化(E8)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult`
- Story 06 → `tool_translator.py` 模块骨架

### 跨 Plan 依赖
- `infra/config.py` → `LLMConfig` 类（读写 LLM 配置）

### 引用的架构决策
- ADR-012: read/write 权限

## 验收标准

- [ ] `get_translation_config` — 返回当前 LLM 配置/术语库设置/后处理阶段/作用域/**当前 profile**，permission: read
- [ ] `set_translation_config` — 参数 `profile: str | None, model: str | None, temperature: float | None, max_tokens: int | None, term_db: str | None, post_process_stages: list[str] | None`，permission: write
  - **H7: profile 预设方案切换** — INI 新增 `[llm_profiles]` 节，Agent 只能通过 `profile` 参数切换预设端点，**不能自由输入 URL**
  - 移除 base_url 参数：校验 profile 是否在 `[llm_profiles]` 预设列表中，不在则拒绝
- [ ] `set_scope` — 操作 `ctx.translation_scope` 正式属性（**E8: 带类型校验的 property**），permission: write
- [ ] `get_scope_preview` — 返回当前作用域下匹配的条目统计，permission: read
- [ ] INI 配置文件新增 `[llm_profiles]` 节：`openai = https://api.openai.com/v1` 等预设端点
- [ ] 全部注册到 `translator` namespace

## 关键接口

```python
# tools/tool_translator.py 追加

def _tool_get_translation_config(args, ctx) -> ToolResult:
    llm_cfg = LLMConfig.load_from_file()
    return ToolResult.ok(data={
        "provider": llm_cfg.provider,
        "model": llm_cfg.model,
        "temperature": llm_cfg.temperature,
        "max_tokens": llm_cfg.max_tokens,
        "base_url": llm_cfg.base_url,  # 脱敏: 仅显示 hostname
    })

def _tool_set_translation_config(args, ctx) -> ToolResult:
    llm_cfg = LLMConfig.load_from_file()
    allowed_keys = {"model", "provider", "temperature", "max_tokens", "base_url"}
    for key, value in args.items():
        if key in allowed_keys:
            setattr(llm_cfg, key, value)
    llm_cfg.save_to_file()
    return ToolResult.ok("翻译配置已更新")

def _tool_set_scope(args, ctx) -> ToolResult:
    """设置作用域并返回预估匹配数。"""
    stages = args.get("stages", [])
    labels = args.get("labels", [])
    categories = args.get("categories", [])
    action = args.get("action", "translate")
    # 保存作用域到 ctx（或临时变量）
    ctx._translation_scope = {"stages": stages, "labels": labels, "categories": categories, "action": action}
    # 预估匹配数
    collection = _get_collection(ctx)
    matched = _count_scope_matches(collection, ctx._translation_scope)
    return ToolResult.ok(f"作用域匹配 {matched} 条", data={"matched_count": matched})

def _tool_get_scope_preview(args, ctx) -> ToolResult:
    scope = getattr(ctx, '_translation_scope', {})
    collection = _get_collection(ctx)
    if not collection: return ToolResult.fail("当前没有加载翻译集合")
    counts = _count_by_action(collection, scope)  # 按 translate/polish/skip 分组
    return ToolResult.ok(data=counts)
```

## 实现步骤

### 步骤 1: `get_translation_config` + `set_translation_config`

**涉及文件**: `tools/tool_translator.py`（追加）

**实现要点**:
- 读/写 `LLMConfig` INI 文件
- `set_translation_config` 仅允许白名单键：`model/provider/temperature/max_tokens/base_url`
- API key 不在 get 中返回完整值（脱敏为 `sk-...xxxx`）

**边界条件**:
- `set_translation_config` 传入未知 key → 忽略（不报错）
- INI 文件写入失败 → `ToolResult.fail("配置保存失败: {error}")`

---

### 步骤 2: `set_scope` + `get_scope_preview`

**涉及文件**: 同上

**实现要点**:
- 作用域数据暂存在 `ctx._translation_scope`（dict）
- `get_scope_preview` 遍历 collection 统计各 action 的匹配条目数
- 返回 `{translate: N, polish: M, skip: K, total: T}`

**边界条件**:
- 作用域未设置 → `get_scope_preview` 返回全条目统计
- scope 匹配 0 条 → 正常返回 0

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_translator.py` | 追加 | 4 个配置工具 + 注册 |

## 风险与注意事项

- **注意**: API key 在 `get_translation_config` 中需脱敏展示，防止 LLM 泄露到对话中
- **注意**: `_translation_scope` 为会话内临时数据，不持久化。`start_translation` 会自动读取它

# Story 04: 配置与工具前置条件

**所属方案**: `plans/smart-assistant-qa-fix/plan.md`
**技术模块**: `smart_assistant/tools/`（tool_translator, tool_default, base）、`smart_assistant/`（prompts, config）
**状态**: 已确认
**创建日期**: 2026-05-12
**覆盖问题**: C3（start_translation 无前置条件）、C4（get_translation_config 虚假属性）、C5（ParaTranz 配置不可见）、C10（ToolResult 无错误分类）、M4（无翻译工作流引导）、M5（无错误恢复策略）

## 前置依赖

### 上游 Story
- **Story-02**（异步通知）: 已完成 → `start_translation` 的通知机制已修复，本 Story 补充前置条件检查

### 引用的架构决策
- **ADR-012 §1.2**（InputValidationGuard）: 参数校验模式，前置条件检查可参考
- **ADR-010**（infra/ 共享基础设施）: LLMConfig 位于 infra/

## 验收标准

- [ ] `start_translation` 执行前检查：API Key 已配置、collection 非空、术语来源已设
- [ ] `get_translation_config` 返回真实的后处理配置（`pp_*` 前缀字段）和术语数据库信息
- [ ] `get_app_state` 返回 ParaTranz API 配置状态（token 是否已配置、URL）
- [ ] `ToolResult.fail()` 支持 `error_category`（network/auth/input/permission/internal）、`error_code`、`recovery_action` 字段
- [ ] 系统提示词包含正确的翻译工作流指导（确认配置→检查术语→设作用域→预览→翻译→轮询→检查→后处理→写回）
- [ ] 系统提示词包含错误恢复策略（网络故障可重试、权限拒绝不可重试等）

## 数据流

### C3: 前置条件检查

```
LLM 调用 start_translation
  → _tool_start_translation(args, ctx)
     ├── 1. 检查 API Key: LLMConfig.load_from_file().api_key 非空
     │     └── 空 → ToolResult.fail("API Key 未配置", category="config", recovery="请先在设置中配置 API Key")
     ├── 2. 检查 collection: ctx.collection 非空
     │     └── 空 → ToolResult.fail("未加载翻译集合", category="input")
     ├── 3. 检查术语数据库: term_db_path 存在且有数据
     │     └── 无 → ToolResult.ok(..., warnings=["术语数据库为空，翻译质量可能下降"])
     └── 4. 通过 → 启动后台线程
```

### C4: get_translation_config 修复

```
LLM 调用 get_translation_config
  → _tool_get_translation_config(args, ctx)
     ├── 读取 LLMConfig (api_key, base_url, model, provider)     ← 已有
     ├── 读取 pp_* 字段聚合为 post_process 配置                  ← NEW
     │     {enable_consistency_check, enable_format_validation,
     │      enable_quality_gate, enable_llm_refiner,
     │      enable_llm_polisher, polish_strength, ...}
     ├── 读取术语数据库文件信息                                   ← NEW
     │     {term_db_path, term_count, last_updated}
     └── 返回完整 ToolResult
```

### C10: ToolResult 错误分类

```python
# 使用示例
return ToolResult.fail(
    "API Key 未配置",
    error_category="config",
    error_code="API_KEY_MISSING",
    recovery_action="请在 AI 翻译设置中配置 API Key",
)
```

## 关键接口

### ToolResult 扩展

```python
# src/transbridge/smart_assistant/tools/base.py

@dataclass
class ToolResult:
    success: bool
    message: str
    data: dict | None = None
    failed_items: list | None = None
    # C10: 新增错误分类字段
    error_category: str | None = None   # "network" | "auth" | "input" | "permission" | "config" | "internal"
    error_code: str | None = None       # 如 "API_KEY_MISSING", "TIMEOUT", "PERMISSION_DENIED"
    recovery_action: str | None = None  # 建议的恢复操作，供 LLM 或用户参考
    warnings: list[str] | None = None   # 非致命警告列表

    @classmethod
    def fail(cls, message: str, *,
             error_category: str | None = None,
             error_code: str | None = None,
             recovery_action: str | None = None,
             failed_items: list | None = None) -> "ToolResult":
        return cls(success=False, message=message,
                   error_category=error_category,
                   error_code=error_code,
                   recovery_action=recovery_action,
                   failed_items=failed_items)

    @classmethod
    def ok(cls, message: str, *,
           data: dict | None = None,
           warnings: list[str] | None = None) -> "ToolResult":
        return cls(success=True, message=message,
                   data=data, warnings=warnings)
```

### get_translation_config 修复后的返回结构

```python
# tool_translator.py
def _tool_get_translation_config(args, ctx) -> ToolResult:
    """返回完整翻译配置，含后处理、术语、ParaTranz 状态。"""
    llm_cfg = LLMConfig.load_from_file()
    config = {
        "llm": {
            "provider": llm_cfg.provider,
            "model": llm_cfg.model,
            "api_key_configured": bool(llm_cfg.api_key),
            "base_url": llm_cfg.base_url,
        },
        # C4 FIX: 真实的后处理配置
        "post_process": {
            "enabled": getattr(llm_cfg, 'pp_enable', False),
            "consistency_check": getattr(llm_cfg, 'pp_enable_consistency_check', False),
            "format_validation": getattr(llm_cfg, 'pp_enable_format_validation', False),
            "quality_gate": getattr(llm_cfg, 'pp_enable_quality_gate', False),
            "llm_refiner": getattr(llm_cfg, 'pp_enable_llm_refiner', False),
            "llm_polisher": getattr(llm_cfg, 'pp_enable_llm_polisher', False),
            "polish_strength": getattr(llm_cfg, 'pp_polish_strength', 'moderate'),
        },
        # C4 FIX: 术语数据库信息
        "term_database": {
            "path": str(term_db_path) if term_db_path.exists() else None,
            "entry_count": term_count,
            "last_updated": last_updated,
        },
        # C5: ParaTranz 配置状态
        "paratranz": {
            "api_url": pt_cfg.api_url if pt_cfg else None,
            "token_configured": bool(pt_cfg.token) if pt_cfg else False,
        },
    }
    return ToolResult.ok("翻译配置", data=config)
```

## 实现步骤

### 步骤 1: C10 — ToolResult 添加错误分类字段

**涉及文件**: `src/transbridge/smart_assistant/tools/base.py`（修改）

**实现要点**:
- `ToolResult` dataclass 添加 `error_category`, `error_code`, `recovery_action`, `warnings` 字段
- `fail()` 类方法添加对应可选参数
- `ok()` 类方法添加 `warnings` 参数
- 向后兼容：所有字段默认 None，现有 `ToolResult.fail("msg")` 调用无需改动

**边界条件**:
- 所有现有调用不传新参数 → 字段为 None，不影响功能
- `warnings` 与 `fail()` 不应同时使用 → 约定：fail 时使用 `message` 说明错误，`warnings` 仅用于 ok

**测试策略**:
- 创建 `ToolResult.fail("x", error_category="auth")` → 验证字段正确
- 现有 `ToolResult.fail("x")` → 验证 error_category 为 None

---

### 步骤 2: C4+C5 — 修复配置暴露

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_translator.py`（修改）、`src/transbridge/smart_assistant/tools/tool_default.py`（修改）

**实现要点**:
- `_tool_get_translation_config`: 读取 `pp_*` 字段聚合为后处理配置；读取术语数据库文件统计；读取 ParaTranz 配置状态
- `_tool_get_app_state`: 扩展返回 `paratranz_configured`, `paratranz_api_url`
- 格式: INI 中的 `pp_enable_consistency_check` → LLM 看到的 `post_process.consistency_check`

**边界条件**:
- `pp_*` 字段不存在于 INI → `getattr(cfg, 'pp_enable', False)` 返回 False
- 术语数据库文件不存在 → `term_database.path: null`, `entry_count: 0`
- ParaTranz INI 段不存在 → `paratranz.token_configured: false`

---

### 步骤 3: C3 — start_translation 前置条件检查

**涉及文件**: `src/transbridge/smart_assistant/tools/tool_translator.py`（修改）

**实现要点**:
- 在参数校验后、创建线程前插入前置条件检查
- API Key 检查: `LLMConfig.load_from_file().api_key`
- 术语检查: 非致命，作为 warning 返回
- 后处理开关: 提示 LLM 当前后处理配置状态

**边界条件**:
- API Key 未配置 → `ToolResult.fail(error_category="config", error_code="API_KEY_MISSING")`
- 术语为空 → `ToolResult.ok(warnings=["术语数据库为空..."])`
- collection 为空 → `ToolResult.fail(error_category="input")`（已有）
- LLMConfig 文件不存在 → `ToolResult.fail(error_category="config")`

---

### 步骤 4: M4+M5 — 系统提示词补充

**涉及文件**: `src/transbridge/smart_assistant/prompts.py`（修改）

**实现要点**:
- 添加翻译工作流段：9 步标准工序
- 添加错误恢复策略段：按 error_category 分类指导
- 总增量估计 ~30 行 prompt 文本

**翻译工作流指导**:
```
翻译标准工作流:
1. 调用 get_translation_config 确认配置就绪
2. 调用 search_terms 检查相关术语
3. 调用 set_translation_scope 确定翻译范围
4. 调用 get_visible_entries 预览待翻译条目
5. 调用 start_translation 启动翻译
6. 翻译完成后系统会自动通知你结果
7. 调用 check_quality 检查翻译质量
8. 按需调用 start_polish 润色
9. 调用 write_to_esp/eet/xt 写回文件
```

**错误恢复策略指导**:
```
工具执行失败时的处理:
- error_category="network" → 可重试，建议等待后重试
- error_category="auth" / "config" → 不可重试，告知用户配置问题
- error_category="permission" → 不可重试，请用户手动授权
- error_category="input" → 调整参数后重试
- error_category="internal" → 不可重试，报告错误详情
```

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/smart_assistant/tools/base.py` | 修改 | ToolResult 添加 error_category 等字段 |
| `src/transbridge/smart_assistant/tools/tool_translator.py` | 修改 | C3 前置条件 + C4 配置修复 |
| `src/transbridge/smart_assistant/tools/tool_default.py` | 修改 | C5 ParaTranz 状态暴露 |
| `src/transbridge/smart_assistant/prompts.py` | 修改 | M4 翻译工作流 + M5 错误恢复 |

## 风险与注意事项

- **风险**: `ToolResult` dataclass 字段扩展后，pickle/序列化可能受影响（TaskManager 深拷贝 progress）→ **缓解**: 新字段为简单类型（str/None），pickle 兼容
- **风险**: `get_translation_config` 返回信息量增加 → 占用更多 LLM 上下文 token → **缓解**: 仅返回关键 boolean 和计数，不返回详细列表
- **注意**: `pp_*` 字段命名需与 `LLMConfig` 实际属性名一致，建议先 grep 确认 `[llm]` INI 段中的实际键名

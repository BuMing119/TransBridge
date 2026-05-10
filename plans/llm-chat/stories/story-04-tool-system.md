# Story 04: 工具系统

**所属方案**: `plans/llm-chat/plan.md`
**技术模块**: `src/transbridge/ui/tools/smart_assistant/` (新建)
**状态**: ✅ 已确认
**创建日期**: 2026-05-06

## 前置依赖

### 上游 Story
- Story-02: ExecutionEngine (调用 ToolRegistry 执行步骤)
- Story-03: ChatWidget (通过 ToolCard/PlanCard 触发工具执行)

### 跨 Plan 依赖
- `ai-translation/plan.md` → `AutoTranslator.translate()` — translate_entries 复用
- `ai-translation/plan.md` → `TermDatabaseManager.match_terms_enhanced()` — lookup_terms 复用
- `ai-post-process/plan.md` → `PostProcessor` — check_quality 复用
- `converter/plan.md` → `TranslationEntryCollection.to_json_file()` — export_json 复用
- `file-writing/plan.md` → `PluginWriter.write()` — write_back 复用
- `ui-workbench/plan.md` → `AppContext` — 工具执行时读取 ctx.collection

## 验收标准

- [ ] ToolRegistry 支持注册/查找/列出全部工具
- [ ] ToolSpec 包含 name/display_name/description/parameters/is_long_running/execute
- [ ] build_tool_schema_for_prompt() 生成 LLM 可用的工具描述
- [ ] lookup_terms 工具正确查询术语库并返回结果
- [ ] translate_entries 工具复用 AutoTranslator 执行翻译
- [ ] check_quality 工具复用 PostProcessor 执行质量检查
- [ ] get_collection_summary 工具返回当前集合统计
- [ ] export_json 工具导出当前集合到 JSON 文件
- [ ] write_back 工具复用 PluginWriter/EETWriter/XTWriter 写回译文

## 数据流

```
ExecutionEngine._run_single(step)
  │  tool_name = step["tool"]
  │  args = step["args"]
  ▼
ToolRegistry.get(tool_name)
  │  返回 ToolSpec 或 None
  ▼
ToolSpec.execute(args, ctx)
  │  args: {"keywords": [...], "filter": {...}}  (LLM 传入)
  │  ctx:   AppContext (只读访问集合状态)
  │
  ├─ lookup_terms:
  │    TermDatabaseManager.match_terms_enhanced(args["keywords"])
  │    → {"success": True, "message": "找到 N 个术语", "data": {term: translation}}
  │
  ├─ translate_entries:
  │    AutoTranslator(ctx.collection).translate(filter=args.get("filter"))
  │    → {"success": True, "message": "翻译完成: 15/15", "data": TranslationResult}
  │
  ├─ check_quality:
  │    PostProcessor(ctx.collection).process()
  │    → {"success": True, "message": "发现 N 处问题", "data": PostProcessReport}
  │
  ├─ get_collection_summary:
  │    ctx.collection → stats
  │    → {"success": True, "message": "总计 N 条, 已翻译 M 条", "data": {...}}
  │
  ├─ export_json:
  │    ctx.collection.to_json_file(path)
  │    → {"success": True, "message": "已导出到 data/export.json"}
  │
  └─ write_back:
       PluginWriter.write() / EETWriter / XTWriter
       → {"success": True, "message": "已写回 N 条"}
```

## 关键接口

### tool_registry.py

```python
@dataclass
class ToolSpec:
    name: str                          # 内部标识: "lookup_terms"
    display_name: str                  # 显示名: "查询术语"
    description: str                   # LLM 可用的功能描述
    parameters: dict                   # JSON Schema 子集: {"keywords": {"type": "list", "description": "..."}}
    is_long_running: bool = False     # True → 显示进度 UI
    execute: Callable[[dict, AppContext], dict]  # 执行函数

class ToolRegistry:
    _tools: dict[str, ToolSpec] = {}

    @classmethod
    def register(cls, spec: ToolSpec) -> None:
        """注册工具，同名覆盖"""

    @classmethod
    def get(cls, name: str) -> ToolSpec | None:
        """按名称查找工具"""

    @classmethod
    def build_tool_schema_for_prompt(cls) -> str:
        """生成 LLM 使用的工具描述文本"""
        # 格式：
        # - lookup_terms: 查询术语库中匹配的术语翻译
        #   参数: {"keywords": ["list of keywords"]}
```

### prompts.py

```python
# v1 工具注册 — 在模块导入时自动执行
def _register_v1_tools():
    ToolRegistry.register(ToolSpec(
        name="lookup_terms",
        display_name="查询术语",
        description="查询术语库中匹配的术语翻译，用于在翻译前获取标准译名",
        parameters={"keywords": {"type": "list", "description": "要查询的关键词列表"}},
        execute=_tool_lookup_terms
    ))
    # ... 其余 5 个工具
```

### v1 工具执行函数

```python
def _tool_lookup_terms(args: dict, ctx: AppContext) -> dict:
    """查询术语库"""
    keywords = args.get("keywords", [])
    terms = TermDatabaseManager().match_terms_enhanced(keywords)
    return {"success": True, "message": f"找到 {len(terms)} 个术语", "data": terms}

def _tool_translate_entries(args: dict, ctx: AppContext) -> dict:
    """复用 AutoTranslator 执行翻译"""
    collection = ctx.collection
    translator = AutoTranslator(collection, ctx.esp_path)
    result = translator.translate(filter=args.get("filter"))
    return {"success": True, "message": f"翻译完成: {result.success_count}/{result.total}", "data": result}

def _tool_check_quality(args: dict, ctx: AppContext) -> dict:
    """复用 PostProcessor 执行质量检查"""
    processor = PostProcessor(ctx.collection)
    report = processor.detect_only()  # 仅检测，不修复
    return {"success": True, "message": f"发现 {report.issue_count} 处问题", "data": report}

def _tool_get_collection_summary(args: dict, ctx: AppContext) -> dict:
    """返回当前集合统计"""
    c = ctx.collection
    total = len(c)
    translated = len([e for e in c if e.translation])
    return {"success": True, "message": f"总计 {total} 条，已翻译 {translated} 条", "data": {"total": total, "translated": translated}}

def _tool_export_json(args: dict, ctx: AppContext) -> dict:
    """导出集合到 JSON"""
    path = args.get("path", f"data/export_{ctx.esp_path.stem}.json")
    ctx.collection.to_json_file(path)
    return {"success": True, "message": f"已导出到 {path}", "data": {"path": path}}

def _tool_write_back(args: dict, ctx: AppContext) -> dict:
    """写回译文到插件/XML"""
    # 简化版：写回 ESP inline 模式
    writer = PluginWriter(ctx.collection, ctx.esp_path)
    result = writer.write()
    return {"success": result["esp_saved"], "message": f"写回完成", "data": result}
```

## 实现步骤

### 步骤 1: 创建 ToolRegistry + ToolSpec

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/tool_registry.py`（新建）

**实现要点**:
- ToolSpec 数据类：6 个字段
- ToolRegistry 类方法：register / get / build_tool_schema_for_prompt
- build_tool_schema_for_prompt 生成 LLM prompt 中的工具描述段

**边界条件**:
- 重复注册同名 → 覆盖（最后注册生效）
- 工具名不存在 → get() 返回 None

**测试策略**:
- 单测：注册 → 获取 → 确认 ToolSpec 完整
- 单测：重复注册 → 确认覆盖
- 单测：build_tool_schema_for_prompt 生成非空描述文本

### 步骤 2: 注册 v1 工具

**涉及文件**: `src/transbridge/ui/tools/smart_assistant/prompts.py`（新建）

**实现要点**:
- 在模块顶层调用 `_register_v1_tools()`
- 6 个工具按优先级注册：core(4) + extended(2)
- 每个工具函数是纯函数：`(args, ctx) -> dict`

**边界条件**:
- ctx.collection 为空 → get_collection_summary 返回 "总计 0 条"
- ctx.esp_path 为 None → export_json 使用默认路径
- translate_entries 传入空 filter → 翻译所有未翻译条目

**测试策略**:
- 集成测试：load 测试 ESP → get_collection_summary → 确认返回非零统计
- 集成测试：lookup_terms(["dragon"]) → 确认返回术语列表
- 手动验证：工具注册完成后 build_tool_schema_for_prompt 输出正确描述

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/transbridge/ui/tools/smart_assistant/tool_registry.py` | 新建 | ToolSpec + ToolRegistry |
| `src/transbridge/ui/tools/smart_assistant/prompts.py` | 新建 | v1 工具注册 + System Prompt 模板 |

## 风险与注意事项

- **工具执行阻塞 UI**: translate_entries 和 check_quality 是长耗时操作（`is_long_running=True`）→ ExecutionEngine 在线程池中执行，不阻塞 UI
- **工具返回数据过大**: 翻译结果可能包含大量数据 → ToolCard 和 LLM 上下文只使用 message 摘要，完整 data 通过信号传递给 UI
- **AutoTranslator 单实例冲突**: 如果用户同时手动触发翻译 → 需检查 AutoTranslator 是否支持并发（当前为单实例模式，工具调用时加锁排队）
- **PostProcessor 的 detect_only 模式**: check_quality 不应执行修复和润色（耗时太长），仅检测并返回问题列表 → 需在 PostProcessor 添加 `detect_only` 参数

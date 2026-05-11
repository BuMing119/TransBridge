# Story 12: P2 解析 + 写回 + 项目查询工具

**所属方案**: `plans/agent-tool-expansion/plan.md`
**技术模块**: backend (smart_assistant/tools)
**状态**: 已确认 (v2)
**创建日期**: 2026-05-11
**更新日期**: 2026-05-11（v2: parser权限write→read(H6) +文件扩展名白名单(E1) +路径遍历强化(E1)）

## 前置依赖

### 上游 Story
- Story 01 → `ToolResult` + 装饰器
- Story 02 → `TaskManager`
- Story 07 → `tool_default.py` 模块骨架

### 跨 Plan 依赖
- `parser/` → `EET_XmlParser`, `XT_XmlParser`, `SST_Parser`, `PluginParser`
- `writer/` → `PluginWriter`, EET/XT writer 类
- `persistence/` → `workspace.json` 读取

## 验收标准

- [ ] Parser namespace 6 工具：`parse_esp/eet/xt/sst` + `import_json/strings`，均为 write，path 可选
- [ ] Writer namespace 4 工具：`write_to_esp/eet/xt/strings`，均为 admin + require_confirmation
- [ ] Default namespace 2 工具：`list_local_projects` + `get_current_project`，均为 read
- [ ] `write_back` 标记 deprecated，保留转发到新的 writer 工具

## 关键接口

```python
# tools/tool_parser.py

def _tool_parse_esp(args, ctx) -> ToolResult:
    file_paths = args.get("file_paths")
    if not file_paths:
        return ToolResult.fail("未提供文件路径。请通过文件对话框选择 ESP 文件。")
    results = []
    for path in file_paths:
        plugin = PluginParser.parse(path)
        ctx.load_plugin(plugin)  # 加载到 AppContext
        results.append({"path": path, "entries": len(plugin.entries)})
    return ToolResult.ok(f"解析完成: {len(results)} 个文件", data={"results": results})

# tools/tool_writer.py

def _tool_write_to_esp(args, ctx) -> ToolResult:
    mode = args.get("mode", "localised")
    output_dir = args.get("output_dir")
    collection = _get_collection(ctx)
    writer = PluginWriter(ctx.active_slot.plugin, language=ctx.strings_lang or "english")
    writer.apply_collection(collection)
    if mode == "localised":
        result = writer.write_strings_only(output_dir)
    else:
        result = writer.write(ctx.esp_path)
    return ToolResult.ok(f"写回完成", data=result)
```

## 实现步骤

### 步骤 1: 创建 `tool_parser.py` — 6 个解析工具

**涉及文件**: `tools/tool_parser.py`（新建）

**实现要点**:
- 封装现有 parser 模块的各类解析器
- `path` 参数可选 — 不传时通过 HITL 文件选择（返回提示 + 等待用户通过 UI 选择）
- 解析结果通过 ctx 自动加载

**边界条件**:
- 文件不存在 → `ToolResult.fail("文件不存在: {path}")`
- 解析失败 → 跳过异常条目，返回部分结果 + 警告

---

### 步骤 2: 创建 `tool_writer.py` — 4 个写回工具

**涉及文件**: `tools/tool_writer.py`（新建）

**实现要点**:
- 封装现有 writer 模块
- 全部 `admin` 权限 + `require_confirmation: True`
- `write_to_esp`: inline/localised 双模式
- `write_to_eet/xt`: 支持更新源文件或新建
- `write_to_strings`: 纯 strings 输出

**边界条件**:
- 无源文件时写回 → `ToolResult.fail("未找到可写回的源文件")`
- 输出路径不存在 → 自动创建父目录

---

### 步骤 3: 追加项目查询工具 + write_back deprecated

**涉及文件**: `tools/tool_default.py`（追加）; `tool_registry.py`（修改）

**实现要点**:
- `list_local_projects`: 扫描 `data/projects/` 目录或读取 `workspace.json`
- `get_current_project`: 返回当前活跃项目详情
- `write_back` 注册时添加 `deprecated=True`，内部转发到 `write_to_*`

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `smart_assistant/tools/tool_parser.py` | 新建 | 6 个解析工具 |
| `smart_assistant/tools/tool_writer.py` | 新建 | 4 个写回工具 |
| `smart_assistant/tools/tool_default.py` | 追加 | list_local_projects + get_current_project |
| `smart_assistant/tool_registry.py` | 修改 | write_back deprecated 标记 |

## 风险与注意事项

- **注意**: Parser 工具的 path 可选导致执行路径分叉——有 path 直接执行，无 path 需 HITL。两种路径的返回值格式需一致
- **注意**: Writer admin 级工具在生产环境中应将 require_confirmation 设为 true（默认），开发/测试时可通过配置关闭
- **注意**: `write_back` 的 deprecated 转发需保持参数兼容——新工具的参数名可能不同（如 `write_to_esp` 用 `mode` 而非原来的 `target`）

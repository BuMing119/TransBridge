# Story 19: write_back 合并 (4→1)

**Epic**: agent-tool-expansion
**优先级**: P0
**净减**: -3 工具
**风险**: 中（LLM 可能选错 target 格式）
**依赖**: S16（注册样板改造后的 tool_writer.py）
**状态**: 已方案

## 范围

合并 `write_to_esp` / `write_to_eet` / `write_to_xt` / `write_to_strings` → `write_back`，使用 dispatch 表路由到现有实现。

## 验收标准

- [ ] `write_back` 注册到 `writer` namespace
- [ ] 参数 `target: str`（必传，enum: `esp`/`eet`/`xt`/`strings`）+ `path: str | None`（可选，不传使用已解析源路径）
- [ ] dispatch 表路由：
  ```python
  _WRITE_HANDLERS = {
      "esp": _write_to_esp_impl,
      "eet": _write_to_eet_impl,
      "xt": _write_to_xt_impl,
      "strings": _write_to_strings_impl,
  }
  ```
- [ ] 4 个现有实现重命名为 `_write_to_*_impl`（去掉 `@require_collection` 装饰器，改为 `write_back` 外层统一校验）
- [ ] `write_back` 外层统一处理 `@require_collection` 和参数校验
- [ ] 保留 `require_confirmation=True` + `permission="admin"`
- [ ] 回显确认时显示实际写入目标类型（如 "即将写入 ESP 文件: Dragonborn.esm"）
- [ ] 旧 4 个工具保留 deprecated wrapper，不注册到 ToolRegistry

## 实现步骤

1. 将 4 个现有函数重命名为 `_write_to_esp_impl` / `_write_to_eet_impl` / `_write_to_xt_impl` / `_write_to_strings_impl`，去掉各自装饰的 `@require_collection`（由外层统一处理）
2. 定义 `_WRITE_HANDLERS` dispatch 表
3. 实现 `_tool_write_back()`：
   - `@require_collection` 装饰
   - 校验 `target` 是否在 `_WRITE_HANDLERS` 中
   - 从 dispatch 表获取实现函数
   - 调用实现，传递 `args` 和统一获取的 `collection`
4. 编写 4 个 deprecated wrapper：转发到 `_tool_write_back({"target": "esp"}, ctx)` 等
5. 更新 `_PARAM_SCHEMAS` 和 `_register_writer_tools()`：注册 `write_back`，移除旧 4 个工具
6. 运行 writer 相关测试

## 涉及文件

- `tools/tool_writer.py`

## 参数设计

```python
_PARAM_SCHEMAS["write_back"] = {
    "target": {"type": "str", "required": True, "description": "写入目标格式: esp/eet/xt/strings"},
    "path": {"type": "str", "required": False, "description": "输出路径，不传则使用已解析的源文件路径"},
}
```

## 工具描述（关键——防止 LLM 误选）

```
何时用我: 用户要求将翻译结果写回/保存/导出到文件时使用。根据用户上下文中已加载的文件类型推断 target:
- 加载了 ESP 插件 → target="esp"
- 加载了 EET XML → target="eet"
- 加载了 XT XML → target="xt"
- 需要 .strings 本地化文件 → target="strings"
若无法确定 target，询问用户确认后再执行。
```

## 边界条件

- `target` 不在 `["esp", "eet", "xt", "strings"]` → 返回错误，列出合法值
- `target="esp"` 但当前无 ESP 插件加载 → 返回错误
- `target="eet"` 且不传 `path` 但无已解析 EET → 返回错误，要求传 path
- 所有 target 都需要 `@require_collection`（非空）

## 风险缓解

- 确认弹窗明确显示 "写入类型: ESP | 文件: Dragonborn.esm | 条目数: 1234"
- 系统 prompt 工具选择指南增加 writer 使用说明

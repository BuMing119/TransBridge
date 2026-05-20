# 001: Story 20 manage_entry_labels 合并 4→1

**日期**: 2026-05-18
**类型**: 改
**关联**: Epic: Agent 工具系统全面扩展 > Story 20: manage_entry_labels 合并 (4→1)

## 修改文件

### `src/transbridge/smart_assistant/tools/tool_editor.py` (改)
- **修改内容**:
  1. `_PARAM_SCHEMAS` 新增 `"manage_entry_labels"` 条目：`action`（create/assign/unassign/batch_assign 必填）、`name`（可选）、`color`（create 用）、`entry_ids`（assign/unassign 用）
  2. 新增 `_tool_manage_entry_labels()` 统一入口（~60 行）——`action` 参数路由，包含 4 种操作的完整逻辑：create（UUID 生成标签 ID + 写入 label_library）、assign（写入 entry_labels）、unassign（从 entry_labels 移除）、batch_assign（复用 filter_entries + 全量分配）。batch_assign 免除 `entry_ids` 参数
  3. 4 个旧函数转为 deprecated wrapper：`_tool_create_label`/`_tool_assign_label`/`_tool_remove_label`/`_tool_batch_assign_label`——各保留 `@validate_params` 装饰器，发出 `DeprecationWarning` 后转发到 `_tool_manage_entry_labels`
  4. `_register_editor_tools()` 注册表：4 旧条目（`create_label`/`assign_label`/`remove_label`/`batch_assign_label`）替换为 1 个 `manage_entry_labels`（permission: write, require_confirmation: True）。`list_labels` 保留不合并（只读工具）
- **原因**: Story 20（工具架构合并路线A，用户裁决包含 create_label）——4 个标签管理工具按操作类型分裂，合并到统一入口 `manage_entry_labels` 通过 `action` 枚举路由，消除碎片

## 测试结果

- 86 passed, 3 failed（全部为预存问题）
- DeprecationWarning 在 create_label/assign_label 旧测试中正常触发
- Editor namespace: 10→7 工具（-3），总计非废弃: 48→45（-3）

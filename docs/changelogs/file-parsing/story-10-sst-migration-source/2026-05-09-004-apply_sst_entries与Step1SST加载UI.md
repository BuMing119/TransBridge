# 004: apply_sst_entries 批量合并与 Step1 SST 加载 UI

**日期**: 2026-05-09
**类型**: 增/改
**关联**: Epic: 文件解析 > Story 10: SST 迁移源集成

## 修改文件

### `src/transbridge/converter/translation_entry_collection.py` (改)
- **修改内容**: 新增 `apply_sst_entries(sst_entries)` 方法。按 (form_id, index) 构建查找表，遍历集合条目提取 form_id+index 后在查找表中匹配，调用 `try_update_from_sst()` 更新译文。返回 `{matched, updated, skipped}` 统计。逻辑比 `apply_xt_entries()` 更简洁——SST 使用 form_id 直接匹配，无需 Phase 1 候选桶 + Phase 2 回退
- **原因**: `try_update_from_sst()` 仅在单条层面可用，集合需要批量合并入口以集成到解析和迁移源流程

### `src/transbridge/ui/context.py` (改)
- **修改内容**: `CollectionSlot` dataclass 新增 `sst_path: str | None = None` 字段，用于记录已加载的 SST 文件路径（与 eet_path/xt_path/strings_path 同模式）
- **原因**: SST 作为新的迁移源类型，需要在 slot 中记录路径以支持锁定（每种迁移源仅限一次）

### `src/transbridge/ui/workbench/step1.py` (改)
- **修改内容**:
  - 新增 SST 行 UI（`_sst_input` / `_sst_browse_btn` / `_sst_clear_btn`），放在"已翻译插件"和"Strings 目录"之间
  - 新增 `_browse_sst()` 方法：打开文件对话框选择 .sst 文件
  - `_run_parse_esp()` 签名增加 `sst_path` 参数，_do() 内解析 SST 并调用 `apply_sst_entries()` 合并译文
  - `_run_batch_parse_esp()` 同步增加 `sst_path` 参数和 `_batch_sst_path` 存储/清理
  - `_apply_migration_sources()` 增加 SST 处理分支，创建 slot 时写入 `sst_path`
  - `_update_migration_buttons()` 增加 SST 锁定逻辑（`sst_enabled`）
  - `_on_slot_selected()` / `_on_new_slot()` 增加 sst_input 加载/清空
- **原因**: 在 Step1 迁移源区域提供与 EET/XT/Strings 一致的 SST 加载入口，用户可在解析时或解析后追加 SST 译文

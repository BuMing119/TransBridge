# 按 Dynamic String Distributor 的字段规则导出

2026-11-22 · file-io-dialogs / Story 02 · [导出与独立 JSON 契约](../../../../plans/file-io-dialogs/plan.md)

## 变更

- `parser/plugin/dsd_subrecords.py` 从物理记录读取任务阶段、日志顺序、无符号任务目标编号和区域数据项位置。`plugin_with_context.py` 与 `plugin_string_with_context.py` 将 DSD 索引传给转换器，继续用既有插件定位写回原格式。没有日志文本的条目也占用该阶段的序号。
- `converter/translation_entry.py` 保留插件字段的零索引并委托 DSD 编解码。`dsd_entries.py` 根据来源记录还原字段，对不同条件或插件归属造成的键冲突分别保留词条。`dsd_metadata.py` 记录可选字段是否存在、条件顺序、显式空替换及索引来源。`translation_entry_collection.py` 的公开导出函数也使用相同规则。
- `application/projects/source_fields.py` 将这些 DSD 字段纳入工程持久化。`application/io/dsd_projection.py` 在有来源字节时取回对应记录的信息，在重开工程后读取已保存的定位，并从旧插件词条中恢复可证明的零索引。`json_export.py` 保存显式空替换，并为旧 DSD 工程提示可能缺少的可选字段。
- `application/io/migration_import.py` 按插件、记录、字段、索引及条件匹配新版 DSD 输出，保留按原文定位的旧任务日志。多个候选无法区分时跳过或报告冲突。
- `tests/parser/test_plugin_dsd_indices.py` 验证插件二进制中的阶段、序号、零索引及原生写回身份。`tests/converter/test_native_dsd_roundtrip.py` 验证条件、显式空值、可选字段、字段类型和公开转换接口。`tests/contracts/io/test_original_match_migration.py`、`test_dsd_saved_locators.py` 与 `tests/persistence/project_store/test_exchange_fidelity.py` 覆盖导回、旧定位和移除原始文件后的工程导出。
- 更新 [DSD 字段说明](../../../dev/dsd.md)、[数据结构](../../../dev/DATA_STRUCTURES.md)和[开发索引](../../../dev/INDEX.md)，以官方字段规则取代原有的仅按原文定位任务日志的说明。

## 验证

使用官方 `tests/generalTestAll.json` 实际导出两次，一次通过集合公开接口，一次经过工程保存、删除临时来源、重开及导出。重读输出并比较完整 JSON 数组，两条路径的 34 条记录、22 种字段类型、记录顺序与所有字段值均一致。[官方测试文件](https://github.com/SkyHorizon3/SSE-Dynamic-String-Distributor/blob/main/tests/generalTestAll.json)

## 兼容性

旧插件工程中缺少物理日志序号的任务日志仍可用已保存的原文导出。旧 DSD 工程未记录的条件或空替换无法从现有译文推断，需要重新打开原始 JSON。验证覆盖文件和工程导出，未运行 Skyrim / SKSE。

## 回归命令与结果

```powershell
uv run --offline --no-sync --no-cache pytest tests/contracts/io tests/converter tests/parser tests/writer tests/integration/publish tests/persistence/project_store/test_exchange_fidelity.py tests/ui/operations/test_export_json_dialog.py tests/ui/test_workbench_translation_import.py tests/ui/test_structured_migration_import.py tests/smart_assistant/tools/test_export_artifact_parity.py -q --tb=line -rs
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
```

pytest 通过 1579 项，跳过 3 项，退出码为 0。两项 EET 测试需要可选本地语料，一项发布测试需要 Windows 符号链接权限。Ruff 两项检查均通过。未运行与这次文件交换改动无关的全库测试。

# 词典按项目默认译名入库，套用时恢复正式正文的译名

2026-11-28 · Epic `translation-memory` · S11 保存、S12 套用 · [实施计划](../../../../plans/translation-memory/plan.md) · [FR15.1.7](../../../requirements.md#fr151-翻译词典)

方案 B 新翻的整句和人工润色可直接用于翻译记忆。例如 B 正文保存「动身去白漫城」，同一动作在词典里存入「动身去雪漫城」。在 B 里套用该词条时，写回「动身去白漫城」。查询显示词典实际存入的整句。显示预览和导出文件不会入库。默认译名由供稿工程决定。

## 转换和存储

- 新增 `application/terminology_profiles/dictionary_projection.py`，用正式方案固定修订与默认术语准备一次转换。扩充同目录 `projection.py` 和 `occurrence_matching.py` 的按出现位置匹配，供词典检查单侧术语、重复出现、重叠和相同位置有多个归属的情况。保留无关正文，遇到已知歧义整句跳过并记日志。未发布默认术语时，可使用该已发布方案明确登记的基础译名。转换在本地完成。
- `translation_memory/library.py` 的普通保存和版本保存共用入库转换，`library_matching.py` 将实际转换后的文本绑定到原记录。SQLite v3 仅在工程的入库检查点增加术语依据；已有词典可查询，首次写入时升级，历史译文原样保留。每条词典记录仍只有一份译文。方案或默认术语依据变化，会重读当前来源，避免沿用旧检查点。
- 新增 `application/projects/dictionary_profile_binding.py`。在 `dictionary_apply.py` 准备操作时固定目标工程与翻译版本，由 `bootstrap/terminology.py`、`terminology_storage.py` 和 `persistence/terminology/project_selection.py` 在后台读取正式关联及已发布修订。默认正文只查询其关联；读不到指定修订时停止本次词典操作。正式另存翻译版本时，项目默认快照使用健康的术语仓储，保留故障恢复模式的原有约束。
- `ui/coordinators/profile_variant_save.py`、`project_coordinator.py`、`ui/main_window.py`、`ui/shell/project_save.py` 和 `saved_dictionary.py` 在正式保存新方案并恢复其来源后，通过已有保存队列为原先选中的插件取得目标版本回执。手动命令和助手使用 `smart_assistant/tools/_dictionary_application.py` 的同一读写入口。
- `application/io/open_translation_file.py`、桌面的 `open_file_coordinator.py`、`parse_coordinator.py` 及助手 `_source_import.py` 使用目标版本和插件作用域。建项由 `application/projects/provisioning*.py`、`prepared_source_results.py`、`source_commands.py`、`source_terminology_scope.py` 和 bootstrap 接入，按新工程语言与开关，在显式导入译文后填充仍未翻译的主插件词条。基础库位置沿用同一应用配置。

## 验证

应用层测试覆盖两个转换方向、已登记和缺失的目标译名、保留整句措辞、插件优先级、跳过和重试、原有 SQLite 词典的只读访问和写入升级。工作台测试经过真实的分页工程、正式 B 版本保存、工程保存按钮及「套用词典」命令。新建工程另验证已有 B 工程处于活动状态、不同目标语言、显式导入译文和关闭自动套用的场景。

联合复验通过 420 项，产生 3 条 SWIG 依赖弃用警告。全仓源码、测试和新增基准脚本的 Ruff 检查及格式检查均通过。未运行全仓 pytest。

```powershell
uv run --offline --no-sync --no-cache pytest -q --tb=short tests/application/terminology_profiles tests/contracts/io/test_terminology_profile_write.py tests/ui/workbench/test_terminology_projection_consistency.py tests/ui/workbench/test_profile_variant_save.py tests/test_dictionary_library.py tests/application/projects/test_dictionary_apply.py tests/application/projects/test_dictionary_projection_storage.py tests/application/projects/test_save_projection_cost.py tests/ui/test_project_save_dictionary.py tests/ui/test_dictionary_commands.py tests/ui/test_dictionary_settings_menu.py tests/ui/test_dictionary_save_changes.py tests/ui/test_project_creation_reuse.py tests/persistence/terminology/test_effective_snapshot.py tests/contracts/io/test_open_translation_file.py tests/smart_assistant/tools/test_dictionary_upload_authority.py tests/smart_assistant/tools/test_file_import_parity.py tests/integration/gui/test_project_paged_workbench.py::test_workbench_batch_replace_and_save_only_read_selected_keys tests/integration/bootstrap/test_dictionary_profile_binding.py tests/application/projects/test_provisioning.py tests/persistence/v2/test_project_provisioning.py tests/persistence/v2/test_project_provisioning_import_formats.py tests/persistence/v2/test_project_provisioning_multiple_sources.py tests/persistence/v2/test_project_provisioning_xml.py
uv run --offline --no-sync --no-cache ruff check --no-cache src tests scripts/benchmark_dictionary_projection.py
uv run --offline --no-sync --no-cache ruff format --no-cache --check src tests scripts/benchmark_dictionary_projection.py
git -c core.safecrlf=false diff --check
```

## 10 万行分页工程的保存耗时

在 Windows 11 build 26300、Core i9-14900HX、Python 3.12.12、SQLite 3.50.4 上，用[可复用的基准脚本](../../../../scripts/benchmark_dictionary_projection.py)比较两份合成的单插件工程。A 使用默认译名，B 的每条正文都含一个要转换的地名；已发布词表含这一个术语。两个工程的 SQLite 文件各为 25,522,176 字节。在同一工作区（基于 `9ed5dafe`）进行五轮配对测量，轮流先测 A 或 B，每轮从空词典开始。计时从取得已提交工程的保存回执开始，包含生产术语关联与快照读取、分页读取、投影、词典 SQLite 提交。工程自身提交、Qt 事件和结果核对在计时之外。

- 首次入库，中位数 A 4.140 秒（3.614–4.282），B 8.633 秒（8.457–9.720）。两者均完整读取和写入 100,000 行，B 的五轮耗时均高于 A。
- 原样保存，中位数 A 2.666 毫秒（2.596–3.027），B 3.733 毫秒（3.501–3.932）。均读取 0 条工程词条、更新 0 条词典记录。
- 只改一句，中位数 A 5.671 毫秒（5.336–5.777），B 6.869 毫秒（5.630–7.340）。均按键读取并更新 1 条，两组范围重叠。

每轮逐行核对了 100,000 条词典记录的全部 15 个字段，两种路径的词典内容均与预期默认文本一致，工程正文分别保持 A、B 译名。数据用于衡量单个已登记地名在大量正文中重复出现的情况；大型术语表和未经登记的命名差异不在此项性能验证范围内。

```powershell
uv run --offline --no-sync --no-cache python scripts/benchmark_dictionary_projection.py --rows 100000 --repetitions 5 --sqlite-terminology
```

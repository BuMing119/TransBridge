# 修复工作台译文导入闪退与 ESP 译文提取

- 日期：2026-10-06
- Epic / Story：maintenance / translation-import
- 关联：沿用 [V3 权威工程 UI 缺口闭环](../../../../plans/v3-ui-gap-closure/plan.md) 的迁移草稿与统一提交边界。

## 问题与证据

用户加载原始 ESP 后，从工作台“导入已有译文…”按钮选择译文 ESP，程序闪退。日志中原始插件成功解析 2,340 条，但没有译文插件的解析开始记录；后续截图确认使用工作台按钮。

旧按钮实际上调用 JSON 专用逻辑，允许选择所有文件，并在启动局部 `ApiWorker` 后未保留引用。隔离进程复现 `QThread: Destroyed while thread '' is still running`；仅保留引用的对照正常退出。真正的 ESP 导入读取了空 `translation` 字段，而插件文字位于 `original`，导致导入 0 条。构造的真实 ESP 验证了这一独立问题。未获得朋友的原始/译文 ESP，未对其完整现场做实物复验。

## 文件变化

- 修改 [widget.py](../../../../src/transbridge/ui/workbench/widget.py)：工作台按钮发送 `SOURCE_MIGRATE`，复用菜单入口；删除旧 `_on_import_json` 整段重复处理和无所有者线程，更新格式提示。
- 修改 [migration_import.py](../../../../src/transbridge/application/io/migration_import.py)：支持插件草稿，复用 `SsePluginAdapter`，将插件正文映射为目标译文；失败、部分解析及多义匹配拒绝提交；相同原文不标为已翻译，准备阶段不修改目标。
- 修改 [parse_coordinator.py](../../../../src/transbridge/ui/coordinators/parse_coordinator.py)：ESP 进入原子迁移流程；worker 启动前加入窗口持有列表；打开对话框时绑定版本和集合，提交时拒绝变化后的目标；保留运行期间修订号/版本校验及失败提示。
- 修改 [translation_entry_collection.py](../../../../src/transbridge/converter/translation_entry_collection.py)：兼容方法从 `original` 提取译文，保留精确 ID 与 `overwrite` 语义；删除无法建立双语对应关系的文本回退；冲突先拒绝，重复导入计数为 0。
- 新增 [test_workbench_translation_import.py](../../../../tests/ui/test_workbench_translation_import.py)：真实工具栏、意图、窗口、后台任务与工程提交回归；覆盖延迟任务与 GC、成功/失败、重复导入、无目标、工程修改及版本切换。另用隔离子进程验证闪退回归。
- 新增 [test_translated_plugin_import.py](../../../../tests/converter/test_translated_plugin_import.py)：9 项真实 ESP 回归，覆盖中文、空白/相同原文、覆盖保护、不匹配和歧义。
- 新增 [plugin_fixtures.py](../../../../tests/plugin_fixtures.py)：共用最小 TES4/NPC_ 二进制构造器，不含用户数据。
- 修改 [test_migration_import.py](../../../../tests/contracts/io/test_migration_import.py)：增加 ESP 草稿、原文不变、冲突拒绝和未匹配契约。
- 修改 [test_main_window_coordinators.py](../../../../tests/ui/test_main_window_coordinators.py)：非模态窗口测试替身补齐集合和工程投影字段。

没有删除或移动文件；删除的是旧处理方法。用户既有 `installer/setup.iss`、`pyproject.toml`、`uv.lock` 修改不属于本记录。

## 兼容与职责边界

- 无数据格式或存储迁移。工作台与菜单使用同一窗口，选择“已翻译插件”即可导入 ESP/ESM/ESL。
- 工作台 JSON 入口由覆盖已有译文改为菜单的保护策略：仅填充空白且未翻译词条；必须先加载目标来源。
- ESP/JSON/SST 可以通过同一草稿验证；同时选择 XML/Strings 时明确提示分开执行，避免混入旧直接修改路径。
- 现有大文件经过职责审查：本次局部修复原导入方法及接线，删除重复职责。`TranslationEntryCollection` 仍是超阈值历史类；本次不做全类拆分，后续新增责任前须按集合存储与格式迁移边界拆分。

## 实际验证

29 项聚焦回归通过。补充目标变化校验、更新测试替身后执行扩大回归：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --no-cache --no-sync pytest tests/ui/test_workbench_translation_import.py tests/ui/test_structured_migration_import.py tests/contracts/io/test_migration_import.py tests/converter/test_translated_plugin_import.py tests/parser/test_plugin_parser.py tests/parser/test_plugin_tree_order.py tests/converter/tests_translation_entry_collection.py tests/ui/test_background_gui_operations.py tests/ui/test_main_window_coordinators.py tests/ui/test_parse_hydration_authority.py tests/ui/test_workbench_slices.py tests/ui/test_workbench_theme_migration.py -q -p no:cacheprovider --basetemp .tmp-import-fix-01a10fab/final-regression
```

结果：**106 passed**。随后增加隔离子进程测试，并修正测试独立运行时 QApplication 的持有周期，复验：

```powershell
uv run --no-cache --no-sync pytest tests/ui/test_workbench_translation_import.py -q -p no:cacheprovider --basetemp .tmp-import-fix-01a10fab/native-final
uv run --no-cache --no-sync ruff check src tests
uv run --no-cache --no-sync ruff format --check src tests
git diff --check
```

UI 复验 **7 passed**（与扩大回归有重叠，不累计）；Ruff、格式及 diff 空白检查通过。沙箱临时目录权限问题通过在沙箱外运行本地测试解决，未修改依赖或锁文件。

## 已知基线与未验证项

额外执行 `tests/contracts/projects/test_authoritative_mutation_paths.py` 时，3 项中 2 项失败：旧断言仍期待 AI `TaskSession.mark_completed` 写入及 `commit_translation(entries)` 字面调用，当前实现已改为 `_commit_entries`。以 `git show HEAD:...` 读取扫描源文件并重放原断言，确认 HEAD 同样为 2 失败、1 通过；本次未修改该模块或调整其断言。

未运行全仓库测试、真实朋友 ESP 复验或安装包构建/发布。除此之外，本次导入修复无未完成项。

# 补齐原文限定词条的全部已确认遗漏入口

日期：2026-10-06。Epic：original-match-entries；Story：S05。

关联：[计划](../../../../plans/original-match-entries/plan.md)、[ADR-045](../../../adr/045-original-qualified-plugin-entries.md)。用户在全入口审计后明确授权 bm-pilot 修复。本增量仅记录本轮修改，不将工作区此前的线程保活、性能优化、版本/安装器变更计入本次。

## 行为与修复落点

以下生产路径相对 src/transbridge。

- **词典错填与归一化合并**：新增 translation_memory/original_matching.py，提供完整身份索引与精确原文候选；修改 manager.py 的 add/save_from_collection/query/apply_to_collection。标记项以完整身份入库，独立保存，不进入归一化文本索引；查询只接受相同 key 和精确原文，禁止 STALE 或跨 key 文本回退。冲突回执携带完整身份，仲裁后可定位到唯一词条。service.py 对 V2 查询采用相同精确过滤并输出诊断。普通词条匹配和词典格式不变；已在旧逻辑下合并丢失的译文不能自动恢复，需要从原数据重新入库。
- **迁移规划误冲突**：修改 migrator/key_migrator.py 的 plan_migration/migrate，对任一侧要求原文身份的项采用精确匹配，无法唯一确定时 warning/跳过。新旧入口均保留普通词条语义；规划按 locator 预建索引，避免逐目标扫描所有源项。
- **DSD 重读丢项**：新增 converter/dsd_entries.py，在构建集合前批量识别冲突；converter/translation_entry_collection.py 与 smart_assistant/tools/_json_import.py 复用该入口。不同原文保留，同 key 同原文全部跳过 warning；普通项不强制标记。
- **DSD 迁回插件**：修改 application/io/migration_import.py 的 DSD 分支，使用同一批量解析器，传播跳过计数。QUST CNAM 的 DSD 不保存 editor ID/stage index，按该格式明确提供的插件限定 FormID、字段类型与精确原文唯一匹配，不猜测多阶段同文项，不跨插件文件名匹配。仅支持能证明对应关系的项，未扩大到其他缺失原文的格式猜测。
- **集合增量加入归一化错误**：修改 converter/translation_entry_collection.py 的 _normalize_legacy_upsert，标记项不经过裸 key 的旧身份重绑定，普通项也不能被重绑定为已有的标记项。覆盖逐条 add、更新与 native JSON 保存重开，源 key/id 不变。
- **助手入口与润色**：修改 smart_assistant/tools/base.py、tool_editor.py、tool_translator.py、smart_assistant/request_scope.py、ui/tools/smart_assistant/request_binding.py，查询返回可操作的完整身份，选中、范围和编辑贯通该身份，拒绝歧义裸 ID；_polish_execution.py 按完整 AI 响应别名回收成功结果。
- **ParaTranz 旁路保护**：smart_assistant/tools/_entry_upload.py 在任何上传前剔除标记项，返回 warning、跳过计数和部分完成状态，普通项继续；全跳过不触发远端写入。paratranz/workflow/downloader.py 对旧/兼容下载入口同样跳过标记项，新增 skipped_original_match 统计和日志，防止仅剩一条时被裸 key 错填。正常同步计划的既有保护不变。
- **初次加载丢已导入译文**：ui/coordinators/parse_coordinator.py 传递完整 EntryKey 初始状态；application/projects/source_commands.py 序列化/恢复该状态。GUI 直接解析的 legacy:v1 身份仅在单来源添加边界按 key+original 重绑定到已验证的来源 namespace；其他 namespace 必须精确一致；裸 key 仅兼容普通项。真实 PluginParser→XML 导入→GUI coordinator→add_source 回归证明三条初始译文及阶段均保留。
- **旧项目包往返失败**：persistence/legacy_project_archive.py 识别 VariantStore 输出的三分量身份字符串，验证当前来源后恢复译文、阶段、标签及快照；缺源时保留身份进入只读恢复。裸 ID 指向标记项或来源不匹配仍拒绝，不降级猜测。
- **旧 XML 写回**：writer/eet_xml_writer.py 对标记项校验完整定位及精确原文；writer/xt_xml_writer.py 同时校验索引、类型和原文；两者都禁止标记项参与跨 key 文本回退。普通词条原行为保持。
- **旧插件实例二次写回失效**：新增 writer/plugin_source.py，弱引用插件实例并保留原始文本及物理写回目标；writer/plugin_writer.py 使用此来源，不再拿第一次译文当第二次匹配的原文。覆盖新建 writer 重用插件、再次修改译文及清空恢复原文；弱引用生命周期测试证明不保活关闭的插件。

新增责任分别放在 original_matching.py、dsd_entries.py、plugin_source.py。已有超限 manager/collection/coordinator 仅作职责内局部修复与接线；本轮不做无关大规模重构，后续扩展其职责前应拆分。

## 回归测试

- 新增 tests/test_original_match_memory_migration.py：12 项覆盖词典保存重开、空白/换行差异、普通与限定词条隔离、旧/V2 套用、冲突回执及迁移。
- 扩展 tests/contracts/io/test_original_match_identity.py：集合逐项 add 与 native JSON 往返；test_original_match_migration.py：DSD 跳过统计、真实插件导出再迁回及跨插件/多阶段歧义拒绝。
- 新增 tests/converter/test_original_match_dsd_roundtrip.py、tests/writer/test_original_match_legacy_xml.py；扩展 tests/parser/test_original_match_plugin.py：DSD、XML 精确匹配及插件重复写回/弱引用生命周期。
- 新增 tests/smart_assistant/tools/test_original_match_tool_paths.py：8 项验证查询/编辑/范围、请求捕获恢复、润色和上传保护。
- 扩展 tests/paratranz/test_workflow.py、tests/application/projects/test_source_commands.py、tests/persistence/v2/test_legacy_project_archive.py、tests/ui/test_main_window_coordinators.py：旧下载、初始状态、旧包/快照及真实首次加载流程。

## 实际验证

所有 pytest 使用 `uv run --no-cache --no-sync pytest`，任务自建 `.tmp-om-fix-root/<轮次>` 为 basetemp，未安装依赖或调用真实 LLM/远端 API。

1. 词典/迁移首轮：`tests/test_original_match_memory_migration.py tests/test_translation_memory.py tests/test_key_migrator.py -q --tb=short`：41 passed。
2. DSD/迁移/FOMOD 聚焦：`tests/contracts/io/test_original_match_migration.py tests/contracts/io/test_migration_import.py tests/test_fomod_tm_provenance.py tests/test_fomod_typed_pipeline.py -q --tb=short`：69 passed。
3. 集成：`tests/contracts/io tests/converter tests/parser tests/writer tests/persistence tests/smart_assistant/tools tests/application/projects tests/application/fomod tests/ui/test_original_match_project.py tests/ui/test_original_match_projection.py tests/ui/test_workbench_translation_import.py tests/ui/test_source_update.py tests/test_translation_memory.py tests/test_translation_memory_gui.py tests/test_original_match_memory_migration.py tests/test_key_migrator.py` 加 `Get-ChildItem tests -Filter test_fomod*.py` 返回的全部 7 个文件，参数 `-q --tb=short`：**1140 passed**，83.45 秒。
4. 最后补充路径复验：`tests/contracts/io/test_original_match_migration.py tests/test_original_match_memory_migration.py tests/application/projects/test_source_commands.py tests/ui/test_main_window_coordinators.py tests/persistence/v2/test_legacy_project_archive.py tests/paratranz/test_workflow.py -q --tb=short`：**92 passed**。此轮包含集成启动后补入的真实插件 DSD 迁回测试与初次加载 namespace 边界。
5. 子任务独立验证：助手 205 passed，格式/插件 101 passed，兼容路径 120 passed；这些与主会话回归重叠，不相加作为唯一测试数。
6. `uv run --no-cache --no-sync ruff check src tests` 通过；`ruff format --check src tests` 为 1541 个通过，剩余任务前已有的 parser/plugin/plugin_string_with_context.py 混合换行问题；本轮所改文件格式检查通过。`git diff --check` 通过。

Neiva 实件只读验证：4 条标记项 → DSD 重读 4 条 → 迁回原插件集合 4 条，skipped=0。前后源文件 SHA-256 一致；插件重复写回测试在内存与自建临时文件进行，用户 ESP 未被修改。

## 限制与交付状态

本轮已确认的九类遗漏及额外集合增量身份问题均有修复与回归覆盖。没有声称所有潜在缺陷已被穷尽。未重新执行整个仓库、真实 LLM/API、打包和发布；此前已记录的助手确认竞态与旧契约测试不是本轮修复范围。相关路径的本轮集成没有失败。测试目录已按归属清理，未提交 Git。

# 原文限定的冲突词条：解析、编辑、保存、AI 与回写

日期：2026-10-06。Epic：original-match-entries。Story：S01～S04。

关联：[计划](../../../../plans/original-match-entries/plan.md)、[ADR-045](../../../adr/045-original-qualified-plugin-entries.md)。

## 用户可见行为

TranslationEntry 新增 requires_original_match，缺省 false。插件中重复 key 组全部设为 true，以原 key 加精确原文区分；不改变源 key/id，不引入 UUID 或数组位置身份。不同原文可独立编辑、AI 翻译、保存重开及写回。同 key 同原文的全部出现项跳过，记录 warning，其他项继续处理。

迁移时必须证明原文一致。已翻译 ESP 无法证明对应关系的冲突词条跳过 warning；不影响能处理的普通项。空白、大小写和换行不会被归一化。ParaTranz 唯一 key 协议不支持这类重复 key：同步计划跳过冲突项及远端同 key 项，避免覆盖或误删；现有重复 key 导出校验仍然有效。

## 文件与符号

以下路径相对 src/transbridge；只记录本轮可归属的变化。工作区已有的闪退、线程保活、批量导入性能、版本号和安装器变更不属于本增量。

### S01：身份、持久化与投影

- 修改 application/io/identity.py：EntryKey 增加可选 original，排序兼容 None/string；普通序列化仍为两分量，标记项为三分量。
- 修改 converter/translation_entry.py、application/io/mutation.py：TranslationEntry/EntrySnapshot 增加布尔标记、序列化兼容与身份一致性校验；标记项 original 不可直接变更。
- 修改 converter/translation_entry_collection.py：mutation 原子拒绝改变原文身份；SST 匹配和插件译文导入使用安全的完整身份规则。
- 修改 persistence/v2/schema.py：快照 schema 与重复校验允许完整原文身份。修改 persistence/v2/variant.py、persistence/variant_store.py：旧缓存对标记项使用完整序列化身份，禁止旧裸 id 缓存误投影到多个词条。
- 修改 application/projects/source_update.py、source_update_migration.py：源更新按 key/original 保留相同项译文，原文变化作为新项；source_commands.py 不将无法证明原文的旧初始状态投影到标记项。
- 修改 ui/entry_projection.py、ui/context.py、ui/project_labels.py：投影、标签和编辑按完整身份处理。
- 修改 ui/workbench/entry_action_scope.py、entry_refresh.py、filters_presenter.py、step2.py、translation_table.py、translation_table_delegate.py、translation_table_sorting.py：选择、刷新、过滤、排序与标签不再将同 key 兄弟项混同。
- 修改 smart_assistant/tools/base.py、tool_editor.py、types.py：保留完整身份，裸 id 指向多个项时不猜测；修改 application/translation/postprocess_checkpoint.py 与 ui/tools/ai_translator/version_snapshot.py：检查点和撤销保留完整身份。

### S02：插件与回写

- 新增 converter/plugin_entry_conflicts.py：resolve_plugin_entry_conflicts 先标记完整冲突组，再剔除同 key 同原文的全部出现项，返回 warning 诊断。
- 修改 parser/plugin/plugin_with_context.py：增加保留所有物理项的 extract_string_pairs_with_context 路径，旧字典接口保留兼容。
- 修改 parser/plugin_parser.py：create_entries 统一生成/过滤及冲突统计，空项过滤在冲突标记之后。
- 修改 application/io/legacy_adapters.py：SsePluginAdapter 接纳完整身份并传播 warning、跳过计数；移除已被统一插件规则替代的中间处理。
- 修改 writer/plugin_writer.py：预先索引物理目标，按精确原文写回；避免写入一条译文后影响后续目标查找。
- 修改 application/io/plugin_write.py：源快照验证 key/original/标记/string_id，不接受伪造的写回身份。
- 修改 fomod/pipeline.py：命名空间转换保留 original；写回用完整身份预建索引。

### S03：迁移、同步与 AI

- 修改 application/io/migration_import.py：_ImportedEntry 保留原文及标记；_map_to_target 只接受唯一的精确原文匹配，不能证明时产生 SOURCE_ORIGINAL_MATCH_REQUIRED warning 并计入跳过数。
- 修改 converter/translation_import_matching.py：EET/XT 标记项要求唯一精确原文，禁止归一化或取首项回退；TranslationEntry 和集合的 SST 入口采用相同约束。
- 修改 application/sync/planner.py：冲突项及远端同 key 项均生成 original_match_required 跳过步骤；application/terminology/corpus.py 的来源对齐保留 original。
- 新增 application/translation/entry_alias.py：ai_entry_id/ai_entry_key 为标记项提供内部完整身份别名，普通项协议保持原值。
- 修改 ai_translator/prompt_builder.py、translator.py、translation_entry_outcomes.py：提示、返回映射和逐条状态使用别名；structured_schemas.py 支持包含 original 的 EntryKey 结构。
- 修改 ai_translator/post_processor/{consistency_checker,format_validator,llm_arbiter,llm_refiner,polisher,post_processor,proofread_pipeline,quality_gate,refinement_response,strict_execution_evidence}.py：校对各阶段、重试和兼容解析按完整别名定位；缺少必要 original 的响应不被接受。
- 修改 application/translation/_open_proofread_stage.py、polish_report.py：校对结果和报告保留完整身份；paratranz/config_manager.py 仅规则结果映射键使用内部别名。
- 修改 ui/tools/ai_translator/{_mixed_worker,_polish_preview_dialog,_translation_report_dialog,_translation_worker,legacy_checkpoint,reporting,result_actions,result_presenter,result_view,run_controller,run_spec,run_view,scope_presenter,source_execution,task_entry_results,task_progress,task_run,task_scope,task_session,task_worker}.py：混合规则、预览选择、执行结果、报告定位和重试不再被同 id 项覆盖。task_session 的权威提交逻辑未在本轮重构。

### S04：测试与文档

- 新增 tests/contracts/io/test_original_match_identity.py、test_original_match_migration.py；tests/contracts/translation/test_original_match_checkpoint.py：身份、原子 mutation、精确迁移、旧入口及 FOMOD 回归。
- 新增 tests/parser/test_original_match_plugin.py、tests/persistence/v2/test_original_match_legacy_variant.py：物理重复、空白差异、交叉译文、localized 写回及缓存隔离。
- 新增 tests/ui/test_original_match_project.py、test_original_match_projection.py：权威工程编辑/保存/重开，以及独立选择和标签。
- 新增 tests/ai_translator/test_original_matched_entries.py：11 项覆盖真实离线 PromptBuilder/AutoTranslator 链路、只选一条、native 校对、预览、任务结果及运行身份。
- 更新 tests/application/projects/test_source_update_plugin.py、tests/contracts/paratranz/test_sync_plan_confirmation.py、tests/contracts/io/test_legacy_format_adapters.py、test_migration_import.py、tests/converter/test_translation_entry.py、test_translated_plugin_import.py、tests/writer/test_plugin_writer.py：源更新、同步保护及新的冲突处理契约。
- 新增 ADR-045、plan；更新 ADR-017 身份定义与相关索引。新冲突规则和 AI 别名分别提取独立模块；已有超限大模块仅作职责内接线/修复，未扩大职责。将来继续扩展集合或 UI 编排职责时需要先按数据流拆分，当前风险由集成回归覆盖。

## 验证命令与结果

命令均使用仓库现有环境。下列 pytest 命令前缀为 `uv run --no-cache --no-sync pytest`；每轮使用任务自建的 `.tmp-original-match-root-01a10fab/<轮次>` 作为 `--basetemp`。测试目录在交付前清理；没有写入用户 ESP。

1. `tests/contracts/io tests/converter tests/parser tests/writer tests/persistence/v2 tests/application/projects tests/ui/test_original_match_project.py tests/ui/test_original_match_projection.py tests/ui/test_workbench_translation_import.py tests/ui/test_source_update.py -q --tb=short`：716 passed、1 failed。失败是旧 to_dict 预期未包含默认 false 字段，修正预期后在最终回归通过。
2. `tests/contracts tests/application/translation tests/ai_translator tests/ui/tools tests/smart_assistant/tools -m 'not llm and not integration' -q --tb=short`：2001 passed、1 skipped、5 failed、1 deselected。没有将此轮记作全绿；详见下文。
3. `tests/contracts/io/test_original_match_identity.py tests/contracts/io/test_original_match_migration.py tests/contracts/translation/test_original_match_checkpoint.py tests/parser/test_original_match_plugin.py tests/persistence/v2/test_original_match_legacy_variant.py tests/ui/test_original_match_project.py tests/ui/test_original_match_projection.py tests/converter/test_translation_entry.py tests/application/projects/test_source_update_plugin.py tests/contracts/paratranz/test_sync_plan_confirmation.py tests/writer/test_plugin_writer.py -q --tb=short`：最终 91 passed。
4. `tests/ai_translator/test_original_matched_entries.py tests/ui/tools/test_ai_translation_reporting.py tests/ui/tools/test_proofread_resume_wiring.py tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py -q --tb=short`：最终 68 passed、1 failed。其中 AI 新增 11 项、报告 12 项、校对恢复 2 项全部通过；失败为既有助手确认竞态。
5. 子任务的解析/写回、身份/UI、AI 分批检查均通过；与以上套件存在重叠，不累加为唯一测试总数。
6. `uv run --no-cache --no-sync ruff check src tests`：通过。`uv run --no-cache --no-sync ruff format --check src tests`：1534 个文件通过，仅任务前已有的 parser/plugin/plugin_string_with_context.py 混合换行需格式化，本轮没有改变其内容。`git diff --check` 无空白错误。

只读解析实件：Neiva 2263 项、4 项标记、1 个重复 key 组；Remiel 8311 项、20 项标记、9 个重复 key 组。两者 skipped=0，完整身份均唯一。单次耗时仅作观察，未宣称性能提升，也未执行用户文件的写回。

## 扩展检查失败及遗留

- tests/contracts/projects/test_authoritative_mutation_paths.py 的两项断言仍期待 task_session 中旧的提交写法和方法名。将测试源码读取替换为 `git show HEAD:src/transbridge/...` 后两项同样失败，证实在 HEAD 已存在；本轮不修改无关提交契约。
- 助手确认卡片的批准/忽略两项在扩展轮失败，复跑通过但确认持久化失败项转而失败。与 [此前记录](../../maintenance/translation-import/2026-10-06-002-bulk-import-and-detached-snapshots.md) 已受控复现的 lease 释放竞态一致，本轮未改该链路，问题仍存在。
- 校对恢复扩展轮在创建 SQLite 表时报 unable to open database file。自建临时数据库绝对路径长 252 字符，独立 sqlite3 写入同一路径复现失败，76 字符临时路径写入成功；改用较短 basetemp 后原测试通过。属于当前 Windows 长路径限制，未借此修改校对业务。
- 未运行真实 LLM/API 集成、安装器构建或发布；未验证旧版程序读取新的三分量身份。未提交 Git。

## 兼容与使用

旧数据缺字段按 false 读取，普通身份序列化不变。新标记项的原文属于身份，不能当普通可编辑字段改变；源更新后原文变动按新项处理。此前已被跳过的词条不会凭空出现在旧工程，需要重新加载或更新源文件。包含新身份的工程不保证能降级给旧版程序使用；保留工程备份。源 key/id 从未改写，AI 内部响应别名不作为插件 key 导出。

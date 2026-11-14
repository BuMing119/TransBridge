# 语言绑定读取、历史来源兼容与全量严格输出

- 日期：2026-11-14。
- Epic / Story：`project-text-encoding` / S07～S12；[计划](../../../../plans/project-text-encoding/plan.md)、[FR33](../../../requirements/project-text-encoding.md)、[ADR-055](../../../adr/055-project-text-encoding.md)。七项修订需求已确认，ADR 保持提议。
- 状态：S07～S12 本地实现与相关 QA 完成；真实双插件/项目副本验证通过，五项已复证既有失败与未运行场景明确保留。
- 归属：按本轮开始时逐文件内容基线与当前文件直接比较，再核对目标 Git 差异。仓库开始即有大量未提交改动，以下只记本轮变化，不把 S01～S06、旧项目编码补缺或其他工作重复归入。既有日志保持不变；未提交或推送 Git，未修改依赖、锁文件或安装器。

## 行为结果与兼容性

程序默认及项目设置均持有源语言、目标语言、输入 codec、输出 codec，固定初始 English/Chinese、UTF-8/UTF-8；新项目复制全局默认后独立。旧项目保存补齐缺字段，即使无译文修改也可保存；不读源、不改原文或译文状态、不覆盖已有值，不依赖 AI profile。

新读取的 `xtranslator-v1` 对每个普通字段先尝试 UTF-8，再尝试源语言唯一备用码页；显式非 UTF-8 直接读取。`strict-v1` 保留明确严格来源并用于输出校验。缺少事实的旧 vendor 插件按 `legacy-v1` 还原原库 UTF-8/cp1250/cp1252/cp1251/replacement 行为；旧独立/关联 Strings 保持各自历史 decoder，不能把插件的历史 codec 列表套到所有格式。还原结果必须与已存 baseline 一致，项目补写 UTF-8 不构成历史读取证据。

普通插件文本和全部 Strings 记录统一目标 codec，包括未译、未改、未引用项以及输入/输出同名 UTF-8 的 fallback 字段。保留严格无损编码、Unicode 回读、长度/NUL 边界及原子发布；不照搬 xTranslator/EET 的替换或截断。源语言/目标语言只绑定 codec，既有显式 `language`/快照 `localized_language` 继续决定伴随文件路径，补写 Chinese 不会重命名原 English 文件。

保存设置不解析来源，独立主动重读对话框及事务链被移除。需要应用新规则时通过既有“更新来源”预览、备份和提交；同文件同指纹但策略变化可更新，可靠一对一映射迁移全部 variants，保留译文、标签、显式清空、锁定/隐藏保护，原文变化且有译文标为待复核。失败/取消/预览失效按既有事务回滚，设置保存和之后来源更新是两个动作。

## S07：配置、持久化与项目设置

下文代码路径以 `src/transbridge/` 为根（已写完整前缀者除外），测试/文档路径以仓库为根。以下除明确“新增”外均为相对于本轮基线的修改；部分文件虽在 Git 中仍是 untracked，但已存在于本轮基线，不算本轮新增。

- `src/transbridge/config/text_encoding.py`：扩展 `TextEncodings` 的两语言和 `read_options()`；新增 `TextReadOptions`、三种版本化策略、语言规范化及语言备用表；程序默认持久保存四项。
- `application/projects/provisioning.py`：准备新项目传递完整 `TextEncodings`；`provisioning_sources.py`：每个新来源使用 `with_source_read_options()`，冻结源语言与策略。
- `application/projects/save_changes.py`：缺省检查覆盖四项，`project_encoding_settings()` 仅补缺并保留已有原始值；`persistence/project_store/records.py`：统一序列化复用该补缺逻辑。
- `persistence/project_store/archive_validation.py`：项目/快照白名单增加语言；`archive_native.py`：验证格式层读取事实及适用格式，避免项目包往返丢事实。
- `application/projects/text_encoding.py`：删除全来源解析、缺输入补选和多来源重读候选，收敛为设置读写；保留所有权/代际/取消保护及未保存译文。
- `bootstrap/persistence.py`：设置服务不再注入 baselines/preparer，移除独立重读依赖。

## S08～S09：逐字段规则、历史来源与发布

- 新增 `src/transbridge/parser/text_reader.py`：`decode_game_text()` 实现按语言唯一备用并报告实际 codec；`decode_strings_text()` 隔离历史独立/关联 Strings 的 BOM、尾 NUL 与 fallback 语义。
- `parser/localized_string_records.py`：接受完整 `TextReadOptions` 和 legacy_linked，记录实际字段 codec，保留严格结构边界。
- `parser/plugin/text_subrecords.py`：新规则使用共享 decoder，legacy-v1 复用原库已解释文字；跟踪实际 codec，避免 replacement 历史字段在“未改”分支漏过转码。
- `parser/plugin/binary_io.py`：实例级传递语言/策略，统计 fallback；暴露全部 inline 文本供 writer 遍历。
- `parser/plugin_parser.py`：语言、读取策略贯穿插件/Strings，汇总 fallback 诊断；保留显式本地化文件后缀独立于源编码语言。
- `parser/strings_file.py`：文件、字节、目录及关联 lookup 路径传完整策略并汇总 fallback；输出不改变既有伴随文件命名。
- `writer/plugin_writer.py`：遍历完整 inline 字段而非只遍历本次可见词条，按目标 codec 编码所有普通文本，覆盖零修改及同名 UTF-8 fallback。
- 新增 `application/io/text_source_options.py`：`request_text_read_options()` 与 `snapshot_read_options()` 分离新请求和历史事实，来源明确策略优先；缺策略但有明确来源 encoding 走 strict-v1，无事实走 legacy-v1；集中记录 metadata/fallback 诊断。
- `application/io/legacy_adapters.py`：解析时传完整规则，来源快照保留实际语言/策略，legacy 不伪造显式 encoding；伴随路径继续使用既有 language。
- `application/io/plugin_localized_sources.py`：关联 Strings 显式传 legacy_linked，复现其历史解码边界。
- `application/io/strings_adapter.py`：解析记录读取事实与 fallback，抽出 `strings_write_plan()` 供写出和验证共用；全部物理记录均编码并与回读预期比较。
- `application/io/source_preservation.py`：删除与完整 Strings 写出计划重复的原字节保留分支，不再因保护/未改而绕过目标 codec。
- `application/io/plugin_publication_source.py`：准备源仅从 snapshot 事实重建，复用以快照身份限定，不能被当前输出请求覆盖解释。
- `application/io/plugin_write.py`：`_parser_from_snapshot()` 只读取快照事实，明确解析失败；输出文件后缀取显式 language 或已存 localized_language，不取编码目标语言。
- `application/io/plugin_transfer_matching.py`、`plugin_transfer_snapshot.py`：匹配/快照解析调用新事实接口；`sst_export.py`：SST 来源准备同样不覆盖历史解释。
- `application/io/publish/plugin_fidelity.py`：插件和关联 Strings 输出回读强制 strict-v1/目标 codec，源物理字段来自历史快照。
- `application/io/publish/validators.py`：通用回读强制严格目标策略；Strings 对完整物理记录列表核验 ID/文字，包含请求外记录，防止残留 fallback 字节或丢未引用项。
- `application/projects/source_encoding.py`：注入完整读取规则并校验预解析来源实际事实，不只比较 codec；`source_fields.py`：白名单保留 source_language/text_read_policy。
- `application/projects/source_extraction.py`：保存/重建来源语言与策略，缺 encoding 不强填 UTF-8；保留显式 localized_language。
- `application/projects/write_inputs.py`：实际输入绑定使用 expected snapshot 的读取事实，项目当前 input_encoding 不再覆盖旧来源。
- `application/projects/source_commands.py`：新增来源采用项目完整规则；`source_supplement.py`：按其新增/补充用例传完整设置，历史输出输入还原由 write_inputs 维护事实。
- `persistence/project_provisioning.py`：受控格式传完整读取参数，原生格式移除不适用参数；译文供体读取沿相同规则。

## S10：更新来源、全部版本及恢复

- `src/transbridge/application/projects/source_update.py`：列表读取登记的可用引用路径；prepare 用当前项目规则但保留历史伴随后缀；同字节、读取事实改变时使用可靠映射；prepare/commit 增加取消检查，全部版本原文差异进入既有迁移。
- `application/projects/text_encoding_matching.py`：限定原文身份的重复/已过滤 locator 必须验证物理顺序；即使本次某 local_key 只剩一项，也不能把它错误视作无需物理证明的唯一项。
- `application/projects/source_input_publication.py`：删去独立多来源重读发布层，单来源更新保留源重验、引用路径前像和取消保护，统一活动状态发布。
- `persistence/project_store/source_updates.py`：读取来源引用不必物化全部版本；移除独立重读的 previous_many/commit_many，保留单来源/全部版本事务与路径前像校验。
- `persistence/project_store/history.py`：新来源恢复点保存来源事实；仅旧编码重读快照兼容保留项目 codec 对，避免恢复来源时回退用户已保存设置。
- `persistence/project_store/encoding_restore.py`：依据同 namespace/fingerprint 但读取事实变化的 source 修订建立反向映射，兼容事实仅存在 parse_options 的旧记录，保护其他 variants；`historical_restore.py`：新旧恢复点分流，不将项目设置变化当作来源事实证明。

## S11：全部入口和界面

- `src/transbridge/application/io/initial_translation.py`：初始译文供体透传语言/策略；`legacy_migration.py`：旧来源及关联 Strings 传完整读取参数；`migration_import.py`：结构化插件迁移传完整策略。
- `application/io/open_translation_file.py`：打开/初始译文共用冻结读取规则；`translation_import.py`：导入的插件/Strings 规则贯穿迁移路径。
- `fomod/plugin_publishing.py`：准备、快照解析和发布沿冻结配置，输出 codec 与文件后缀分离；`fomod/pipeline.py`：旧插件译文迁移复用整次运行读取规则。
- `smart_assistant/tools/tool_parser.py`：来源导入/登记传完整 read_options；`write_back_preparation.py`：冻结读取和目标语言/codec，并让历史实际输入由来源事实绑定。
- `ui/coordinators/open_file_coordinator.py`、`parse_coordinator.py`：打开/解析/供体读取传完整项目规则；`parse_migration.py`、`migration_coordinator.py`：迁移与结构化准备链透传语言/策略；`project_coordinator.py`：插件恢复路径同步规则。
- `ui/operations/write_feature.py`：单/批/多方案输出草稿保存完整冻结设置，目标语言不改变文件后缀。
- `ui/text_encoding_fields.py`：新增源/目标语言选择、UTF-8 游戏默认及语言备用说明，可明确选 codec，窄布局换行；`ui/settings/text_encoding_page.py`：程序默认说明扩展到语言。
- `ui/project_text_encoding_dialog.py`：四项设置和普通保存按钮，明确应用到已存原文需更新来源；删除独立 TextEncodingPreviewDialog。
- `ui/coordinators/project_text_encoding_coordinator.py`：设置准备后直接提交，删除源文件定位/重读预览；`source_update_coordinator.py`：提示可选择原文件应用新读取规则，复用原预览更新流程。
- `ui/text_encoding.py`：摘要显示两语言和 codec，并说明旧来源按保存的读取记录核对。

## S12：回归与文档落点

- `tests/config/test_text_encoding.py`：四项缺省、语言/codec 绑定与全局持久化。
- `tests/parser/test_plugin_text_encoding.py`：严格策略定位、English/Polish/Russian 逐字段备用、无备用失败、legacy 复现及 UTF-8→UTF-8 全量转码。
- `tests/contracts/io/test_strings_parse_bytes.py`：混合编码逐记录 codec，以及历史独立/关联 Strings 原 decoder 差异。
- `tests/parser/test_localized_variant_identity.py`：现有解析 spy 接受并转发 `**read_options`，适配 `_legacy_linked` 参数；不改业务行为。
- `tests/parser/test_localized_capture_boundaries.py`：源编码语言不能改变默认 English/显式 French 伴随查找；`tests/integration/publish/test_plugin_write_reuse.py`：测试替身适配事实解析接口并保留复用断言。
- `tests/integration/publish/test_project_text_encoding.py`：snapshot 策略决定 Annaďg/Annaïg；严格输出拒绝残留 fallback；Strings 校验包括未请求记录。
- `tests/application/projects/test_project_text_encoding.py`：替换独立重读测试为设置不读源及明确更新来源，覆盖全版本、取消/文件变化/并发设置、失败回滚、恢复和伴随后缀。
- `tests/application/projects/test_source_update_plugin.py`：skip_empty 改变重复 locator 可见集合时拒绝绑定另一物理记录。
- `tests/application/projects/test_save_projection_cost.py`：clean save 分新旧配置，缺语言保存不得引入词条序列化；`test_source_commands.py`：新增来源声明完整规则。
- `tests/persistence/project_store/test_source_extraction.py`：程序/项目默认变化后仍按明确严格或缺事实 legacy 来源导出。
- `tests/application/fomod/test_plugin_publishing.py`：未改 fallback 转成 UTF-8，源/目标语言不重命名关联 Strings。
- `tests/smart_assistant/tools/test_source_import_authority.py`：English fallback 导入成功，无备用 Chinese 失败不登记来源。
- `tests/ui/test_project_text_encoding.py`：设置保存不定位/重读、语言选择及 10/14 pt 四项控件/说明/按钮几何。
- `docs/requirements/project-text-encoding.md`、`docs/adr/055-project-text-encoding.md`：直接修订有效合同，移除旧禁止 fallback、独立重读及当前设置覆盖历史事实的规定；记录上游证据、严格输出差异和 Strings 命名边界。
- `plans/project-text-encoding/plan.md`：保留 S01～S06 历史，新增本轮 S07～S12 与实际结果；`docs/requirements.md`：FR1.3 容纳同名 UTF-8 必要文本变化，FR33 摘要同步。
- `docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`：仅更新直接相关入口；新增本日志，不改已有增量。

## 实际验证

使用仓库现有 uv/Windows/Python 环境。实际 wrapper 将 tempfile、uv cache、pytest basetemp 放入任务自有目录，必要时在允许现有原子发布硬链接的权限下运行；下列保留实际 pytest 选择器与结果，省略临时路径。各组重叠，不累计为独立总数。

### 自动化回归

- codec 聚焦组：`tests/config/test_text_encoding.py tests/parser/test_plugin_text_encoding.py tests/parser/test_plugin_parser.py tests/parser/test_localized_capture_boundaries.py tests/parser/test_localized_resource_capture.py tests/contracts/io/test_strings_parse_bytes.py tests/integration/publish/test_project_text_encoding.py tests/integration/publish/test_plugin_write_reuse.py tests/integration/publish/test_plugin_transfer.py tests/integration/publish/test_round_trip_fidelity.py -q`，207 passed；被后续广域组覆盖，不重复累计。
- 最终入口组：`tests/ui/test_project_text_encoding.py tests/ui/test_ui_settings_dialog.py tests/ui/test_settings_keyboard_navigation.py tests/ui/test_settings_responsive_layout.py tests/ui/test_open_file_workflow.py tests/ui/test_source_update.py tests/ui/test_legacy_migration_safety.py tests/ui/operations tests/application/fomod tests/smart_assistant/tools/test_source_import_authority.py -q --tb=short`，656 passed、4 项既有弃用 warnings，74.65 秒。
- I/O 广域：`tests/config tests/parser tests/contracts/io tests/integration/publish tests/writer -q --tb=short`，1384 passed、1 skipped、1 failed，106.5 秒。唯一失败为 `tests/parser/test_localized_variant_identity.py` 的解析 spy 旧签名不接受 `_legacy_linked`；仅修测试替身透传参数后，完整该文件 11 passed、0.22 秒。未将这次原广域运行改记为同次全绿，也未重跑整个广域组。
- 项目/store 广域：`tests/application/projects tests/persistence/project_store -q --tb=short`，382 passed、1 skipped、5 failed。五项均来自 `tests/application/projects/test_source_artifact_publication_cost.py` 的旧来源路径断言，与此前记录同项。本轮用开始时逐文件基线重建 src/tests/pyproject，在隔离 cwd 以原 `.venv` Python 执行 `python -m pytest tests/application/projects/test_source_artifact_publication_cost.py -q --tb=short --basetemp <基线测试目录>`，同样 5 failed、2 passed，确认前存。
- 最终项目组实际命令：`uv run --offline --no-sync pytest tests/application/projects tests/persistence/project_store -q --tb=short --basetemp .tmp-encoding-revision-20261114/pytest-project-final -k 'not test_source_artifact_publication_cost'`，381 passed、1 skipped、7 deselected。排除的是已单独基线核验的整文件，未把 deselected 算作通过。
- 独立复审发现并修复两项：源语言误联动 Strings 后缀；skip_empty 改变重复 locator 可见集合导致错绑风险。随后额外聚焦 25 项通过，覆盖重复过滤、项目恢复及相关边界；该组精确选择器未保留在本记录，不编造命令。审查没有发现其他本轮原文/译文丢失问题，不据此声称所有路径穷尽验证。
- 最终恢复边界补充：`encoding_restore.py` 同时读取 format_fields 与 parse_options，兼容历史事实仅在 options 的来源；修复后已完成相关聚焦复验。此处不重复计数，精确选择器未在归档时获得。

### 静态检查

最终 `uv run ruff check src tests`、`uv run ruff format --check src tests`（2682 个文件无需格式化）及 `git -c core.safecrlf=false diff --check` 通过。未执行全仓 pytest；广域组及聚焦复验的覆盖和限制如上，互有重叠。


### 真实插件和现有项目

用户源 `BSAssets.esm`、`BSHeartland.esm` 及现有 project.sqlite3 保持只读；输出和迁移均在任务隔离目录/项目副本执行。两插件分别 8,758 和 11,317 条目，合计 20,075 条均有译文。未手动选 cp1250，历史事实绑定自动使用 legacy-v1 并与全部已存条目匹配。

实际执行 HydratedWritePreflight、发布/fidelity 路径及 strict-v1 UTF-8 输出重读，两插件成功。验证前后原数据库和原插件逐字节相同，不以设置保存或输出触碰真实输入。

旧数据库副本无译文改动保存补齐缺失 source/target language，20,075 原文/译文/状态保持。双 variants 在同字节 Polish 更新下全部保留；切 English 更新 BSAssets 得到 8,757 unchanged、1 changed、零新增/删除。两个版本都将 `Annaďg` 更新为 `Annaïg`，译文保留、TRANSLATED 转 QUESTIONABLE；逐项核对 20,075 keys 的译文、标签、tombstones 及其他阶段无非预期变化。

UI 专项 7 项通过，覆盖 10/14 pt 几何；主会话实际查看设置截图，确认四项选择、说明与按钮没有遮挡。截图检查不是可见桌面全流程或外部 xTranslator/EET 联动验收。

## 遗留与验证边界

本轮功能实现遗留项：无。五项来源路径旧断言失败按上述基线证据保留，不宣称全仓 pytest 全绿。未执行用户真实项目原位迁移、可见桌面完整交互或外部 xTranslator/EET 写出联动；读取其源码/IL 仅用于确认规则。只承诺新程序兼容现行缺字段项目，不承诺旧程序接受新增字段包。

本轮临时基线、脚本、输出、副本、截图及测试目录由主会话确认归属后清理，不作为长期证据链接，不删除用户原文件或共享临时目录。精确结果与选择器保留在本日志。

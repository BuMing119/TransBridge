# EET 原格式流程接入与四种格式边界澄清

- 日期：2026-11-08
- Epic / Story：eet-integration / Story 01–03
- 计划：[EET 接入](../../../../plans/eet-integration/plan.md)
- 设计：[ADR-053](../../../adr/053-eet-binary-io-integration.md)
- 范围：基于任务开始时的工作区副本逐文件核对增量。此前已有 SST、EET codec/数字缓存实验及其他未提交工作，不归为本次新增实现。

## 行为与兼容

EET v3 Classique/BDD 从实验脚本接入软件来源、工程创建/追加、保存重开、译文迁移、
ParaTranz JSON 往返、GUI 单个/批量写回及助手原格式写回，统一经过预检、staging、完整回读和原子发布。
保持实验性。重复物理记录分别保存；译文变化只重建译文数字缓存，保留原文缓存、原生状态及其他原生数据。

ESP-ESM Translator XML（.xml）、ESP-ESM Translator EET（.eet）、xTranslator XML（.xml）、
xTranslator SST（.sst）是四种独立格式。SSU8/SSU9 是 SST 版本。
保留已有 FormatId、助手 parse_eet 和 target=eet 兼容名称；后两者表示工具来源选择，
按内容及快照路由各自 adapter，不代表 XML 与 EET 合并或隐式转换。
维护注释直接放在格式枚举、读取选择和写回边界，界面筛选器分别列出 XML/EET。

新 EET 条目使用来源命名空间和 eet-v1 物理序号，携带来源摘要、记录签名及 codec 版本。
没有这些绑定的旧条目必须重新解析，不能猜测原生写回位置。可信工程快照可在来源移走后恢复写回。
跨来源译文迁移要求唯一定位及原文一致，两侧已知 EditorID 也必须一致；冲突、重复和空译文保守跳过。

## 逐文件增量

路径均相对仓库根目录；“修改”包含任务开始时已经存在但尚未提交的文件。

### 格式与发布

- 新增 `src/transbridge/converter/eet_entries.py`：document_entries、版本化物理身份与原生记录签名，保留重复记录。
- 新增 `src/transbridge/converter/eet_matching.py`：known_editor_id，从保存元数据及插件定位键恢复匹配约束。
- 修改 `src/transbridge/converter/sst_matching.py`：target_locator 支持 EET 元数据定位。
- 新增 `src/transbridge/application/io/eet_adapter.py`：EetAdapter 读取、绑定验证、缓存更新、取消与原格式写出。
- 新增 `src/transbridge/application/io/eet_source.py`：probe_eet_format / parse_eet_source 按捕获内容选择两个独立 adapter，澄清历史 helper 命名。
- 修改 `src/transbridge/application/io/contracts.py`：FormatId 注释说明四格式边界及持久化 ID 兼容性。
- 修改 `src/transbridge/application/io/catalog.py`：注册实验性 binary.eet 能力及 adapter。
- 修改 `src/transbridge/application/io/legacy_adapters.py`：EetXmlAdapter.parse_bytes，使 XML 条目与快照来自同一次读取。
- 修改 `src/transbridge/application/io/use_case.py`：分派捕获字节解析，移除旧 binary.eet 不支持提示。
- 修改 `src/transbridge/application/io/migration_import.py`：EET 译文来源与保守唯一匹配。
- 修改 `src/transbridge/application/io/write_formats.py`：EET 单产物路径；注释禁止因相同生产工具而跨格式写回。
- 修改 `src/transbridge/application/io/operation_write.py`：EET 纳入已有 hydration 预检。
- 新增 `src/transbridge/application/io/publish/eet_fidelity.py`：比较完整预期原生文档，覆盖未选择记录、缓存和短语表。
- 修改 `src/transbridge/application/io/publish/validators.py`：注册 EET 完整性验证分支。
- 修改 `src/transbridge/parser/eet_binary_codec.py`、`scripts/eet_lab.py`：只更新过时模块说明；codec 与缓存算法已在之前实验实现。

### 助手入口

- 新增 `src/transbridge/smart_assistant/tools/_eet_import.py`：追加前验证捕获快照与解析词条一致。
- 新增 `src/transbridge/smart_assistant/tools/_record_import.py`：提取共享定位匹配，保留目标身份和元数据；EET 附加 EditorID 约束。
- 修改 `src/transbridge/smart_assistant/tools/_sst_import.py`：复用共享匹配，保留 SST 自身快照验证。
- 修改 `src/transbridge/smart_assistant/tools/_source_import.py`：连接 EET 追加映射。
- 修改 `src/transbridge/smart_assistant/tools/tool_parser.py`：允许 .eet、内容路由、四格式名称；注释 parse_eet 兼容语义。
- 修改 `src/transbridge/smart_assistant/tools/write_back_preparation.py`：按真实快照保存 EET/XML 身份，派生 .eet 输出，说明 target=eet 不请求格式转换。
- 修改 `src/transbridge/smart_assistant/tools/tool_writer.py`：更新支持范围及实验性说明。

### GUI

- 新增 `src/transbridge/ui/eet_source.py`：解析集合、准备/应用迁移，供既有后台工作器调用。
- 修改 `src/transbridge/ui/coordinators/parse_coordinator.py`：委托独立来源 helper，传递格式与快照。
- 修改 `src/transbridge/ui/coordinators/parse_migration.py`：EET 迁移读取接线。
- 修改 `src/transbridge/ui/coordinators/migration_coordinator.py`：应用保守匹配结果。
- 修改 `src/transbridge/ui/coordinators/source_input_coordinator.py`：输入提示及来源内容识别接线。
- 修改 `src/transbridge/ui/coordinators/source_update_coordinator.py`：EET 来源更新与迁移。
- 修改 `src/transbridge/ui/source_hydration.py`：恢复 binary.eet 来源信息，保留缺失路径的完整快照。
- 修改 `src/transbridge/ui/operations/write_plan.py`：EET 写出计划与原格式目标。
- 修改 `src/transbridge/ui/operations/write_dialog.py`：EET 原格式另存及独立筛选器名称。
- 修改 `src/transbridge/ui/operations/write_batch.py`：批量 EET 输出，按内容派生正确扩展名。
- 修改 `src/transbridge/ui/shell/start_center_controller.py`：已有来源建项支持 EET，更新名称。
- 修改 `src/transbridge/ui/shell/context_help.py`：帮助文案区分四格式。
- 修改 `src/transbridge/ui/workbench/_parse_config_dialog.py`：来源类型说明与独立 EET/XML 文件筛选器。
- 修改 `src/transbridge/ui/workbench/source_input_view.py`：共用工具来源入口，明确两种独立文件格式。
- 修改 `src/transbridge/ui/workbench/parse_presenter.py`：输入提示名称。
- 修改 `src/transbridge/ui/workbench/step1.py`：旧入口的格式与快照传递。
- 修改 `src/transbridge/ui/workbench/cards/write_card.py`：写出说明区分四格式。

### 测试

- 新增 `tests/eet_fixtures.py`：合成 EET 记录/文件，避免提交用户数据。
- 新增 `tests/contracts/io/test_eet_adapter.py`：25 项 adapter 身份、快照、缓存、重复、取消及损坏回归。
- 新增 `tests/contracts/io/test_eet_publication.py`：11 项统一发布及工程重开/缺来源回归。
- 新增 `tests/contracts/io/test_eet_source_migration.py`：20 项内容路由、单次读取、匹配约束及分类 JSON 往返。
- 新增 `tests/smart_assistant/tools/test_eet_workflow.py`：15 项创建/追加/写回及保存证据回归。
- 新增 `tests/ui/test_binary_eet_sources.py`：12 项 GUI 来源、更新、迁移及名称回归。
- 新增 `tests/ui/operations/test_write_binary_eet.py`：5 项原格式单个/批量写出回归。
- 修改 `tests/source_format_fixtures.py`：新增 binary.eet 合成来源。
- 修改 `tests/persistence/v2/test_project_provisioning_import_formats.py`：纳入 EET 持久化与损坏诊断。
- 修改 `tests/contracts/io/test_catalog.py`：目录能力断言。
- 修改 `tests/capability/test_capability_matrix.py`、`tests/capability/matrix_generator.py`：实验性读写能力及生成器说明。
- 修改 `tests/ui/test_source_update.py`、`tests/ui/test_structured_migration_import.py`：EET 更新与保守迁移。
- 修改 `tests/ui/test_safe_drop_router.py`、`tests/ui/test_start_center_multi_plugin.py`：来源识别和建项支持；保留已知无关基线断言。
- 修改 `tests/ui/test_parse_migrations.py`、`tests/ui/operations/test_write_save_as.py`：四格式名称及实际拒绝诊断断言。

### 文档

- 新增 `docs/adr/053-eet-binary-io-integration.md`、`plans/eet-integration/plan.md`：设计提议、三 Story、边界、职责复核及完成证据。
- 修改 `docs/requirements.md`：EET 接入；FR9.6 对齐真实 writer.write_back，纠正虚构工具/参数及 deprecated 矛盾。
- 修改 `docs/adr/017-translation-io-kernel-v2.md`：EET 实验性能力状态。
- 修改 `docs/dev/ARCHITECTURE.md`、`docs/dev/DATA_STRUCTURES.md`：adapter 边界、快照及身份绑定。
- 修改 `docs/dev/parser.md`、`docs/dev/converter.md`、`docs/dev/writer.md`、`docs/dev/ui.md`：解析、匹配、写回及用户入口；澄清四格式和兼容接口。
- 修改 `docs/dev/eet-experiment.md`：数字缓存证据、3DNPC 实验及正式接入路径。
- 修改 `docs/dev/sst-experiment.md`、`plans/sst-integration/plan.md`：补齐之前 80 份 New ANK SST 样本验证证据。
- 修改 `plans/file-parsing/plan.md`、`plans/file-parsing/stories/story-04-eet-parser.md`：区分 XML parser 与 EET 原生 adapter。
- 修改 `plans/file-writing/plan.md`、`plans/file-writing/stories/story-03-eet-update.md`、`plans/file-writing/stories/story-04-eet-build.md`：原格式更新及 XML builder 范围。
- 修改 `plans/stage-unification/plan.md`、`plans/stage-unification/stories/story-02-writer-stage-fix.md`：EET 原生 STATUS 不套用 XML 0/99 状态映射。
- 修改 `plans/translation-io-kernel-v2/plan.md`、`plans/translation-io-kernel-v2/stories/story-04-esp-eet-xt-adapters.md`：实验性接入现状。
- 修改 `plans/release-hardening-v2/stories/story-04-windows-security-capability.md`：能力矩阵现状。
- 修改 `docs/INDEX.md`、`docs/dev/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`：相关入口及增量链接，保留历史日志。

## 验证命令与结果

扩大回归：**1218 passed、1 skipped、2 deselected**。7 条依赖弃用警告；不是全仓测试全绿声明。

```powershell
uv run --offline --no-sync pytest tests/contracts/io tests/parser tests/capability tests/persistence/v2/test_project_provisioning_import_formats.py tests/persistence/v2/test_project_provisioning_xml.py tests/smart_assistant/tools/test_eet_workflow.py tests/smart_assistant/tools/test_sst_append.py tests/smart_assistant/tools/test_source_import_authority.py tests/smart_assistant/tools/test_write_back_options.py tests/smart_assistant/tools/test_safe_write_back.py tests/smart_assistant/tools/test_write_back_publication_boundary.py tests/ui/test_binary_eet_sources.py tests/ui/operations/test_write_binary_eet.py tests/ui/test_parse_hydration_authority.py tests/ui/test_workbench_slices.py tests/ui/test_source_update.py tests/ui/test_safe_drop_router.py tests/ui/test_structured_migration_import.py tests/ui/test_start_center_multi_plugin.py tests/ui/operations/test_write_batch_sst.py tests/ui/operations/test_write_save_as.py --deselect=tests/smart_assistant/tools/test_safe_write_back.py::test_localized_prepared_source_roundtrip_preserves_binary_companions_and_metadata --deselect=tests/ui/test_start_center_multi_plugin.py::test_choose_multiple_plugins_creates_one_project_and_loads_every_collection -q --basetemp=.tmp-eet-integration-20261108/final-regression --tb=short
```

两项排除在任务开始时完整源代码副本上独立复现：localized 用例预期裸 metadata，而既有实现补充 terminology_plugin_id；
开始中心用例预期路径来源标识，而既有实现返回 source-0 等槽位。未通过修改无关实现掩盖失败。
跳过项为 `test_symlink_and_unreadable_inputs_have_stable_diagnostics`，Windows 无符号链接权限。

最后的名称/注释调整后，核心 **41 passed**；UI **92 passed、1 deselected**（同一开始中心基线失败）。

```powershell
uv run --offline --no-sync pytest tests/contracts/io/test_eet_source_migration.py tests/smart_assistant/tools/test_eet_workflow.py tests/smart_assistant/tools/test_source_import_authority.py -q --basetemp=.tmp-eet-integration-20261108/naming-retry --tb=short
uv run --offline --no-sync pytest tests/ui/test_binary_eet_sources.py tests/ui/operations/test_write_binary_eet.py tests/ui/test_source_update.py tests/ui/operations/test_write_save_as.py tests/ui/test_parse_migrations.py tests/ui/test_workbench_slices.py tests/ui/test_structured_migration_import.py tests/ui/test_start_center_multi_plugin.py -q -k 'not test_choose_multiple_plugins_creates_one_project_and_loads_every_collection' --basetemp=.tmp-eet-ui-names-20261108/final
uv run --offline --no-sync ruff check src tests scripts/eet_lab.py scripts/sst_lab.py
uv run --offline --no-sync ruff format --check src tests scripts/eet_lab.py scripts/sst_lab.py
```

Ruff 全通过，2429 文件格式符合。最后核心回归首轮 40 通过、1 次 Windows 恢复记录原子替换 WinError 5；
未改代码，在独立临时目录复跑 41 项全部通过。受限环境中的 uv 缓存访问及 pytest 目录权限问题通过批准的本地验证执行解决。

真实样本 `C:/Users/admin/Downloads/3DNPC_B675CB1D.eet`：53847 条，使用正式解析→hydration 预检→统一发布→全原生内容比较。
仅物理序号 29430 译文由“拿着，50金币。”改为“拿着，75金币。”，译文数字缓存由 50 更新为 75，原文缓存保持 50。
输入字节未修改。产物保留在忽略目录 `out/eet-integration-20261108/3DNPC_B675CB1D.eet`，
证据在同目录 `validation.json`；输出 SHA256 为 `e74527a570a0b25f6f086519fe8111f8a7af082affa11158b95c097ca7a7bb17`。
单次观察解析 1.964 秒，全链 11.209 秒，不作为性能基准。

## 遗留边界

- 仅已验证 EET v3，仍为实验性；从零构建完整 EET 与原文编辑不在本次译文写回能力内。
- 数字缓存使用已验证的 EET4 4.35 默认设置；用户自定义上游设置不存入 EET 文件，不能自动恢复。
- 按用户要求未使用 computer-use，未启动外部翻译器；上游软件人工验收由用户进行。
- 未进行真实 ParaTranz 上传、完整仓库测试、打包安装验证；未增加独立 MCP 文件读写工具。
- 大型旧入口的局部修改及后续拆分条件记录于计划职责复核；没有新增大型集中式责任。
- 未提交或推送 Git；用户原始翻译文件与既有无关修改保留。

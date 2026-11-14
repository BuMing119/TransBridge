# 项目文本语言与编码实施计划

- Feature：`project-text-encoding`。
- 状态：本轮七项修订需求与实施范围已确认；S07～S12 本地实现与相关 QA 完成，真实双插件及项目副本验收通过，既有失败和未运行场景明确保留。S01～S06 为此前已完成历史，不重复计为本轮成果。
- 日期：2026-11-14。
- 需求：[FR33](../../docs/requirements/project-text-encoding.md)；架构：[ADR-055](../../docs/adr/055-project-text-encoding.md)（提议）。

## 目标与非目标

程序默认与项目独立持有源/目标语言和输入/输出编码；新读取采用逐字段 UTF-8 加源语言备用，非 UTF-8 显式读取；历史来源遵循登记事实；全部普通文本统一严格输出。设置保存不读源，应用新规则复用更新来源并迁移全部 variants。

无文件级覆盖、临时导出编码、独立主动重读、AI profile 语言联动、新缓存或新 hash；不新增依赖或游戏，不迁移旧项目格式。原生翻译存档与专用字段保持自身格式规则。语言设置只绑定编码，不扩展 Strings 伴随文件命名：沿用显式 `language`/快照 `localized_language`，补写 Chinese 不得把原 English 文件改名。保留共享工作区无关变更，不将用户插件/项目提交仓库。

## 修订基线与公共契约

- `config/text_encoding.py::TextEncodings` 当前已有两 codec，本轮扩展两语言；项目键 `text_source_language`、`text_target_language`，固定缺省 English/Chinese、UTF-8/UTF-8。全局默认仅新项目采用，旧项目缺字段由保存补齐，不读源。
- `ParseRequest.options`、来源 `read_options` 传递 `input_encoding`、`source_language`、`text_read_policy`。新读取 `xtranslator-v1`；无事实旧 vendor 为 `legacy-v1`；明确严格历史来源及输出校验为 `strict-v1`。保存项目设置不得捏造来源事实。
- `application/projects/source_fields.py`、`persistence/project_store/records.py`、`archive_validation.py` 等白名单均须同步。导出还原历史来源用读取事实，当前项目设置仅控制新来源/明确更新及输出目标。
- 既有 `parser/plugin/binary_io.py`、`text_subrecords.py`、`localized_string_records.py`、`game_text.py` 已隔离格式 codec，可扩展策略但不全局 monkeypatch。全量转码不能以 input/output 相同跳过。
- 移除 `ProjectTextEncodingService` 独立重读链，保留设置职责；复用 `source_update.py`、`source_update_migration.py`、`text_encoding_matching.py` 和 `ProjectSourceRevisionStore`。来源策略变更允许同文件同指纹更新，全部 variants 在既有事务迁移。
- 真实旧项目已有 UTF-8/UTF-8 但来源无编码事实，历史 cp1250 与 20,075 条目匹配，cp1252 的 `Annaďg`/`Annaïg` 差异说明不能按当前 English 默认重解。此为前置调查事实，尚非本轮写出/迁移验收通过。
- 大模块先做责任审查，新策略/事实决议独立成完整可测责任，已有入口仅接线；不得将新业务继续堆入超规模模块。

## 历史 S01～S06（保留完成事实）

以下记录前次实施，不构成本轮有效实施指令；被修订的读取、重读和事实优先级由 S07～S12 与当前 FR33/ADR 统一定义。细节与原验收见[历史记录](../../docs/changelogs/project-text-encoding/story-01-to-06/2026-11-14-001-project-text-encoding.md)。

### S01：项目编码设置可保存与恢复

历史状态：本地实现与相关验证完成；包括两 codec 所有权、持久化/项目包及旧项目保存补缺。[后续补缺修复](../../docs/changelogs/project-text-encoding/story-01-to-06/2026-11-14-002-persist-missing-encoding-defaults.md)相关测试 125 通过。本轮由 S07 扩展语言。

### S02：插件与 Strings 严格读取

历史状态：局部 codec、异常定位与隔离验证完成。当时采用严格单 codec，本轮 S08 修订新读取规则并保留严格策略用于明确历史来源和输出验证。

### S03：完整纯转码与按输出编码验证

历史状态：全量转码、StagePolicy、Strings 结构和严格发布相关验证完成。本轮 S08 补齐同名 UTF-8 下 fallback 字段必须转码的语义，S09 分离历史源验证。

### S04：全部来源安全重读和全部版本迁移

历史状态：独立重读、可靠映射、全版本事务及恢复相关验证完成。本轮 S10 移除独立主动重读入口，将可复用定位/迁移能力并入既有更新来源。

### S05：界面与全部操作入口接线

历史状态：实现及离屏行为/几何/键盘验证完成，未做可见桌面验收。本轮 S11 更新语言设置、操作模型及事实传递。

### S06：跨入口回归与记录

历史状态：I/O 广域 952 通过、1 跳过；项目组 414 通过、1 跳过、5 项已在任务前基线复现失败；UI 628 通过；边界 118、独立复审 101 通过。字节/原生隔离专项 16、FOMOD 72、助手 options 34 有重叠，不累计。历史 Ruff/格式（2680 文件）及 diff 检查通过；未跑全仓 pytest、可见桌面、真实大型 Mod 或外部软件联动。这些数字只说明前次实施，不替代 S12。

## 本轮 Stories 与顺序

S07 定义配置和策略合同，S08 完成 codec，S09/S10 在合同上分别处理历史还原及明确迁移，S11 接入入口，S12 综合验收。沿用用户批准的委派边界，修改同一文件须协调。本轮文档先确认有效合同，最终 QA 后再回填状态、增量记录与直接索引。

### S07：语言与 codec 设置及旧项目兼容

状态：本地实现与相关验证完成。对应 FR33.1、FR33.4。

验收：全局/项目四项独立；源/目标语言关联读取/输出默认且可手选 codec；旧项目固定补缺且保留已有值；无改动保存也落盘、不读源、不改状态，不借 AI profile 推导。

文件落点：`config/text_encoding.py`、`application/projects/provisioning.py`/`provisioning_sources.py`、`persistence/project_provisioning.py`、`project_store/records.py`/`archive_validation.py`/`save_verification.py`、已有项目保存接线。

步骤：扩展值对象与默认服务、规范语言/codec；新建冻结四项；更新白名单和包合同；普通保存补缺只写缺字段，核验物理落盘并同步内存；区分缺失与非法。

验证：`tests/config/test_text_encoding.py`、`tests/application/projects/test_provisioning.py`、项目 store/archive/save 及 UI 保存测试；覆盖默认变化、部分缺失、已有值、无修改连续两次保存、来源路径不可用仍可保存。

### S08：逐字段语言读取与完整严格转码

状态：本地实现与相关验证完成。对应 FR33.2、FR33.3。

验收：UTF-8 与 English/Polish/Russian 备用字段混合正确；非 UTF-8 直接读取；同名 UTF-8 仍重编码 fallback；所有普通字段/Strings 包括未译未改未引用统一输出；严格无损及专用字段保真。

文件落点：`parser/game_text.py`、`localized_string_records.py`、`plugin/binary_io.py`/`text_subrecords.py`、`plugin_parser.py`、`strings_file.py`、`writer/plugin_writer.py`、`application/io/strings_adapter.py`/`legacy_adapters.py`/`plugin_write.py`、`application/io/publish/validators.py`/`plugin_fidelity.py`。新增读取策略模块时保持单一责任。

步骤：实现版本化读取规则并沿局部实例传递；全量遍历普通文本后按阶段策略选择译文；去掉同名 codec/零修改绕过转码；源与输出验证分别传正确策略；严格编码/回读、溢出、NUL 和整组失败沿用已有保护。

验证：`tests/parser/test_plugin_text_encoding.py`、`tests/contracts/io/test_strings_parse_bytes.py`、`test_localized_strings_adapter.py`、`tests/integration/publish/test_project_text_encoding.py`；覆盖三语言混合、显式码页、UTF-8→UTF-8 fallback、UTF-8↔cp936、中文→cp1252失败、未引用 Strings、VMAD、截断拒绝和输出禁止 fallback。

### S09：历史来源事实及 baseline 一致性

状态：本地实现与相关验证完成。对应 FR33.5、FR33.7。

验收：来源事实优先于当前项目设置；无事实旧 vendor 用 legacy-v1 并逐项匹配 baseline；明确严格来源保留 strict-v1；已补 UTF-8 不推定显式读取；不匹配不发布、不重写原文。

文件落点：`application/projects/source_encoding.py`/`source_fields.py`、来源准备/补充接线、`application/io/plugin_publication_source.py`、写出及助手准备链；按实际调用路径定位所有历史源还原处。

步骤：集中区分新读取与历史还原；保留持久 read_options，不以 project settings 覆盖；明确旧事实缺失判定；插件/Strings/BSA 还原与 baseline 比较；将源校验策略和严格输出策略分开传递。

验证：项目补充、助手 options、发布保真与项目编码测试；构造同字节 cp1250/cp1252 均可解但 Unicode 不同的样本，验证更改设置不静默改文，兼容旧 replacement 且输出不新增替换。

### S10：复用更新来源迁移新读取规则

状态：本地实现与相关验证完成。对应 FR33.6。

验收：设置保存不解析来源；无独立主动重读；同文件同指纹、策略改变可更新；全 variants 保留译文/标签/清空/锁隐藏，变原文待复核；歧义、过期预览、取消、并发及失败不部分提交，恢复点可回退。

文件落点：`application/projects/text_encoding.py`、`text_encoding_matching.py`、`source_update.py`/`source_update_migration.py`/`source_input_publication.py`、`persistence/project_store/source_updates.py`/`encoding_restore.py`/`historical_restore.py`，必要 bootstrap 接线。

步骤：把设置 service 收敛为设置事务；将读取策略纳入更新是否变化的判断；新规则解析后可靠映射旧身份；在既有来源更新事务写 baseline/事实/全部版本和恢复点；删除失效独立重读路径及重复接线，不另叠一套流程。

验证：`tests/application/projects/test_project_text_encoding.py`、`test_source_update.py`、`test_source_update_plugin.py`及当前 SQLite 恢复测试；覆盖同文件、两版本、重复定位、锁隐藏、脏项目、晚取消、失败/恢复和原设置保存与后续更新分离。

### S11：界面和所有入口统一

状态：本地实现与相关验证完成。对应 FR33.1、FR33.6、FR33.7。

验收：全局/项目显示四项和作用范围；项目保存不读源，已有原文通过既有更新来源；新来源/历史源/输出规则分别正确；GUI、CLI/MCP、助手、批量/多方案及 FOMOD 不绕过应用层。

文件落点：`ui/settings/text_encoding_page.py`、`ui/text_encoding_fields.py`、`project_text_encoding_dialog.py`、`coordinators/project_text_encoding_coordinator.py`/`source_update_coordinator.py`、`ui/operations/`、`smart_assistant/tools/text_encoding.py`/`write_back_preparation.py`、入口和 bootstrap 窄接线。

步骤：复用既有控件/主题和 worker；删除重读按钮及文字，展示当前语言/codec；操作开始冻结设置，历史读取事实单独传递；更新来源保留预览/补充/备份提示。无项目、忙碌、错误、取消及键盘焦点应清楚。

验证：`tests/ui/test_project_text_encoding.py`、`tests/ui/operations/test_write_text_encoding.py`、助手写回 options、FOMOD 和 bootstrap；行为测试与可用条件下实际界面检查分开报告，不把离屏通过等同可见桌面验收。

### S12：真实两插件、旧项目和综合 QA

状态：相关 QA、真实双插件与项目副本验证及增量记录完成；范围限制见下文。对应全部需求。

验收：本轮有独立命令/结果证据；真实源文件与用户项目保持只读，隔离副本验证 20,075 条目匹配、普通保存补缺、设置不读源、来源更新全版本迁移及严格输出回读；未执行场景明确列出，不隐藏既有失败。

真实输入：用户提供的 `BSAssets.esm`、`BSHeartland.esm` 及当前 project.sqlite3。具体路径由主任务运行时引用，不把用户文件复制进版本控制。只读上游/本地 EET 调查证据见 ADR，不能替代 TransBridge 实际验收。

步骤：先跑聚焦测试，失败定位后扩大；审查异常、取消/回滚、文件清理和责任阈值；逐项记录真实样本读取与输出结构/语义比较，保存前后项目事实比对；运行全仓静态检查；最终仅据本轮真实差异和 QA 写增量记录，再回填文档/索引。

验证命令起点：

```powershell
uv run pytest tests/config/test_text_encoding.py tests/parser/test_plugin_text_encoding.py tests/contracts/io/test_strings_parse_bytes.py tests/contracts/io/test_localized_strings_adapter.py -q
uv run pytest tests/application/projects/test_project_text_encoding.py tests/application/projects/test_source_update.py tests/application/projects/test_source_update_plugin.py -q
uv run pytest tests/integration/publish/test_project_text_encoding.py tests/ui/test_project_text_encoding.py tests/ui/operations/test_write_text_encoding.py -q
uv run ruff check src tests
uv run ruff format --check src tests
```

按实际变更补充助手、FOMOD、项目保存/包、原生 SST/EET/XML 回归；不得仅机械运行以上列表。记录命令、通过/失败/跳过和环境限制，广域失败与任务前基线比较。清理本任务所有临时文件前先确认归属，保留用户原文件与共享临时目录。

## 阶段跟踪与风险

- analyze：七项修订需求已确认，已同步当前 FR33。
- arch：ADR 已按新合同修订，状态保持提议。
- plan：本轮范围已确认，S07～S12 拆分落盘。
- dev/qa：S07～S12 本地实现与相关验证完成，结果及限制见下文。
- chronicle：新增[本轮增量记录](../../docs/changelogs/project-text-encoding/story-07-to-12/2026-11-14-001-language-reading-legacy-compatibility.md)，既有日志保持不变。

关键风险为历史解释被当前设置覆盖、同名 UTF-8 错过转码、策略变更被同指纹短路、原文参与身份导致丢译，以及 fallback 掩盖输出损坏。对应以事实优先、全字段写出、策略参与变化判定、可靠全版本迁移及严格输出校验控制。设置变更不改原文；来源更新失败保留前像，成功使用恢复点；来源插件永不修改。无待用户确认的产品问题。


## 本轮验证结果（S07～S12）

最终入口组 656 passed；codec 聚焦 207 passed，包含于后续 I/O 广域覆盖。I/O 广域原运行 1384 passed、1 skipped、1 failed；唯一解析 spy 签名适配后，完整该测试文件 11 passed，未重跑整个广域，不改记为同次全绿。

项目/store 广域 382 passed、1 skipped、5 failed；五项旧来源路径断言在本轮开始基线相同复现（该文件 5 failed、2 passed）。排除该文件的最终项目组 381 passed、1 skipped、7 deselected。独立复审两项问题已修复，相关额外聚焦 25 passed；各组有重叠，不累计总数。

真实 BSAssets.esm 8,758 条、BSHeartland.esm 11,317 条，总计 20,075 条，legacy-v1 输入绑定、实际发布/fidelity 及 strict-v1 UTF-8 输出回读通过；原插件和原数据库逐字节不变。旧项目副本无修改保存补语言，原文译文状态不变；双 variants 同文件 Polish 更新保留全部，English 更新准确迁移 1 条原文并标待复核，无新增/删除和丢译。UI 专项 7 passed，10/14 pt 几何测试通过，默认字号截图已实际查看。

最终 Ruff 检查、2682 文件格式检查和 Git 差异空白检查通过。未跑全仓 pytest、用户项目原位迁移、可见桌面完整交互或外部 xTranslator/EET 写出联动。实际命令、复验与基线证据见本轮记录；任务临时文件由主会话在交付前确认归属后清理。

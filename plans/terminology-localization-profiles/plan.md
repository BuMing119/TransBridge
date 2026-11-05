# terminology-localization-profiles（可切换译名方案）

**对应需求**: FR5.18
**架构决策**: ADR-039
**技术模块**: application + persistence + bootstrap + ai_translator + ui + io
**状态**: Story 01～10 本地实现与 QA 已完成；Story 11～13 行为已确认、实现契约提议，本地实现与相关 QA 完成（2026-11-05）
**创建日期**: 2026-09-06

## 目标与边界

同一 Project 支持两种工作方式：只维护 A 正文、临时预览/导出 B，或显式保存独立 B Variant 后分别维护。现有 Variant→profile/revision 关联唯一表示实际保存正文的方案，预览目标只在会话中存在。方案切换不改正文、不创建 Variant，AI 只依据当前实际 Variant 方案。

本轮按共同术语 original+scope 从实际 A 译名转换为 B，仅当词条 original 命中相应术语时替换中文 translation，重复出现正常全部替换；无变化跳过，original 不变。不增加第二持久选择、不要求复杂人工绑定、不自动同步 A/B，不改导入、上传或错误词库/特殊占位符防御性规则。

Story 01～10 是已完成实施历史，其当时的持久预览选择、唯一位置识别、AI 共享选择等验收由本次 FR5.18、ADR-039 与 Story 11～13 替代；保留历史记录，不重新将旧功能标为未实施。

## Story 01：领域契约与版本化存储

**依赖**: 无
**主要落点**: `src/transbridge/application/terminology_profiles/`、`src/transbridge/persistence/terminology/`

**验收标准**:
- [x] 定义术语版本、不可变已发布修订、逻辑术语映射、条目级例外、受控出现位置、选择状态与诊断契约
- [x] 术语版本与 Project Variant 正交；选择键为 `(project_id, variant_id)`，版本内容归属 Project
- [x] SQLite schema 以备份优先方式升级，旧项目打开后默认保持无术语版本、行为不变
- [x] 提供创建、复制、重命名、归档、发布、列出和选择用例；归档当前版本时安全清除选择
- [x] 发布修订不可原地修改，版本号与内容摘要稳定且可复现

## Story 02：安全术语投影与历史识别

**依赖**: Story 01
**主要落点**: `src/transbridge/application/terminology_profiles/projection.py`、`recognition.py`

**验收标准**:
- [x] 派生译文优先使用条目级例外，其次使用已绑定出现位置，最后才尝试唯一可证明的历史译名识别
- [x] 不执行无边界的中文全局替换；重叠、同形、多候选或无法证明时保留整条公共译文并返回可见诊断
- [x] 多个不重叠术语按稳定顺序一次性投影，避免前一个替换结果被后一个再次命中
- [x] 从版本 A 切换 B 再切回 A 时输出完全一致，公共 `VariantEntryState.translation` 始终不变
- [x] 缺失映射不跨版本回退，不存在术语的条目保持字节等价文本

## Story 03：生效术语与 AI 运行快照

**依赖**: Story 01、Story 02
**主要落点**: `src/transbridge/application/terminology_profiles/effective.py`、`src/transbridge/bootstrap/terminology.py`、`src/transbridge/application/translation/terminology_run_snapshot.py`

**验收标准**:
- [x] 在现有生效术语端口外叠加版本映射，保持原 TermDecision 权威和 scope 优先级不变
- [x] 未选择版本时完全复用现有生效术语版本与摘要，旧 AI checkpoint 可继续恢复
- [x] 选择版本后，AI 提示词只接收该已发布修订的目标译名，缺失项不会借用其他版本
- [x] AI 任务开始时冻结基础术语版本、术语版本修订与组合摘要；运行中切换不影响已启动任务
- [x] 恢复任务时校验同一组合快照，错配时给出可操作错误

## Story 04：快速切换与管理界面

**依赖**: Story 01～03
**主要落点**: `src/transbridge/ui/workbench/terminology_profile_bar.py`、`src/transbridge/ui/tools/terminology_profiles/`、`src/transbridge/ui/workbench/translation_table.py`

**验收标准**:
- [x] Workbench 顶部提供独立于 Variant 的术语版本选择器，并明确显示“公共译文”与当前版本
- [x] 提供管理窗口完成创建、复制、重命名、映射编辑、发布与归档，未发布草稿不会被选择器用于生产投影
- [x] 切换后当前可见表格在 500ms 目标内刷新派生译文，不重载或重写公共集合
- [x] 派生值与公共译文不同时，表格明确标识且禁止把派生文本误写回公共译文；清除配置档后恢复公共译文编辑
- [x] 不完整或有歧义的映射在受影响条目显示诊断，切换失败时恢复并保持旧选择

## Story 05：导出与跨入口一致性

**依赖**: Story 02、Story 03
**主要落点**: `src/transbridge/application/io/`、`src/transbridge/bootstrap/`

**验收标准**:
- [x] GUI 与智能助手写出入口在格式适配器前接收临时投影条目，适配器和内存公共集合均无需感知术语版本
- [x] 一次写出冻结 Variant revision、基础术语版本和术语版本修订；运行中切换不改变该次输出
- [x] 相同输入与组合快照产生相同输出；导出结果可追溯所用术语版本名称、修订与摘要
- [x] ParaTranz 下载与现有同步仍只处理公共译文；未建立显式远端配置档映射时不上传 profile overlay
- [x] CLI/MCP/GUI 未指定术语版本时保持历史兼容行为

## Story 06：回归、性能与追溯收口

**依赖**: Story 01～05

**验收标准**:
- [x] 覆盖 A→B→A、仅术语条目变化、缺失映射隔离、歧义不替换、条目例外和不可变修订
- [x] 覆盖旧 schema 迁移、未选择版本、归档当前版本、并发发布/选择、AI 恢复与导出快照一致性
- [x] 覆盖 UI 切换、派生值只读保护、失败保护和切换响应时间
- [x] 执行相关 pytest、Ruff check 与 Ruff format；记录未执行的真实游戏资产、联机与人工桌面验证
- [x] 根据真实差异更新 Requirement、ADR、Plan 状态、索引与 QA 证据

## Story 07：通用产品定位与 AI 可见选择

**依赖**: Story 01～06
**主要落点**: `src/transbridge/ui/workbench/`、`src/transbridge/ui/tools/terminology_profiles/`、`src/transbridge/ui/tools/ai_translator/`

**验收标准**:
- [x] 产品界面统一使用“译名方案”，将“本体汉化”降级为使用模板，内部 `TerminologyProfile` 与存储契约保持兼容
- [x] 工作台使用项目译文、应用方案等用户语言，不在主选择器暴露 revision、派生或投影术语
- [x] AI 页签上方固定显示并可切换当前译名方案，与“术语来源”页签明确分离
- [x] 工作台与 AI 窗口共享同一选择控制器，任一入口切换或管理更新都会同步另一入口和预览
- [x] AI 运行摘要显示当前方案，任务启动时继续冻结精确技术版本且不写入任务预设
- [x] 更新用户语言契约测试并通过相关 UI、应用层与静态检查

## Story 08：从单个术语来源创建译名方案

**历史范围**: 本节记录已完成的首版；外部来源的全量导入、冲突策略与无底板行为由 Story 10 修订，当前要求以 FR5.18.25 为准。

**依赖**: Story 01、Story 03、Story 07
**主要落点**: `src/transbridge/application/terminology_profiles/importing.py`、`src/transbridge/ai_translator/term_database.py`、`src/transbridge/ui/tools/ai_translator/`、`src/transbridge/ui/tools/terminology_profiles/`

**验收标准**:
- [x] 术语来源列表项保存稳定来源 ID，用户可从当前选中来源发起创建，不依赖显示文案反推身份
- [x] 动态词库、ParaTranz、JSON、CSV 与 Excel 共用单源读取边界；网络和大文件读取不阻塞 Qt 主线程
- [x] 应用服务以当前已发布项目术语为完整骨架，唯一命中采用来源译名，其余保持项目译名，并稳定统计重复、冲突、作用域不明确和来源独有项
- [x] 创建前预览来源、快照语义、处理统计与逐项结果；空来源、无项目术语版本和读取失败不产生持久化状态
- [x] 确认后创建并发布独立方案，默认不改变当前选择；用户明确要求时才设为当前方案并同步工作台与 AI
- [x] 覆盖单源加载、合成规则、发布/选择、错误恢复和 UI 入口测试，并通过相关 pytest 与 Ruff 检查

## Story 09：译名方案资产入口归位术语工作台

**依赖**: Story 04、Story 08
**主要落点**: `src/transbridge/ui/tools/terminology/`、`src/transbridge/ui/tools/terminology_profiles/`、`src/transbridge/ui/tools/ai_translator/`

**验收标准**:
- [x] 项目术语工作台新增独立“译名方案”区域，集中提供当前方案切换、来源创建和方案管理
- [x] 来源选择器列出项目插件动态词库、已绑定 ParaTranz 术语和已配置本地来源，并允许直接浏览 JSON、CSV、Excel 文件
- [x] 来源读取、预览、发布和可选立即使用继续复用既有单源快照与后台任务边界，不在术语工作台复制领域逻辑
- [x] AI 翻译任务移除就地创建流程，只保留前往项目术语工作台的快捷入口，同时继续显示和消费当前方案
- [x] 精简运行入口在缺少方案服务时显示不可用状态，不影响既有术语构建、版本和报告页面
- [x] 补充入口归属、来源发现、切换和跳转回归，并通过相关 pytest 与 Ruff 检查

## Story 10：外部来源完整导入与整批译名冲突策略

**状态**: 本地实现与独立 QA 完成（2026-11-04）；技术设计记录为草稿，需求范围已确认
**依赖**: Story 03、Story 08、Story 09
**对应需求与决策**: FR5.18.25、ADR-039 第 2、6、10 节修订

### 目标、当前事实与非目标

用户从外部来源创建新方案时，两种策略均导入全部有效新词；已存在同键译名由“保留已有译名”（默认）或“采用导入译名”统一处理。创建和选择不修改底板、其他方案或正文。

实施前 `importing.py` 仅遍历底板有效决定，来源独有词被计数但丢弃；`effective.py` 仅投影底板决定；`browse_queries.py` 把所有无底板映射标为 `BASE_MISSING`。导入控制器和应用服务均拒绝无底板，组合版本编码也不能表达固定的空底板。旧数据及测试明确要求孤立旧映射不自动生效，因此只扩大遍历范围不足以实现兼容修改。

本轮不自动继承以后发布的底板、不重做所有术语问题、不增加逐词冲突决定、不直接修改既有方案、不改变“基于项目术语创建方案”的入口要求，也不实现来源直连或自动同步。正文自动适配仍受历史译名与位置证据约束。

### 实施步骤与文件归属

1. **建立独立词权威与兼容持久化**：在 `application/terminology_profiles/models.py` 增加显式独立标记，默认关闭。无该标记的序列化与摘要维持旧格式；新标记参与内容摘要。更新 `persistence/terminology/profiles.py` 的严格读取，必要时将完整内容编码职责提取为 `profile_content_codec.py`，避免继续扩大已接近 500 行的仓储模块。复制、发布、编辑往返均保留标记。
2. **完整导入与统一策略**：在 `importing.py` 和 `import_preview.py` 增加两种策略、作用域逻辑键、固定顺序的重复处理和新增/覆盖/保留结果。保留创建时有效底板骨架；两种策略均增加来源新词。`ai_translator/term_source_reader.py` 只保留可信结构化作用域，普通无作用域来源归 project，不猜测同名插件身份。来源摘要覆盖顺序、作用域和译名。无版本/空版本允许导入，损坏或不可用版本拒绝导入。
3. **生效查询、冻结与输出边界**：在 `effective.py` 只为显式独立且底板不存在的映射派生稳定有效决定，不复活底板抑制项；在 `browse_queries.py` 区分独立有效词与旧孤立映射。组合引用精确表示“无底板”，恢复不得跟随之后的新版本。验证 `entry_projection.py`、`projection.py`、`write_projection.py` 与 AI 运行快照边界；不以原文或来源译名伪造历史译名证据。
4. **用户入口与只读预览**：在 `ui/tools/terminology_profiles/source_picker_dialog.py`、`import_dialog.py`、`import_preview_model.py`、`source_import_controller.py` 与 `ui/workbench/terminology_profile_controller.py` 接入两个中文策略，默认保留；读取来源前选择策略，预览固定并显示该选择，确认创建使用当前所见结果。`mapping_model.py` 编辑时保留独立标记。必要的 `source_browser.py` 状态文字变更须与既有未提交修改并存，不覆盖用户改动。
5. **验证与追溯**：针对真实变更增加应用、持久化、AI 快照、预览/写出和 UI 回归；运行相关测试与 Ruff，记录真实未执行项目；由主会话根据实际结果更新状态和增量记录，不提前标为完成。

### 验收标准

- [x] 底板含 A、来源含不同译名 A 与独有 B：默认保留 A，采用策略覆盖 A；B 在两种策略均进入新方案，底板和其他方案内容摘要不变。
- [x] 来源无命中、尚无底板、已发布空底板均可创建并立即发布包含有效来源词的独立方案；空来源、读取失败、损坏或不可用底板不产生方案。
- [x] 来源选择器只提供两个整批策略，预览明确显示所选策略、新增、覆盖、保留数量及只读明细；确认创建使用同一固定预览，取消不写入方案。
- [x] 规范化等价但原始译文文本不同（如 `字母A` 与 `字母Ａ`）时，保留策略保持原有文本；采用策略保存来源文本并计为覆盖。完全相同的既有译名计为保留，三类数量完整覆盖最终映射。
- [x] 同键且译文文本相同的记录计为同译重复；规范化等价但文本不同仍计为来源差异。同键异译按固定来源顺序在保留策略下保留首值、采用策略下保留末值；摘要覆盖顺序，重复结果稳定可复现。
- [x] 无作用域来源只产生 project 逻辑键；明确 plugin 作用域保持插件身份。同名不同作用域不误覆盖，不广播替换全部插件译名。
- [x] 选择新方案后独立词出现在生效浏览与 AI 查询，取消选择或切换其他方案后不泄漏；既有抑制决定不复活，旧孤立映射仍未生效。
- [x] 无底板时冻结的 AI 快照在之后发布底板、修改或切换方案后仍可精确恢复；旧普通版本与旧组合版本引用保持兼容。
- [x] 无独立标记的旧 payload 和内容摘要保持不变；新标记经 SQLite、复制、发布和管理器编辑往返仍保留；非法标记值失败保护。
- [x] 来源独有词缺少历史译名时保留正文和导出译文并给出诊断；已确认位置绑定按既有规则工作，不进行全文猜测替换。
- [x] GUI 与 AI 共享选择；未主动选择时创建不改变当前方案。后台读取、项目切换保护和取消行为保持可用。

### 工作分配与依赖顺序

- Astra：需求、架构与本计划，随后独立审查和 QA；不修改业务代码。
- Sol 核心：应用导入/模型/生效/浏览、持久化内容编码、来源读取与相关测试。
- Sol UI：导入预览/控制器、管理器映射编辑、状态文案与 UI 测试；与核心先对齐策略与预览字段，不修改核心文件。
- 主会话：协调接口、集成验证、问题回流、最终清理和增量记录。核心契约先固定，UI 可并行实现，独立审查在两者提交实际工作树结果后执行。

### 假设、风险与回退

用户确认的是完整导入和两个整批策略；同批重复的首/末值规则、无作用域默认 project 及显式独立标记是本次基于现有模型提出的实现决策，不冒充用户逐项批准。来源有效性继续遵守既有读取器契约，不放宽文件校验。

新增独立词仅归方案，旧孤立映射不迁移；需要主动重新导入才能获得新语义。旧读取器若不能识别独立字段，应拒绝该新方案；回退先取消选择并恢复兼容备份，不删除独立字段伪造旧摘要。无底板仅指明确尚无版本，不能用来掩盖损坏或读取错误。保存任务既有未提交改动，并在结束时只清理本任务拥有的临时目录。

### 验证计划与阶段

- [x] 读取现有需求、ADR、调用链、测试与工作区差异；确定范围和兼容边界。
- [x] 原位修订 FR5.18.25、ADR-039，并形成 Story 10 设计草稿与文件分工。
- [x] 核心与 UI 实施，完成针对性测试。
- [x] Astra 独立审查，修复发现并完成集成验证。
- [x] 主会话完成增量记录与任务临时文件清理；验证证据和 Story/索引状态已更新。

优先运行 `tests/application/terminology_profiles`、`tests/persistence/terminology/test_profiles.py`、相关 `tests/ui/tools` 与 `tests/ui/workbench` 测试，再覆盖来源读取、AI 快照和写出合同。使用仓库已有 uv 环境；本次工作区已有无关 `pyproject.toml`/`uv.lock` 修改，可用 `uv --cache-dir <本任务临时目录>/uv-cache run --no-sync ...`，避免顺带同步依赖。并行测试分别使用本任务目录下独立 `--basetemp`。最后运行 `ruff check src tests` 与 `ruff format --check src tests`，不要求真实 LLM、ParaTranz 网络或用户游戏资产。

独立 QA：最终直接相关回归 `173 passed`；扩展集成首次 `360 passed, 2 failed`，两项报告硬链接测试经环境归因后退出 sandbox 复验 `2 passed`；窄窗口 `4 passed`。以上批次有重叠，不累加。全库 Ruff 检查与格式检查通过；详见 [QA 报告](../../docs/test-reports/terminology-source-import-2026-11-04.md)。

本次归档：[Story 10 增量记录](../../docs/changelogs/terminology-localization-profiles/story-10-complete-source-import/2026-11-04-001-complete-source-import.md)。已核验并清理本任务临时目录；原有 30 个无关已跟踪差异逐块比较一致，共用文档与 `source_browser.py` 的已有改动亦保留。

## Story 11：实际正文关联与 A→B 转换

**状态**: 本地实现与相关 QA 完成（2026-11-05）；行为已确认，技术契约提议
**依赖**: Story 01、02、03、10
**对应合同**: FR5.18.1～4、6～7、10～13、19、21；ADR-039 第 1、2、6、8 节

### 当前事实与落点

`TerminologyProfileSelection` 已含 profile/revision，但旧发布推动 revision、归档删除关联；SQLite schema 4 的选择代表预览偏好。`entry_projection.py`/`projection.py` 原从目标历史译名识别，重复译名会被唯一位置约束阻挡。计划在上述应用模块、`in_memory.py`、`service.py`、`effective.py`、`persistence/terminology/{schema,migration,profiles,profile_reads}.py` 原位修正；新增职责须独立模块，不扩大超限类。

### 实施与验收

- [x] schema 升级为 5，先现有摘要备份/完整性检查，再清除 schema<5 的旧预览偏好，正文/方案/术语版本不变；迁移证据记录重置，交付说明兼容影响，重复打开保留新实际关联。
- [x] 实际关联固定 revision，发布与归档不悄悄改关联；归档版本仍能按固定引用读取；无关联旧版本保留基础术语和 AI 行为。
- [x] `ProfileEntryProjector(target, base, source_profile=actual)` 接受明确来源方案；共同 original+scope 生成一次性非级联替换，原文命中才替换，重复全部替换；original、stage、标签和无变化条目保持原值。
- [x] A/B 不同 revision 不能因相同 profile ID 被视作同一内容；完全相同固定输入直接返回原文。无关联基础来源只取明确共同术语快照，不从旧 selection 或目标 base_translation 猜测。
- [x] AI 读取实际关联，旧普通/组合 checkpoint 与显式无底板仍可恢复。

**验证**: 应用投影/entry projection/有效术语测试，SQLite migration、publication、archive、profile service 测试；包括重复出现、无命中中文同名、scope 隔离、缺映射、无变化、旧选择重置与迁移回滚。

## Story 12：全工作台临时预览、AI 分离与文件导出

**状态**: 本地实现与相关 QA 完成（2026-11-05）；行为已确认，技术契约提议
**依赖**: Story 11
**对应合同**: FR5.18.2、5、9、11、14～17、22；ADR-039 第 3、6、7 节
**主要落点**: `ui/workbench/terminology_profile_controller.py`、现有 profile preview/coordinator/table、`ui/tools/ai_translator/`、`application/terminology_profiles/write_projection.py`、`application/io/` 与现有 bootstrap 写出接线。

### 实施与验收

- [x] UI 状态区分 selected/preview revision 和 applied/actual revision；选择不写 repository，刷新同 Variant 保留临时目标，切换 Variant 重置为实际方案。
- [x] 后台捕获完整 Variant 全部来源条目，预览覆盖分页、筛选之外；generation、identity/revision 丢弃迟到结果，切回 A 直接读保存正文。
- [x] 临时 B 预览保护 A 不被普通表格编辑覆盖；实际 A/B 正文均保持普通编辑能力。界面能辨别实际正文与预览目标。
- [x] A 上临时预览 B 时 AI 展示和冻结 A；新活动 B 才使用 B。AI 不把临时选择写回实际关联。
- [x] 现有文件导出显式固定会话目标，复用完整转换；预览 B 导出成功而不增加 Variant，不改 A。没有临时目标导出实际正文，CLI/MCP 默认行为保持。

**验证**: workbench switching/async refresh、多页多来源 projection、AI 上下文、write projection 和 IO 合同；测试失败恢复、快速 A→B→A、刷新与 Variant 切换。后台/网络入口不新增实际外部服务依赖。

## Story 13：独立 B 保存、明确覆盖与可恢复一致性

**状态**: 本地实现与相关 QA 完成（2026-11-05）；行为已确认，技术契约提议
**依赖**: Story 11、12
**对应合同**: FR5.18.8～9、13、16、18～19；ADR-039 第 4、5 节及 ADR-035
**主要落点**: `application/projects/` 新窄保存用例、现有 project command facade、`persistence/v2/` activation journal/participant、术语 association adapter、`ui/shell/project_save.py` 与独立方案保存对话/控制器（计划新增，避免将新责任加入超限主类）。

### 实施步骤

1. 提供 `project_commands.save_profile_variant(name, context, *, target_profile, source_profile, target_variant_id=None, expected_target_revision=None, expected_project_revision=None, expected_variant_revision=None, base_snapshot=None)`；profile 为固定 published 对象或 None，校验实际来源/完整正文输入，不从当前页 collection 抓取。
2. 在已有 activation journal 的 participant 边界加入 SQLite selection row 的预/目标镜像与 CAS；prepared 恢复/回滚、committed 恢复验证目标，事务日志不作为第二选择字段。已有 Project/Variant journal 不能直接覆盖 SQLite，必须实现参与者后再开放 UI。
3. UI 预览 B 点击保存默认另存。已有 B 时显式选择新建/覆盖指定目标并捕获目标 revision；自动保存不得把临时预览当作用户另存意图。
4. 全部持久化与关联验证成功后激活 B，之后恢复普通编辑与 AI。取消、冲突和故障保持 A/原目标及可用预览；错误保留原因并可重试。

### 验收标准

- [x] B 正文和 B revision 关联真实保存，重开一致；A 正文/关联不变，B 独立修改不自动同步 A。
- [x] 保存包含完整来源/页集合，未变化条目保留 entry revision；target/source 同名不代表可以覆盖。
- [x] 默认新建、显式覆盖及取消可独立验证；覆盖必须匹配目标 revision，不静默覆盖任一已有 B。
- [x] 注入正文与 selection 写入之间、完成提交与 UI 激活之间的失败，重开可恢复且不显示混合状态；新建不留下悬空正式目标，覆盖不损坏原目标。
- [x] prepared/committed 重复恢复幂等；外部较新 revision/selection 不被回滚覆盖，明确冲突而非成功。
- [x] AI/普通保存可继续维护 B；只预览导出路径不生成 Variant。

**验证**: 新保存用例与 activation journal participant 故障注入测试、SQLite selection CAS、UI save/cancel/overwrite、重开验证；结合 projection/AI/export 回归确认两种工作方式。

### 本次共用验证、风险与收口

按 Story 11→12→13 集成。先针对性 pytest，再相关全域与 `uv run ruff check src tests`、`uv run ruff format --check src tests`。使用现有 uv 环境与锁，不修改既有依赖；临时文件限定 `.tmp-variant-schemes-pilot-20261105/` 所属子目录，和本轮 baseline 比较，不能将用户已有修改算成本轮回归。

责任审查：`bootstrap/terminology.py` 698 行仅调整实际方案生效入口；`gui_facade.py` 573 行只增加薄委托，完整新责任归 `ProfileVariantCommands`；`schema.py` 为数据声明，`step2.py` 700 行/48 方法仅转交 source_profile，不引入新职责。未做无关大范围重构。

旧 selection 重置和实际 revision 不再追随发布是明确兼容变化；备份与迁移证据必须验证。恢复协议涉及文件与 SQLite，不能以 UI 测试代替故障注入；不访问真实用户译文。回退需要匹配的数据库/Project/Variant 备份，不仅回退代码。迁移结果与具体 journal 文件落点以实施结果复核，未通过验证前不勾选完成。

本轮本地实现与 QA 完成：核心/术语SQLite/IO合同最终 245 项通过；工作台与方案管理 UI 113 项通过；AI runtime/adapter/UI 32 项通过。保存/恢复扩大批次 134 项通过、2 项系统临时目录权限失败，改为任务专有 TMP/TEMP 后相关 6 项全部通过；39 项新增保存/恢复测试均通过，批次有重叠不累加。一次 FAISS native 导入中断未算通过，最终以独立进程完整批次为证据。全库 Ruff 与格式检查由主会话最终复核通过（2381 文件），`git diff --check` 通过。

详见 [专项 QA](../../docs/test-reports/variant-scheme-preview-save-2026-11-05.md) 与 [Story 11～13 增量记录](../../docs/changelogs/terminology-localization-profiles/story-11-to-13-variant-schemes/2026-11-05-001-variant-scheme-preview-save.md)。未使用真实 LLM/ParaTranz/用户游戏资产，未构建安装包，临时目录由主会话最终清理。

## 实施结果与后续增强

- 首版核心闭环已完成：配置档持久化与不可变发布、Workbench 快速切换、只读安全投影、AI 组合快照、GUI/智能助手冻结写出及兼容迁移。
- 当前产品定位统一使用“译名方案”：工作台可临时预览目标，AI 与运行摘要只使用当前实际翻译版本关联；保存 B 后可独立维护。历史共享持久选择已由 Story 11～13 替代。
- 自动化 QA：扩展回归 `344 passed`；Ruff check 与 format check 全仓通过；合成性能样本（2000 条目、200 映射）约 `0.112s`。
- Story 07 自动化 QA：相关 UI、应用、持久化、AI 快照与集成回归 `468 passed`，补充接线/写出/兼容合同 `9 passed`；Ruff check 与 format check 全仓通过。
- Story 08 已完成：AI“术语库”可从动态词库、ParaTranz、JSON、CSV 或 Excel 的当前单源快照创建完整译名方案；预览明确采用、保持、冲突、作用域不明确、重复和来源独有统计，默认创建后不切换。
- Story 08 自动化 QA：核心新增边界 `22 passed`，相关 UI、AI、应用与集成回归 `683 passed`，持久化/写出/迁移补充回归 `74 passed`；Ruff check 与 format check 全仓通过。系统 Python 环境缺少 `xlrd`、`faiss` 和 `rank_bm25`，因此另一次包含可选依赖测试的 `696` 项运行有 `10` 项环境失败，非本功能回归。
- Story 09 已完成：项目术语工作台新增“译名方案”区域并成为来源创建、切换和管理的主入口；AI 翻译任务降为跳转入口，底层快照、预览和发布语义保持不变。
- Story 09 自动化 QA：新增与核心相关回归 `100 passed`，扩展 UI tools 与 Workbench 回归 `340 passed`；Ruff check 与 format check 全仓通过。
- 本轮未执行真实 Skyrim 插件/Strings 资产、ParaTranz 联机和人工桌面交互验证。
- FR5.18 的配置档差异视图、聚合影响统计、在 UI 中新建条目特例/精确出现位置绑定，以及显式 ParaTranz 配置档远端映射属于后续增强；底层模型已为特例和绑定保留契约。

## 架构依赖

- [ADR-039：可切换译名方案与非破坏性译文投影](../../docs/adr/039-terminology-localization-profiles.md)
- Project/Variant 权威快照与 `VariantEntryState.translation`
- 项目术语 `TermDecision`、effective snapshot 和 SQLite 迁移机制
- AI `TerminologyRunSnapshot` 与恢复校验
- Translation I/O `WriteRequest` 和现有格式适配器
- Workbench `TranslationTable`、TaskRuntime 与项目生命周期

## 风险与回退

- **历史译文无法可靠定位术语**：不替换并报告诊断；用户通过受控出现位置或条目级例外确认，不猜测。
- **版本切换污染公共译文**：投影服务保持纯函数，只接收不可变快照并返回临时文本；持久化仓储不提供“写回派生译文”接口。
- **AI/导出运行中版本变化**：任务启动时冻结组合摘要；后续切换只影响新任务。
- **旧项目兼容**：无选择记录即关闭版本投影；可通过清除选择即时回退到原行为，无需数据逆迁移。
- **UI 复杂度继续堆入 AppContext**：新增独立控制器和组件，通过窄接口接入 Workbench，不向超限类增加领域职责。

## 验证命令

- `uv run pytest tests/application/terminology_profiles tests/persistence/terminology tests/ai_translator tests/ui/workbench -q`
- `uv run ruff check src tests`
- `uv run ruff format --check src tests`

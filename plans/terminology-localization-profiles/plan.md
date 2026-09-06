# terminology-localization-profiles（可切换译名方案）

**对应需求**: FR5.18
**架构决策**: ADR-039
**技术模块**: application + persistence + bootstrap + ai_translator + ui + io
**状态**: 已完成（2026-09-06）
**创建日期**: 2026-09-06

## 目标与边界

让同一个 Project/Variant 只维护一份项目译文，同时按目标场景选择独立的译名方案。方案可承载本体汉化、地区/社群译名、团队/发行规范、系列旧译/新译与整合包约定；切换只改变预览、AI 术语约束、质量检查与导出的适配结果，不改写项目译文，也不借用 Project Variant 表示术语差异。

首版提供项目内术语版本的创建、复制、重命名、归档、发布与快速切换；允许每个逻辑术语在不同版本中保存不同目标译名，并通过明确绑定或唯一可证明的历史识别生成结果。普通中文全文替换、不同模组译文分叉、远端 ParaTranz 多分支发布和自动推断歧义术语不在本轮范围。

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

## 实施结果与后续增强

- 首版核心闭环已完成：配置档持久化与不可变发布、Workbench 快速切换、只读安全投影、AI 组合快照、GUI/智能助手冻结写出及兼容迁移。
- 通用产品定位与 AI 可见选择已完成：工作台、管理窗口、预览诊断和 AI 任务区统一使用“译名方案”；AI 与工作台共享选择并在运行摘要中显示当前方案。
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

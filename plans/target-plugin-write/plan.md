# 将已有译文套用到所选 ESP

- 状态：产品边界已确认；S01～S06 已完成本地实现与相关验证。未构建或发布安装包。
- 日期：2026-10-09
- 需求：[FR4.1 ESP 写回](../../docs/requirements.md#fr4-文件写回)；用户要求不先保存、更新项目来源就能直接向新版 ESP 套用当前译文。
- 架构约束：[ADR-016](../../docs/adr/016-modular-monolith-application-composition.md)；沿用现有 application I/O、TaskRuntime 与 staging/原子发布端口。

## 目标、事实与边界

当前 `HydratedWriteDraft` 将项目源快照作为输出模板，`target_path` 只是输出路径。仅修改该路径不能保留其他 ESP 的新记录。`source-file-update` 会迁移全部项目版本，属于项目维护，不作为本功能的前置步骤。

单插件写回窗口允许显式选择 ESP/ESM/ESL 作为本次模板，在后台解析并匹配当前译文；默认生成目标插件的翻译副本，也可显式确认覆盖并备份。项目来源、版本、原文、dirty 状态和磁盘项目均不改变。普通写回、XML、助手 `write_back` 和批量写回只使用各自来源快照，输出路径不得被解释为新的输入模板；来源或定位不符时整份文件失败，不能跳过失配条目后报告成功。

本功能不做模糊匹配、不自动接受原文变化、不强制先保存项目；没有来源 hydration 的只读恢复状态仍由既有恢复流程处理。跨版本正式项目维护继续使用“更新源文件”。CLI/MCP 新参数和安装包发布不在本轮范围。

## S01：以所选目标为基础匹配与发布

- 新增 `application/io/plugin_transfer.py` 及按职责拆分的匹配/快照保护模块；复用 `HydratedWritePreflightService`、`HydratedWriteWorkload`、插件保真验证及 bundle 发布。
- 接口：`PluginTransferDraft(write, plugin_path)`；后台 preflight 返回冻结目标源快照、匹配报告和底层写回预检；workload 消费同一冻结结果。
- 先固定原项目的术语投影，再以目标条目身份套用有效译文。不能直接复用旧 namespace、string ID 或源快照。仅对完整 key、原文及记录归属证据唯一匹配的条目写入，不使用索引模糊兜底。
- 原文变化、缺失、歧义、未翻译、隐藏或阶段不允许的条目逐条跳过并报告，不阻止其他可写条目；无可写条目时明确提示，不生成无意义成品。
- 空白原文仍参与完整定位。目标未匹配记录与非文本内容保留。localized 共享 ID 以全部物理引用为组检查，不允许套用一条间接改变未匹配引用。
- 冻结目标插件、Strings 和已有输出的指纹；执行前及最终提交时复核。覆盖确认不允许忽略预检后变化；取消、失败、备份与回滚沿用既有发布语义。
- 验收：真实 ESP 增删改、同 key 原文消歧、空白、不同 namespace、改名、master 归属、本地化及共享 ID、输出覆盖及目标中途变化；输出来源为新文件，旧项目不变。

## S02：一次写回完成选择、匹配与输出

- 将 `ui/operations/production.py` 中既有写回职责提取为内聚的 feature/plan/batch 模块，production 只装配；不在已有 593 行组合模块追加新领域职责。
- `WritePlanDialog` 增加目标 ESP 浏览、输出浏览与明确覆盖选择。所选 ESP 与输出路径分开，选择后自动给出翻译副本路径；可清除选择恢复当前插件导出。
- 新模板解析与匹配在既有 `ApiWorker` 预检模式运行；提交只调度已冻结 workload，不在 GUI 再解析。预检期间正确管理按钮、关闭、过期回调与确认令牌。
- 显示匹配统计和具体跳过原因，成功/部分完成保留实际输出路径与报告。生产调用链不触发项目更新或保存。
- 验收：真实 Qt 选择 → 后台预检 → 确认 → TaskRuntime → 文件输出，输入错误可重试，关闭无泄漏，已有 XML/批量/结果反馈回归通过。

## S03：独立验证与增量记录

- 先运行新增应用及 UI 测试，再覆盖 I/O 契约、publish、parser/writer、operations、来源更新相关兼容测试。
- 全库 `uv run --no-sync ruff check src tests`、`uv run --no-sync ruff format --check src tests` 和差异审查。
- 仅更新直接相关需求、计划索引和新增 changelog；保留当前工作区其他改动及历史增量记录。

## S04：严格入口与单插件套用隔离

- 普通、批量、助手保持严格 HydratedWrite 路径；只有 GUI 单文件显式选择新模板进入 PluginTransfer。
- 批量按各自来源预检完整批次、输出冲突与覆盖策略；可以部分文件成功，但单文件不得容忍失配条目。输出目录中的同名 ESP 不参与模板匹配。
- 删除旧工作台卡片中绕过发布链的 Plugin/XML 直接 writer 回退；缺失正式上下文时提供恢复指引。保留独立 DSD 导出能力。
- 助手缺少有效 source snapshot 时拒绝写回，失败或部分提交保留真实诊断与输出证据。不新增助手套用工具、批量套用、正式 MCP/CLI 写回能力。
- 落点：`ui/workbench/cards/write_card.py`、`ui/operations/write_batch.py`、`smart_assistant/tools/tool_writer.py` 与对应回归。

## S05：FOMOD 和发布守卫闭环

- FOMOD 继续解析新版、迁移/TM/AI 决策，再严格写回新版集合。保留冲突/待确认阻断，禁止引用 PluginTransfer 的宽松保留策略。
- 从超过 500 行的 pipeline 提取专责插件发布模块，使用统一暂存、保真检查与提交，不直接覆盖待打包 ESP；工作副本不生成会混入包的长期备份，原归档保留。
- 普通单文件发布在渲染后和提交守卫内复核输出指纹。覆盖只授权预检看到的版本，不授权覆盖运行期间的外部更改。
- 验收：真实插件身份/原文失配整文件失败，暂存或验证失败保留旧目标，取消阻断提交，FOMOD 输出包不含发布临时物，已确认覆盖后目标变化仍拒绝。
- 落点：`fomod/pipeline.py`、新建 `fomod/plugin_publishing.py`、`application/io/publish/coordinator.py` 及聚焦回归。

## S06：跨入口回归与增量记录

- 运行 publish、GUI operations/旧卡片、FOMOD、assistant writer 及关联契约回归，重点验证严格/套用两种语义不串用。
- 全库 Ruff、format 与差异检查；审查已有未提交改动的边界，增量记录只收录本轮归属明确的变更。

## 依赖、风险与回退

S01 与 S02 以冻结接口并行，S03 依赖两者。风险集中在跨版本同定位误配、共享 Strings 的间接修改、预检后文件变化和 GUI 后台生命周期，均以真实二进制/线程回归验证。原导出路径独立保留；新模式失败不修改项目，正式目标只在验证与提交守卫通过后替换。

S04 与 S05 分入口实施，S06 统一验收。严格写回不依赖套用匹配器；批量聚合检查所有主文件和伴随文件输出冲突，执行已确认投影。FOMOD 冻结完整候选来源集合，保留原归档，不把发布临时物打包。

## 执行状态

- S01：完成。新增目标匹配、快照保护与执行服务；40 项真实插件回归通过。
- S02：完成。浏览/副本路径/覆盖确认、后台预检、报告与旧令牌失效已集成；77 项操作界面回归通过。
- S03：完成。广泛回归 459 passed、1 skipped；语言处理补充后的最终回归 117 passed，Ruff 及差异检查通过。详见[增量记录](../../docs/changelogs/target-plugin-write/story-01-03/2026-10-09-001-target-plugin-write.md)。
- S04：完成。普通/批量/助手保持严格；删除旧直接写回分支，保留独立 DSD 导出；完整批次输出冲突与终态证据已验证。
- S05：完成。FOMOD 专责模块固定新版来源及候选 Strings 后严格发布；普通覆盖也拒绝运行期间目标变化。24 项 FOMOD 新增真实回归通过。
- S06：完成。三组最终回归分别为 344 passed / 1 skipped、165 passed、181 passed（去重 652 passed / 1 skipped）；全库 Ruff、1592 文件格式检查及差异检查通过。详见[严格入口增量记录](../../docs/changelogs/target-plugin-write/story-04-06/2026-10-09-001-strict-write-boundaries.md)。

正常保留项返回完成结果、警告与 skipped 计数；仅底层真实发布部分失败沿用 PARTIAL 语义。Qt 测试统一保持应用实例至测试会话结束，覆盖真实 ApiWorker 连续运行。离屏截图复核了选择项、顶部统计、滚动详情和页脚布局；临时截图已清理。

未运行全仓库测试或安装包构建；未使用用户原始 Neiva ESP 现场复验。本地化目标仅有归档内 Strings、没有可写独立 Strings 时明确阻止。项目 schema 无变化，无数据迁移。

未决产品问题：无；原文变化默认保留目标并报告，后续人工处理不在本轮增加强制套用开关。

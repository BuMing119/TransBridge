# 项目术语库与 ParaTranz 备份/双向同步计划

- **状态**：S00～S08 功能实现完成、离线 QA 通过；正式发行门禁 OFF
- **日期**：2026-08-30
- **Feature slug**：`project-terminology-paratranz-sync`
- **对应需求**：[FR5.17](../../docs/requirements.md)、FR5.2、FR5.13.4、FR5.16、FR22.2～FR22.8
- **架构**：[ADR-023](../../docs/adr/023-local-project-paratranz-binding.md)、[ADR-027](../../docs/adr/027-canonical-terminology-format-adapters.md)、[ADR-034](../../docs/adr/034-project-terminology-build-versioning-reporting.md)（提议）、ADR-016、ADR-018、ADR-019
- **前置 Plan**：`project-terminology-build-versioning-reporting` S02/S07～S10、`paratranz-project-binding`、`paratranz-sync-service-v2` S01～S04、`terminology-format-compatibility`

## 2026-08-30 实现与验收状态

- S00～S08 的生产代码、受控合同、持久化迁移、GUI、Agent/MCP、AI 运行快照和回归测试已实现；离线聚焦套件为 `198 passed, 1 skipped`，相关大回归为 `557 passed`。
- `ruff check src tests`、`ruff format --check src tests` 和 `git diff --check` 已通过；跳过项是必须由显式凭据和专用 ParaTranz 测试项目驱动的 live 合同测试。
- 正式发行门禁保持 **OFF**：尚无本轮可引用的脱敏 live ParaTranz 合同样本，且前置 FR5.16 S12 正式性能门禁仍为 OPEN/未通过。S08 的受控 HTTP 与性能清单只提供诊断证据，不冒充正式发行证据。
- 完整证据、风险和迁移说明见 [FR5.17 QA 报告](../../docs/test-reports/project-terminology-paratranz-sync-qa-2026-08-30.md)。

## 目标

- 由用户显式选择“备份已发布版本”或“双向同步”，默认不在打开 Project、切换 Variant 或启动 AI 任务时访问 ParaTranz 术语写接口。
- 只向当前本地 Project 通过 ADR-023 显式绑定且执行前重新验证的 ParaTranz 项目读写术语，不读取浏览页选择或最近项目。
- 以 FR5.16 不可变已发布术语版本作为本地唯一权威，通过三方比较生成确定、可分页检查、可失效的同步计划。
- 将 ParaTranz 的真实新增、修改和删除保存为可追溯的入站候选/冲突，再由用户复核并经现有 draft/publish 边界生效。
- 保存同步基线、远端身份、目标、逐项结果和未知结果，阻断回声、旧值复活、重复副作用和跨 Variant 覆盖。
- 在每次翻译、润色、混合或自定义 AI 任务的运行档案中固定术语版本与内容摘要，任务中的所有批次读取同一不可变快照。
- GUI、Agent 和 MCP 复用同一 application use case、计划哈希、确认、权限、TaskRuntime 和结果模型。

## 非目标

- 不实现后台、定时、实时或 Project/Variant 切换触发的自动术语同步。
- 不把 ParaTranz 变成本地版本、草稿、人工决定或审计历史的唯一存储。
- 不自动选择冲突译名，不把远端变化直接写入 effective version，也不把远端删除直接转换成本地物理删除。
- 不把插件特例、抑制状态或其他 ParaTranz 无法保真表达的语义静默扁平化为项目全局术语。
- 不改写通用翻译词条同步的 `application.sync` 领域模型，也不使用批量导入接口绕过逐项身份、结果和重试记录。
- 不在本 Plan 内完成 FR5.16 S12 尚未通过的正式性能发布门禁；FR5.17 的发行验收必须引用一份通过的 FR5.16 基座证据。

## 当前实现事实与关键约束

- FR5.16 已实现项目隔离 SQLite、不可变 `TerminologyVersion`、active draft、发布/恢复、`EffectiveTerminologyPort` 和 GUI 工作台；其 Plan 当前记录 S00～S11 已完成、S12 正式性能收口未通过。
- `EffectiveTerminologySnapshot` 已包含 Project、Variant、version ID、content digest 和排序后的 decisions，可作为本地同步输入和 AI 运行固定快照；非 ready 快照不得假装为空版本继续同步。
- `ProjectTerminologyAdapter` 已使已发布项目术语在当前作用域覆盖 legacy 来源，并让 suppression 阻止同作用域 fallback；现有同步基线尚不能按 ParaTranz remote ID 识别“本项目版本的远端回声”。
- `project_terminology_runtime.resolve_project_terminology()` 当前按调用时活动 Project/Variant 构造 context，未固定 version ID；部分 worker 仍有运行中重新解析 binding 的 fallback。`AiRunSpec` 也未保存术语版本与摘要，因此当前实现不满足 FR5.17.3。
- ADR-023 的 `ParaTranzTargetResolver` 已将 Project 持久绑定与浏览状态分离，并携带 binding revision、endpoint 和 account；绑定属于 Project，当前没有 Variant 级术语同步映射。
- `application.sync` 已有计划哈希、确认令牌、stale 检查、逐项结果、取消和 retry token，但 DTO 绑定翻译 `EntryKey`、stage 和本地集合 UoW。术语同步只复用这些模式/基础合同，不把 `TermDecision` 伪装成翻译词条。
- typed `ParaTranzPort` 当前只覆盖 projects/entries/artifacts。术语仍由原始 `ParatranzTermsAPI` 提供分页 list/create/update/delete，响应字段、分页终止、远端修订和超时后副作用判定尚无 typed contract。
- `application/terminology/models.py`（724 行）、`ai_translator/term_database.py`（896 行）、`ui/tools/terminology/window.py`（537 行）、`smart_assistant/tools/tool_paratranz.py`（847 行）已超过仓库责任复审阈值；本功能不得向这些文件加入新职责，只允许窄字段/委托，并优先新增完整可测切片。
- `docs/requirements.md` 中 FR5.17 是当前工作区的未提交用户修改；实现和后续文档更新必须保留该改动。

## 已确认的实施前校准结论

以下三项原为需求中的实施前校准点，用户已于 2026-08-30 全部确认。本 Plan 固化以下可逆方案；后续若结论变化，只调整相关 profile、planner policy 和迁移设计，不削减 FR5.17 已确认的双向同步范围。

- **插件作用域**：首版只自动同步 ParaTranz 可保真表达的 Project 全局术语；插件特例及依赖该特例的 suppression/replacement 一律 `lossy_mapping` 跳过并在计划中可见，不提供“扁平化上传”开关。
- **Variant 映射**：同一本地 Project 的一个 ParaTranz 目标同一时刻只允许绑定一条术语同步 Variant line。首次成功写入建立映射；切换映射必须先预检并显式确认，旧 line 的 baseline 保留只读审计，不自动清理远端。
- **删除策略**：只允许删除/覆盖能由同步基线证明为 TransBridge 管理且仍指向同一 remote ID 的远端术语；独立远端术语永不因备份缺失而删除。删除默认只进入计划，执行前逐项可见并使用破坏性确认；无法证明归属时改为冲突/跳过。
- **修订降级**：若 ParaTranz 术语接口没有稳定 revision/ETag，则以规范字段、remote ID、目标身份和响应元数据生成 `observed_digest`，同时记录 `observed_at`；计划执行前重新读取并比对，不把本地时间戳伪装成服务端修订。

## 总体设计与责任边界

- 新增 `src/transbridge/application/terminology_sync/`，承载同步 line/profile、规范远端快照、三方 planner、入站 change set、执行 use case、结果与 ports。该包只依赖 application/domain 合同，不导入 Qt、HTTP client 或 sqlite3。
- 新增 `src/transbridge/application/ports/paratranz_terms.py`，定义 typed `ParaTranzTerm`、分页快照、远端写结果和术语专用 port；不扩大翻译 entries 的 `ParaTranzPort` 语义。
- 新增 `src/transbridge/paratranz/terms_service.py`，把现有 raw API 映射到 typed port，统一认证/权限/限流/超时/取消错误；ADR-027 adapter 负责字段保真，HTTP adapter 不决定本地权威或冲突。
- 在现有项目隔离 terminology SQLite 中新增 sync profile/baseline/item link/run/outcome/inbound change set 表，由 `src/transbridge/persistence/terminology/sync_state.py` 提供窄仓储；不把同步元数据塞入 `TermDecision.notes` 或 ADR-027 unknown metadata。
- 新增 `src/transbridge/application/translation/terminology_run_snapshot.py` 固定 AI 运行使用的 snapshot。`AiRunSpec` 只增加不可变引用/摘要字段，worker 消费已固定 binding，不再运行中查询 current effective version。
- 新增 `src/transbridge/ui/tools/terminology/sync_*` 视图、presenter 和 task adapter，并把入口作为术语工作台版本/概览区域的上下文动作；不向 537 行 `window.py` 增加 planner 或网络状态。
- Agent/MCP 通过新增的窄工具模块投影同一 use case；不继续扩张 847 行 `tool_paratranz.py`，也不让 transport 直接调用 `ParatranzTermsAPI`。

## Story 00：锁定 ParaTranz 术语合同与产品校准

- **详细设计**：[story-00-terms-contract-calibration.md](stories/story-00-terms-contract-calibration.md)

### 验收标准

- 以受控 HTTP fixture 或经脱敏保存的真实响应样本确认 list/create/update/delete 的分页结构、所有已知字段、remote ID、只读字段、错误响应、权限要求和删除返回语义。
- 明确接口是否提供远端 revision/ETag、条件写或幂等键；没有时记录 `observed_digest + observed_at + target identity` 的保守 fresh-check 方案。
- 本 Plan 的插件作用域、单 Variant 映射和受管删除三项建议被确认或替换为明确决策；任何替代方案仍满足无损/显式/可逆要求。
- 固定术语同步 fixture：纯回声、双方各自修改、远端新增、双方删除、remote ID 重用/缺失、独立远端项、插件特例、两个 Variant 冲突、分页中途变化和超时后未知结果。

### 文件落点与实施步骤

- 新增 `tests/contracts/paratranz/fixtures/terms/` 的脱敏响应和 `tests/contracts/paratranz/test_terms_api_contract.py`。
- 在本 Plan“明确假设”中记录最终校准；若需要改变 Project schema 或 ADR-023 的公共绑定契约，先用 `bm-arch` 新建/更新 ADR，再开始 Story 02。
- 不在本 Story 修改生产行为或发起默认 live API 测试；可选 live smoke 只在显式凭据和测试项目环境运行并使用 `integration` marker。

### 测试策略

- 分页边界、空页、重复页、乱序、未知字段、缺失 ID、401/403/404/409/429/5xx、timeout/cancel、响应脱敏。
- fixture schema 与 typed DTO 预期先形成失败合同，后续 Story 01 使其通过。

### 依赖与边界

- 无代码依赖；必须在 S01～S03 冻结公开数据合同前完成。

## Story 01：Typed ParaTranz 术语端口与规范映射

- **详细设计**：[story-01-typed-paratranz-terms-port.md](stories/story-01-typed-paratranz-terms-port.md)

### 验收标准

- `ParaTranzTerminologyPort` 分页读取完整术语库，并提供 create/update/delete；所有结果保留 remote ID、可用修订、只读字段、未知字段和安全诊断。
- ADR-027 `TermEntry` 与 remote DTO 的转换只提交 ParaTranz 可写字段，不回传项目、创建/更新时间等只读字段；未知字段在本地 remote snapshot 中保留但不盲写。
- 请求遵守现有 typed client 的认证、限流、Retry-After、超时、取消和 secret redaction；非幂等写不自动盲重试。
- 列表分页中发现目标或 revision 漂移时返回不稳定快照诊断，不生成可执行计划。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/ports/paratranz_terms.py` 和 `src/transbridge/paratranz/terms_service.py`。
- 扩展 `src/transbridge/paratranz/api/paratranz_terms_api.py` 只做必要的取消/typed client 委托；将分页遍历、字段校验和错误分类放入新 service。
- 复用 `src/transbridge/ai_translator/term_formats.py` 的 ParaTranz 适配器；如该文件接近责任上限，将 ParaTranz 映射完整抽到 `term_format_adapters/paratranz.py` 并保留兼容导入。

### 测试策略

- `tests/paratranz/test_terms_service.py` 覆盖分页、字段 round-trip、只读字段剔除、未知字段、错误/取消和脱敏。
- `tests/contracts/paratranz/test_terms_api_contract.py` 对受控服务运行；live smoke 默认跳过并标记 `integration`。

### 依赖与边界

- 依赖 S00；不创建同步计划、不写本地版本或 baseline。

## Story 02：同步 line、Variant 映射与持久基线

- **详细设计**：[story-02-sync-line-baseline-persistence.md](stories/story-02-sync-line-baseline-persistence.md)

### 验收标准

- `TerminologySyncLine` 稳定绑定 local Project/Variant、endpoint、account user、remote project ID 和 profile revision；目标或 Variant 不同即为不同 line，不共享 baseline。
- 每个受管条目保存 local term ID/version/digest、remote ID/revision 或 observed digest、最后共同 canonical digest、作用域、ownership、最后结果和 tombstone 状态。
- baseline、逐项结果和入站 change set 在同一个项目隔离 SQLite 中事务保存；SQLite 损坏或 schema 不兼容时同步 fail closed，不退化为空 baseline 执行全量覆盖。
- 相同目标已有其他 Variant 的活动映射时，任何远端写入前返回 `variant_mapping_conflict`；显式替换映射保留旧审计和 remote links。
- schema migration 有备份、校验和故障回退；旧数据库没有 sync 表时解释为“尚未同步”，不改变 effective version。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/terminology_sync/models.py`、`ports.py`、`identity.py`。
- 新增 `src/transbridge/persistence/terminology/sync_state.py`、`sync_codec.py`，最小扩展 `schema.py` 和 repository factory；不继续扩大 724 行 terminology domain model。
- baseline repository 提供按 line、remote ID、local term ID 查询和单次 run 原子提交；所有目标字段参与 canonical identity/hash。

### 测试策略

- 新库、旧 schema 升级、重复迁移、损坏/磁盘故障/事务回滚、Project/Variant/endpoint/account/remote project 隔离。
- remote ID 重用、同 local term 多历史 remote link、tombstone、未知 outcome、替换 Variant 映射和并发 revision conflict。

### 依赖与边界

- 依赖 S00；模型可与 S01 并行实现，持久化集成需要两者合同冻结。

## Story 03：三方差异、回声阻断与可失效同步计划

- **详细设计**：[story-03-three-way-sync-planner.md](stories/story-03-three-way-sync-planner.md)

### 验收标准

- planner 以“当前已发布本地版本 + 当前远端快照 + 最后成功共同基线”三方比较，稳定区分 local-only、remote-only、unchanged echo、local changed、remote changed、both changed、deleted 和 unknown。
- 备份模式只产生远端 create/update、受管 delete、conflict、lossy skip；远端独立项不会下载、覆盖或删除。
- 双向模式同时产生上传、入站候选、冲突、跳过和删除影响；冲突不自动选择本地或远端，远端删除只形成入站 suppression/delete proposal。
- 插件作用域或其他有损项始终可见且不可执行；同目标多 Variant 歧义在 plan ready 前阻断。
- plan hash 包含 local version ID/digest、remote snapshot digest、baseline revision、target/binding revision、sync profile 和全部 item；任一输入变化使确认失效。
- 相同三方输入重复规划产生相同排序、counts、item identity 和 plan hash；已同步回声不产生新候选或冲突。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/terminology_sync/planner.py`、`plan_models.py`、`mapping.py`、`use_case.py`。
- 复用通用 confirmation policy/`ConfirmationToken`，但定义术语专用 action/reason；不复用 `application.sync.LocalEntrySnapshot`。
- 本地输入只通过 `EffectiveTerminologySnapshotPort` 读取显式 version；remote 只通过 S01 port；baseline 只通过 S02 port。

### 测试策略

- S00 全量 fixture 的 golden plan、输入乱序、重复 normalized term、duplicate remote ID、无 baseline 首次备份、独立远端项、双方删除和 unknown outcome。
- spy 证明 dry-run 不写远端、draft、effective pointer 或 baseline；修改任一输入后 authorize 返回 stale。

### 依赖与边界

- 依赖 S01～S02；不得调用网络写或 draft mutation。

## Story 04：单向备份执行、逐项结果与安全重试

- **详细设计**：[story-04-backup-execution-retry.md](stories/story-04-backup-execution-retry.md)

### 验收标准

- executor 只接受已授权且 fresh 的 backup plan；执行前重新验证 ADR-023 目标、binding revision、账号/成员权限、本地 version digest、远端 snapshot 和 baseline revision。
- create/update/delete 逐项记录 succeeded/failed/skipped/unknown、remote ID/revision、request ID 和安全诊断；部分成功不伪装为完整成功。
- baseline 只在能证明远端副作用的 item 上前进；timeout/断线导致结果未知时先 reconcile，再决定重试，绝不直接重复 create/delete。
- retry token 绑定 line、plan hash、owner、目标和已确认 outcome；重试跳过已成功项，并在远端已变化时返回 stale/replan。
- 取消后不再启动新请求，迟到结果只能进入 reconcile 状态；当前本地已发布版本和 draft 始终不变。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/terminology_sync/executor.py`、`execution_models.py`、`task_adapter.py`。
- 复用 TaskRuntime、confirmation、typed error 和 cancellation 合同；把通用 retry/confirmation helper 的必要复用抽成小型基础模块时保持现有 `application.sync` API 兼容。
- 远端操作采用逐项/有界批次 API；不调用无法给出逐项身份和结果的 bulk import。

### 测试策略

- 受控 HTTP create/update/delete 成功链、401/403/409/429/5xx、分页后变化、中途断线、取消 race、timeout after commit、reconcile 和幂等重试。
- 断言重复备份同版本第二次无写请求；独立远端项与 lossy 项无写请求；partial 状态重启后仍可判定。

### 依赖与边界

- 依赖 S03；只实现以本地为权威的 backup，不修改本地 draft/effective version。

## Story 05：双向同步入站 change set 与本地复核边界

- **详细设计**：[story-05-bidirectional-inbound-review.md](stories/story-05-bidirectional-inbound-review.md)

### 验收标准

- 双向 executor 可与上传同一运行完成，但所有 remote-only/remote-changed/remote-deleted 内容先保存为 immutable `InboundTerminologyChangeSet`，保留 remote identity、目标、远端修订/摘要、baseline 和来源计划。
- 入站 change set 不改变 effective pointer；“导入待处理内容”通过独立命令合并到 active draft 或创建新 draft，并使用 expected draft/version revision 防止覆盖并发人工修改。
- 远端新增生成 review-required candidate；远端修改与本地人工决定不同则生成可见冲突；远端删除生成 suppression/delete proposal，绝不删除历史 version/evidence。
- 用户接受/拒绝/改写入站项均留下 actor、时间、before/after digest 和 remote provenance；只有后续 FR5.16 publish 才影响新 AI 任务。
- 重复拉取同一远端状态复用已有 change set/item identity，不制造重复候选、冲突或人工 action。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/terminology_sync/inbound.py`、`draft_import.py`；扩展 S02 sync state repository 保存 change set 和 review disposition。
- 通过现有 `DraftService`/draft transaction port 提交，不直接写 terminology tables；remote metadata 保存在 sync provenance 表并由稳定 item ref 关联 draft action。
- 对已有 active draft 先生成 import preview，再由用户确认；base version 不同则要求 rebase/review，不自动覆盖。

### 测试策略

- 新增/修改/删除、已有 draft/无 draft、draft revision race、base version 改变、人工决定冲突、拒绝/改写、重复导入和发布前后隔离。
- 集成断言入站完成后旧 effective snapshot digest 不变，发布新版本后才出现新 digest。

### 依赖与边界

- 依赖 S03～S04 和 FR5.16 draft/publish；不自动发布，不替用户解决冲突。

## Story 06：AI 运行术语快照固定与 legacy 回声过滤

- **详细设计**：[story-06-ai-run-snapshot-echo-filter.md](stories/story-06-ai-run-snapshot-echo-filter.md)

### 验收标准

- 用户确认 AI 预检并创建运行档案时，一次性读取 `EffectiveTerminologySnapshot`，将 Project/Variant/version ID/content digest/status 写入不可变 run spec，并把同一已验证 snapshot binding 传给全部批次/阶段。
- 翻译、润色、混合和自定义任务运行中发布/恢复其他术语版本、同步 ParaTranz 或修改长期配置，均不改变该任务的术语 snapshot identity 和匹配结果；后续新任务读取新版本。
- 运行中不能读取已固定 version 或摘要不匹配时安全失败；已持有的不可变内存 snapshot 可继续使用，但不得切到 current/legacy。
- 存在已发布项目版本时，S02 baseline 证明为该版本回声的 ParaTranz legacy term 在来源合并前过滤；独立远端项仅在未被项目版本覆盖/抑制且用户配置允许时 fallback。
- 没有已发布版本时保持 FR5.2 的 JSON/CSV/Excel/ParaTranz/dynamic 优先级和现有 AI 行为；本 Story 不触发网络同步。

### 文件落点与实施步骤

- 新增 `src/transbridge/application/translation/terminology_run_snapshot.py` 和 `src/transbridge/ai_translator/legacy_term_policy.py`。
- 扩展 `src/transbridge/ui/tools/ai_translator/run_spec.py` 的不可变字段和 digest；`run_controller.py`、batch/polish/mixed worker 只接收固定 binding，删除运行中重新解析 current binding 的 fallback。
- Smart Assistant 的 translator/postprocess request 在提交 TaskRuntime 前调用同一 snapshot factory；将接线放入新 helper，不继续扩大 `tool_translator.py`。
- baseline filter 由 application port 注入 `TermDatabaseManager`，只做窄委托；不向 896 行主类加入同步状态查询。

### 测试策略

- 四种 AI mode 的多批次并发测试：中途 publish、restore、sync、Variant switch、版本读取失败和 digest mismatch。
- project term + 同 remote ID 回声、同词不同 remote ID、独立远端 fallback、suppression 防复活、无项目版本 legacy parity、向量 snapshot identity 一致性。
- spy 断言打开 Project、切换 Variant 和启动 AI run 不调用术语写 API。

### 依赖与边界

- snapshot pinning 依赖 FR5.16 effective port；精确回声过滤依赖 S02。可与 S04～S05 并行开发。

## Story 07：术语工作台、Agent 与 MCP 等价入口

- **详细设计**：[story-07-multi-entrypoint-experience.md](stories/story-07-multi-entrypoint-experience.md)

### 验收标准

- 术语工作台提供“备份已发布版本”和“双向同步”两个显式动作，显示当前 Project/Variant、本地 version、目标/账号/endpoint、映射状态和上次结果。
- preflight/plan 可分页展示上传、下载、冲突、跳过、有损映射和删除，删除/覆盖及 Variant 映射替换必须结果导向确认；计划 stale 后按钮失效并要求重新预检。
- 运行通过现有 TaskRuntime 展示连续进度、取消、partial/unknown、retry/reconcile；UI 主线程不执行网络、全量 diff 或 SQLite 大查询。
- 入站 change set 可从同步结果进入现有草稿复核；未发布前 UI 明确说明“不会影响当前翻译术语版本”。
- Agent/MCP 暴露同一 plan、authorize、execute、status/retry 和 inbound-import capability；写操作遵守现有 HITL/权限，返回与 GUI 同一 plan hash 和 counts。

### 文件落点与实施步骤

- 新增 `src/transbridge/ui/tools/terminology/sync_view.py`、`sync_presenter.py`、`sync_task_adapter.py`，通过现有 workbench service/command facade 挂载。
- 新增 `src/transbridge/smart_assistant/tools/tool_terminology_sync.py` 并在 registry 注册；MCP 继续自动投影工具 schema，不增加 transport 业务分支。
- 在 `bootstrap/terminology.py`/composition 注册 terminology sync ports/use cases；无 ParaTranz 配置或未绑定时 capability 明确 unavailable，不构造隐式网络请求。

### 测试策略

- presenter/Qt：未绑定、未验证、无 published version、两种模式、分页、lossy、冲突、删除确认、stale、取消、partial、retry 和 inbound review navigation。
- Agent/MCP 合同：相同输入 plan hash/counts 一致，破坏性操作无 confirmation 被拒绝，跨 owner/token 重放失败，secret 不出现在结果。

### 依赖与边界

- 依赖 S03～S06；View/工具只投影 application DTO，不自行计算 diff 或调用 raw API。

## Story 08：端到端故障演练、兼容与发布门禁

- **详细设计**：[story-08-end-to-end-release-gates.md](stories/story-08-end-to-end-release-gates.md)

### 验收标准

- FR5.17 的十个验收场景均有自动化集成测试或明确的受控服务证据，并可追溯到 plan item/outcome/baseline/run spec digest。
- 在常规大术语库上，远端分页读取、plan 生成/分页展示、SQLite 写入和 UI 投影使用有界内存；取消后 500ms 内出现反馈且不再调度新网络请求。
- 认证失效、权限不足、限流、网络中断、取消、部分成功、timeout-after-commit、数据库故障、目标/binding/revision 变化均不会改变 current effective version，也不会重复已确认副作用。
- 默认关闭、未绑定、无网络、无已发布版本和旧 Project/terminology SQLite 的现有本地术语与 AI 流程通过兼容回归，且无不必要网络请求。
- FR5.16 基座正式发布证据通过后，才可把 FR5.17 标记为发行完成；若基座 S12 仍失败，FR5.17 可保持功能实现但发行门禁必须为 OFF。

### 文件落点与实施步骤

- 新增 `tests/integration/terminology_sync/`、`tests/performance/terminology_sync/` 和受控 HTTP server fixtures。
- 新增 `docs/test-reports/project-terminology-paratranz-sync-qa-<date>.md` 记录命令、fixture/API contract 版本、故障矩阵、性能/资源结果、兼容和未通过门禁。
- live smoke 仅对专用测试项目运行，完成后用受管 item identity 清理本次创建数据；不删除预存在的远端术语。

### 测试策略

- 聚焦：`uv run pytest tests/application/terminology_sync tests/persistence/terminology/test_sync_state.py tests/paratranz/test_terms_service.py -q`。
- 合同/集成：`uv run pytest tests/contracts/paratranz/test_terms_api_contract.py tests/integration/terminology_sync -q`。
- AI/UI：`uv run pytest tests/ai_translator tests/application/translation tests/ui/tools/terminology tests/smart_assistant/tools -q` 的相关筛选集。
- 性能：`uv run pytest tests/performance/terminology_sync -m slow -q`，并记录固定 fixture 规模和峰值 RSS。
- 静态：`uv run ruff check src tests scripts`、`uv run ruff format --check src tests scripts`、`git diff --check`。

### 依赖与边界

- 依赖全部前序 Story；live 凭据测试不作为普通离线 CI 的必要条件，但受控 HTTP 成功链和故障链必须进入 CI。

## 依赖顺序与可交付阶段

```text
S00 ─┬→ S01 ─┐
     └→ S02 ─┴→ S03 → S04 → S05 ─┐
              └────────→ S06 ─────┼→ S07 → S08
```

- **阶段 A（合同与基线）**：S00～S02，冻结远端术语合同、产品校准、同步 line 和可迁移基线；不提供写入口。
- **阶段 B（安全计划与备份）**：S03～S04，交付确定 dry-run、显式确认、单向备份、partial/reconcile/retry。
- **阶段 C（双向闭环与运行隔离）**：S05～S06，交付入站待处理内容、草稿复核、AI 快照固定和回声过滤。
- **阶段 D（多入口产品化）**：S07，GUI/Agent/MCP 等价消费同一 use case。
- **阶段 E（发行验收）**：S08，完成故障、兼容、性能和 FR5.16 基座门禁证据。

## 需求追溯

- FR5.17.1：S03、S04、S07～S08。
- FR5.17.2：S02、S03、S06、S08。
- FR5.17.3：S06、S08。
- FR5.17.4：S02、S03、S05、S07～S08。
- FR5.17.5：S00、S02～S03、S07～S08。
- FR5.17.6：S01～S05、S08。
- GUI/Agent/MCP 等价边界：S04、S05、S07～S08。

## 迁移、兼容与回退

- terminology SQLite 只做向前 schema migration，迁移前备份并校验；失败时旧库保持可读、术语同步 capability fail closed，FR5.16 effective terminology 继续可用。
- 初次同步没有 baseline 时只允许 create、safe match 和显式冲突；不得把“同原文”自动当成已受管 remote identity，也不得删除远端独立项。
- 回退代码可以禁用/隐藏网络同步 use case，但不得清空 baseline、remote link、inbound change set 或 outcome journal；新版数据保留只读审计。
- 未启用/未绑定/无已发布版本时不迁移 legacy ParaTranz/JSON/CSV/Excel/dynamic term source，也不改变优先级。
- 旧进行中 AI 任务没有 terminology run ref 时按旧行为完成；新版本创建的 run spec 必须具备显式 snapshot status，不能静默读取 current。
- Variant 映射策略未来若扩展为“每 Variant 独立远端项目”，新增 line/profile 和 target mapping，不重写旧 baseline identity。

## 主要风险与控制

- **ParaTranz 缺少稳定 revision/条件写**：执行前完整重读、canonical observed digest、逐项 reconcile；未知 outcome 不盲重试。
- **首次匹配误认远端独立术语**：无 baseline 时同词只生成 safe-match proposal/conflict，只有用户确认后建立 managed link。
- **插件语义泄漏**：planner 在映射前检查 scope/capability；有损项不可执行，禁止“去掉 scope 后上传”。
- **双权威与旧值复活**：published version 是 AI 唯一项目权威；baseline filter 去除同步回声，suppression 继续阻止 legacy fallback。
- **运行中术语漂移**：run preflight 固定 snapshot object + version/digest；worker 不再解析 current，恢复时按精确版本验摘要。
- **部分远端成功与本地崩溃**：逐项 durable outcome、unknown/reconcile、baseline CAS 和 retry token；本地 effective version不参与远端事务。
- **跨 Variant 覆盖**：line identity 和活动映射 gate 在 planner、authorize、execute 三处校验；目标变化使 plan stale。
- **大文件继续膨胀**：新领域、port、UI 和 Agent adapter 独立成包；对超阈值文件只做窄委托，新增职责前必须抽取。

## 未决问题与明确假设

- **已确认：三项产品校准**。采用“插件特例跳过、单目标单 Variant、只删除可证明受管项”。若未来改变持久 Project/remote binding 公共契约，先执行 `bm-arch` 并更新本 Plan。
- **待确认：ParaTranz revision 能力**。仓库只证明 raw terms API 存在，不证明服务端提供 revision/ETag/条件写；S00 以实际合同决定字段，不在实现中伪造。
- **假设：入站 remote term 不直接成为 FR5.16 evidence**。它先是 sync change set；用户接受后才投影为 draft candidate/decision，并保留独立 provenance。
- **假设：受管删除使用逐项 API**。bulk import 无法提供足够逐项身份、unknown outcome 和重试证据，因此首版不用于正式同步执行。
- **假设：ADR-034 的已发布 effective boundary 保持不变**。若 ADR-034 未接受且该边界发生变化，S02、S03、S05、S06 必须先回到架构评审。
- **假设：FR5.16 功能基座可供开发，发行证据仍是独立门禁**。S12 未通过不阻止在其稳定接口上实现 FR5.17，但阻止最终发行完成声明。

## 完成定义

- 两种模式、三方差异、回声/删除/冲突/Variant/作用域、部分失败和安全重试全部具有自动化合同与集成证据。
- 任一入站操作完成后 current effective version 不变；只有用户复核并调用现有 publish 后新任务才消费新版本。
- 同一 AI run 的所有阶段/批次记录并使用同一 Project/Variant/version/digest；中途 publish/restore/sync 不改变结果。
- GUI、Agent、MCP 对相同输入返回相同 plan hash、counts、stale/confirmation/partial 语义，且没有 transport 专属业务分支。
- 默认关闭、未绑定、无网、无发布版本和 legacy term source 回归通过；打开/切换/启动 AI 无隐式术语写请求。
- 聚焦、合同、集成、相关 UI/AI 回归、性能证据、Ruff、format 和 `git diff --check` 通过；所有未通过项阻止 Plan 状态改为完成。

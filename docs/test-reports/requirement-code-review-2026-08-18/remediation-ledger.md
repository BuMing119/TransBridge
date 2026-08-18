# 综合整改问题台账与追溯矩阵（Phase 0）

- 日期：2026-08-18
- 状态：Phase 7 完成；37/37 Story 实现并通过综合 QA（2026-08-18）
- 执行模式：`bm-pilot` 复杂路线；当前处于 Phase 4 编码
- 配置：`.codex/bm_config/paths.json`、`.codex/bm_config/pilot.json`（`dev_skill=bm-dev`）
- 正式输入：`README.md`、`fr-01.md`～`fr-16.md`、三份不带 `.partial` 的横向报告、`integrated-roadmap.md`、`paratranz-json-compatibility-adjustment.md`
- 排除输入：三个 `.partial.md` 仅保留审查过程，不读取为正式结论、不覆盖、不删除
- 写入边界：Phase 0～3 已完成；Phase 4 37/37 个 Story 全部完成增量验证；已生成最终 QA 汇总，尚未回填既有正式审查报告

## 1. 复核方法与状态口径

本台账不把报告结论直接当作事实。每项至少经过以下一种当前事实复核，并在状态列注明证据强度：

1. 当前源码/配置/文档静态复核；
2. 独立运行时探针；
3. 现有定向测试；
4. 需要在后续 Story 先补 characterization/contract test 才能最终判定。

处理状态：

- `确认有效`：当前代码或运行时可直接复现。
- `确认有效（待合同测试）`：静态调用链成立，但需先固化行为测试再修改。
- `待产品确认`：历史需求是否继续公开支持会改变整改范围。
- `历史状态失真`：实现/验收证据与“已完成”状态不一致；只做增量纠偏。
- `描述偏差`：报告对事实的表述不完整或过时，但底层风险仍存在。
- `误报`：当前事实不支持报告结论；不得因此修改正确代码。

分类：`C1` 纯 Bug；`C2` 需求缺口/行为合同变化；`C3` 跨模块架构或状态所有权；`C4` 历史完成状态失真；`C5` 报告误报或描述偏差。

## 2. 当前基线证据

- Git：`main...origin/main`；受控业务文件无未提交修改。未跟踪内容为 `.agents/`、`.codex/`、本次审查目录和 `full-project-static-audit-2026-08-18.md`。
- 包入口：`pyproject.toml` 指向 `transbridge:main`，但 `src/transbridge/__init__.py` 无 `main`；版本分别为 `0.1.1.1` 与 `0.1.1.8`。
- 导入：当前 `src/transbridge` 中有 391 处 `src.transbridge` 文本命中，开发态与安装态包身份没有收口。
- 环境：项目 `.venv` 的 uv-managed Python 3.12.12 在允许访问 `%APPDATA%/uv/python` 后可正常执行；早期失败是受限执行上下文造成的证据误判，不重建有效环境。
- 依赖：`rank-bm25` 已在 `pyproject.toml` 声明，但未进入 `uv.lock`，当前探针环境也不可导入；报告“pyproject/lock 都缺失”属于部分过期。`py7zr/rarfile` 未声明或锁定，系统环境偶然可导入不能证明发布物可用。
- Agent 注册：工具注册前 `translator/parser/editor/paratranz/writer=0`；注册 49 个工具后仍全部为 0，确认 wildcard 过早展开。
- Variant：对空 Variant 调用 `apply_to()` 后旧译文仍为 `from-A`；清空译文后 `collect_from()` 仍保留旧缓存值 `old`。
- Prompt 帮助：`editor` 完整帮助 4180 字符，真实 `to_observation()` 结果 150 字符，尾部 schema 丢失。
- Session：`python -O` 下从 `idle` 非法调用 `handle_execution_complete()` 后进入 `thinking`，确认生产状态校验不能依赖 `assert`。
- Task listener：注册 legacy completed callback 后用原 callback 注销，`finished` listener 数量从 1 仍为 1。
- ParaTranz Agent：`ParatranzProjectAPI` 不存在 `get_entries/upsert_entry/get_upload_history`，当前工具调用链确定失败。
- 测试反证：FOMOD/FileOps/Tool Prompt/Session/MCP/TaskManager 定向套件共 122 项全部通过，但上述运行时探针仍失败，确认现有测试未覆盖真实组合根与成功链合同。

## 3. 去重根因台账

### 3.1 architecture-contract-stabilization

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-001 | CLI、包名、导入与版本单一来源未成立 | HC P0-4；HQ Q-01；FR9/10 | 原证据有效；platform S01 已统一安装态包身份、入口和版本元数据，clean/non-editable uv smoke 通过 | FR7/9/10/15/16, NFR6 | C3 | P0/Blocker | 已修复（综合 QA 通过） |
| R-002 | 依赖、锁文件、能力探测与开发环境基线漂移 | HQ Q-02；FR5 P0-6；FR15 P0-6；FR16 P0-4 | `.venv` 失效为误判；platform S01 已锁定 rank-bm25/py7zr/rarfile 并建立 capability/bundle 基线 | FR5/15/16, NFR3/6 | C3+C5 | P0/Blocker | 已修复（最终发行物待 release S05） |
| R-003 | 缺唯一 Composition Root；全局 registry/controller 与伪 DI 隐藏依赖 | HA 4.2/4.5；HC P2-3；FR7/9/10 | `app.py` 初始化顺序固定；工具模块惰性创建新 `AppContext/TaskManager` | FR7/9/10/12/14/16 | C3 | P0/Critical | 确认有效 |
| R-004 | MCP 启动、context、认证配置、Windows stdio 与 admin 语义未形成可用拓扑 | HC P0-3；HQ Q-09；FR7 P0-5；FR9 P0-5；FR16 P0-3 | `ToolRegistry` 未导入、ctx 永久为空、`select(stdin)`、token 未传入 | FR7/9/10/16 | C3 | P0/Blocker | 确认有效；拓扑待确认 |
| R-005 | Tool schema/validation/path/HITL/result 没有 canonical contract | FR9 P1-3/4/5；FR11 P1-3/5；FR16 P0-2/P1-4 | schema 使用 `str/list`；绝对路径统一拒绝；部分写操作先变更后失败 | FR7/9/11/16 | C3 | P0/Critical | 确认有效 |
| R-006 | 历史 Requirement/ADR/Plan/Story/INDEX “已完成”状态不等于验收 | HC P3-1；HQ Q-12；FR7～16 多处 | INDEX 宣称全部完成，多个 Story 缺失/验收未勾/调用链失败 | FR1～16 | C4 | P0/Critical | 历史状态失真 |

### 3.2 application-layer-foundation

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-007 | GUI、Agent、MCP、FOMOD 各自编排具体 parser/writer/client，缺 Application Use Case | HA 4.1/4.4/4.8～4.11；IR 2.3 | MainWindow/工具/FOMOD 直接构造具体实现 | FR1/3/4/5/6/7/9/15/16 | C3 | P0/Critical | 确认有效 |
| R-008 | AppContext、Step2、VariantStore 的筛选/标签/选择状态重复所有权 | FR2 P0-5；FR7 P0-3/P1-6；FR9 P0-3 | 同名私有状态并存，无双向订阅；持久化只恢复部分状态 | FR2/7/8/9 | C3 | P0/Critical | 确认有效 |
| R-009 | 预置 Agent 只是 metadata；运行时/能力选择器缺失且 wildcard 初始化为空 | FR7 P0-1/2；FR9 P0-4/P1-6；FR11 P0-3 | `agents/` 仅 3 文件；注册 49 工具后 5 个 Agent 仍为 0 工具 | FR7/9/11 | C2+C3 | P0/Critical | 确认有效；多 Agent 产品语义待确认 |
| R-010 | 文件上传只有 UI 展示，没有可检索知识注入 | FR7 P0-4 | Context 仅含文件名/格式/字符数；无检索工具或索引入口 | FR7.13 | C2 | P1/Major | 确认有效（待合同测试） |
| R-011 | 工具帮助在真实 Observation 被截断；LLM eval 13% 被历史 QA 判通过 | FR11 P0-1/2/P1-2/4 | 4180→150 字符；`result.json` 为 2/15、13% | FR11 | C1+C4 | P0/Critical | 确认有效/历史状态失真 |
| R-012 | 后端→UI/PyQt 反向依赖及 ChatWidget/MainWindow God Object | HA 4.3/4.4；FR7 P2-1/2；FR10 P1 | 业务编排、线程、持久化、对话和视图仍集中在 UI | FR7/8/10/12/13/14 | C3 | P1/Major | 确认有效（渐进迁移） |

### 3.3 translation-io-kernel-v2

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-013 | 缺显式 ParaTranz JSON 双 ID Adapter 与 GUI/Agent/MCP 统一入口 | FR1/2/3/4；PT 调整；IR 1.1/5.2 | S03 已提供离线 ParaTranz JSON 双 ID Adapter、type-stable `id`、V2 facade 与 parse→write→parse 合同；网络同步及跨入口完整业务链仍待后续 Story | FR1/2/3/4/9/16 | C2+C3 | P0/Blocker | 部分修复（待 S04/S06 与 ParaTranz sync） |
| R-014 | 公开 Agent parser dispatch 与真实构造/方法/模块错配 | HC P0-1；FR1 P1；FR9 P0-1 | S04 已通过统一 TranslationIoUseCase 适配 ESP/EET/XT 实际构造/parse 合同，Agent dispatch 不再假定 `cls().parse()`，并保存 SourceSnapshot/FormatId；其他格式与发布仍待后续 Story | FR1/9/16 | C1 | P0/Blocker | 部分修复（ESP/EET/XT；待其余格式） |
| R-015 | Parser→Slot→Writer 丢 source/write context；EET/XT writer API 错配 | HC P0-1；FR4 P0-2；FR9 P0-2 | S04 的 Slot 现保存 SourceSnapshot/FormatId，ESP/EET/XT adapter 保留 source template、locator、encoding/BOM 并在写后重解析验证；staging/backup/atomic publish 仍待 S06 | FR1/4/9 | C3 | P0/Blocker | 部分修复（待 S06 发布门禁） |
| R-016 | Localized Strings 写回从空 mapping 开始，可能删除未翻译 string_id | FR4 P0-1；IR 5.2 | S05 已为 STRINGS/DLSTRINGS/ILSTRINGS 建立完整 SourceSnapshot/rebuild、真实 fixture 和 source fingerprint/locator 门禁；BSA 内嵌 strings 仍是 experimental，原子 publish 待 S06 | FR4 | C1 | P0/Blocker | 部分修复（loose strings；待 S06/BSA） |
| R-017 | 七级 Stage、hidden/locked/空译文输出策略在 Writer/TM/AI/PostProcess 间分裂 | FR2 P0-8～10；FR4 P0-4；HC P1-1 | S05 已确立离散 StagePolicy，并接入 AI、PostProcess、TM、FOMOD 和 EET/Strings writer；Stage0/hidden 原文、locked-empty 发布阻断固定为合同 | FR2/4/5/6/15/16 | C2+C3 | P0/Critical | 部分修复（待 S06 publish 与综合 QA） |
| R-018 | 写回缺 staging、备份、验证、原子发布与 fidelity 合同 | FR4 P0-5/P1；HQ Q-03；IR 5.2 | S06 已实现同卷 staging、fsync/atomic replace、重解析/fidelity、目标指纹、verified backup、fault/cancel/UNC 门禁；ESP corpus 仍 partial 而安全拒绝发布 | FR4/15/16 | C3 | P0/Critical | 部分修复（待 clean ESP corpus 与综合 QA） |
| R-019 | ParseOutcome 未区分合法空/partial/failed/cancelled；上下文与 SST/DSD 支持矩阵含糊 | FR1 条件缺陷/待确认；HQ Q-08 | 异常可返回空；DSD 入口含糊；SST 自动套件不可重放 | FR1/9/16 | C2+C3 | P1/Major | 待产品确认 |
| R-020 | EntryKey、历史 id、external id 与直接字段 mutation 没有唯一合同 | FR2 全部；HC P1-1；PT 调整 | S02 已建立 EntryKey/ExternalEntryRef/revision/provenance 与 MutationPort；S03 已将 ParaTranz `key`/可选 `id` 映射到该合同。其余格式、Variant/TM 与旧 facade 迁移仍待后续 Story | FR1/2/3/5/6/8/9/15/16 | C3 | P0/Critical | 部分修复（持续迁移） |

### 3.4 project-session-persistence-v2

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-021 | Variant 使用 overlay 而非 replace；清空译文会复活并导致多版本串版 | HC P0-2；FR2 P0-1/3；FR8 P0-1；FR4 P0-3 | S02 已建立 source baseline 上的完整 replace materialization、tombstone 与单次 VariantChangeSet swap；A→空→B 与重启清空不复活均有回归 | FR2/4/8 | C1+C3 | P0/Blocker | 已修复（综合 QA 通过） |
| R-022 | Stage、labels、provenance、revision 与 dirty 没有随 Variant 完整持久化 | FR2 P0-2/4/5；FR8 P0-4/P2-4 | S02 VariantEntryState/Snapshot 保存 stage、labels、provenance、revision 与显式空值；旧 list facade 仍是有损投影，dirty/projection 由 S05 承接 | FR2/7/8 | C2+C3 | P0/Critical | 部分修复（待 S05） |
| R-023 | 关闭/切项目/导出/快照前 collect、快照 current 指针和事务生命周期不统一 | FR8 P0-2/3；HQ Q-10 | 保存协调在 MainWindow；快照加载对象可继续指向快照路径 | FR8 | C3 | P0/Critical | 确认有效（待故障注入） |
| R-024 | 多源 key 无 namespace，缺 source fingerprint 和非 ESP 恢复屏障 | FR8 P0-5/P1-2；HC P0-2 | S02 保存 source namespace/fingerprint，冲突产生 migration plan 而非同 local key 覆盖；非 ESP 生命周期/入口仍由 S03 承接 | FR1/2/8 | C2+C3 | P1/Major | 部分修复（待 S03） |
| R-025 | Session 启动只恢复 UI；切换先提交 active id，后台任务可污染新会话 | FR13 P0-1/2/3；HC P1-3 | Panel 启动调用 `load_history`；切换先写 `_active_session_id` | FR12/13/14 | C1+C3 | P0/Blocker | 确认有效 |
| R-026 | Session ID/JSON schema/保存失败/最近活跃/缓存可变对象合同不完整 | FR13 P1-1～6/P2-1/5 | S01 已建立受限 opaque ID、编码路径、内部/请求 ID 一致性校验、strict JSON/schema、验证备份/隔离与 future/invalid record fail-closed；完整 Session aggregate、最近活跃和 owner 隔离仍待 S04 | FR13 | C2+C3 | P0/Critical | 部分修复（待 S04） |

### 3.5 unified-task-runtime

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-027 | pause/cancel/stop/completed 终态分裂；可假暂停、取消后 completed | FR7 P1-5；FR14 P0-1/2；FR15 P0-2 | TaskManager 只改 Event/status；外层 runner 可继续写 completed | FR5/6/7/12/14/15 | C3 | P0/Blocker | 确认有效 |
| R-028 | 同步结果与 Deferred TaskRef 无类型化合同，AWAITING_TASK 生产路径不可达 | FR7 P0-6；FR12 P0-1/P1-2；FR14 P0-3；HC P1-2 | `handle_task_started` 无生产调用；`is_long_running` 同时标记同步工具 | FR7/9/12/14 | C3 | P0/Blocker | 确认有效 |
| R-029 | Task 缺 project/session/variant/slot owner、correlation 与资源 lease | FR12 P0-2/P1-4；FR13 P0-2；FR14 P0-4 | TaskHandle 无 owner；事件全局广播；listener 注销运行时失败 | FR5/6/7/12/13/14/15 | C3 | P0/Blocker | 确认有效 |
| R-030 | 状态转换、shutdown、cleanup 和 TaskMonitor 投影没有单一所有者 | FR14 P1-5～8/P2-2/3；FR10 P1 | 终态可逆、句柄可变、GUI join、view dispose 可 reset 全局 runtime | FR10/12/14 | C3 | P0/Critical | 确认有效 |
| R-031 | AI/PostProcess/Graph checkpoint 非原子、identity 不稳定、恢复非幂等 | HQ Q-06；FR5 P0-5；FR6 P0-4；HC P1-4 | 直接 `open(...,"w")`；异常当无 checkpoint；graph 用进程随机 hash | FR5/6/7/10/14 | C3 | P0/Critical | 确认有效 |
| R-032 | Graph pause/frontier/分支/checkpoint 恢复语义错误 | FR7 P1-3；FR10 P0-1～3；HC P1-4 | Event 方向反；递归分支+BFS；结果/frontier 未恢复 | FR7/10/12 | C1+C3 | P0/Critical | 确认有效（待特征测试） |

### 3.6 translation-workflow-runtime

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-033 | MixedWorker 构造/输入/Polisher 调用不可执行；Agent mode 被忽略 | FR5 P0-1～3 | 当前报告所列签名仍可在源码定位；缺真实 mixed 成功链测试 | FR5.11/7/9 | C1+C2 | P0/Blocker | 确认有效（待特征测试） |
| R-034 | 三轮 context 常量、unknown fallback 与 quest 串行 barrier 不满足合同 | FR5 P0-4；IR FR5 | 拼接常量/跳过未知/分支并发证据仍在；无全 context 合同测试 | FR5 | C1+C2 | P0/Critical | 确认有效（待合同测试） |
| R-035 | Prompt profile/target language/config 未贯通 GUI/Agent/MCP/FOMOD | FR5 关键偏差；FR15 P0-3 | FOMOD target_lang 主要进入 XML；Agent profile 不切 endpoint | FR5/7/9/15 | C2+C3 | P0/Critical | 确认有效 |
| R-036 | 流式/后台 mutation 直接写正式 Collection；cancel 后可能晚到写入 | FR5 P0-7；HC P1-5 | worker 中直接 `_update_collection`；`safe_mutate` 主要用于通知 | FR2/5/7/14 | C3 | P0/Critical | 确认有效（待并发测试） |
| R-037 | PostProcess 候选链、Stage、精确 scope、batch outcome 与恢复语义断裂 | FR6 P0-1～5；HC P1-6 | Polisher 输入不是 refine candidate；异常可只记日志并 completed | FR6/9/15 | C2+C3 | P0/Blocker | 确认有效（待成功链） |
| R-038 | UI/Excel/历史报告不是同一 canonical snapshot | FR6 P1；IR FR6 | 顶层/嵌套动态字段不同；accepted 由 confidence 推断 | FR6 | C2+C3 | P1/Major | 确认有效（待报告 fixture） |
| R-039 | Retrieval manifest/inactive rows/restart/BM25/tokenizer/embedding disabled 合同不完整 | FR5 FR5.12；HQ Q-07 | `rank-bm25` 已声明但 lock/环境未闭环；其余索引生命周期仍缺证据 | FR5.12, NFR2 | C2+C5 | P1/Major | 描述偏差（风险有效） |

### 3.7 paratranz-sync-service

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-040 | Token/LLM/MCP secret 明文存储与跨通道脱敏缺失 | FR3 P0-1；HQ Q-05 | ParaTranz S01 已把 INI token 迁移为 CredentialRef + secure store、建立环境只读覆盖和 canary redaction；client、direct upload/download 及旧 Agent 工具回归均验证无凭据 fail-closed 与异常脱敏。其他服务凭据/后续 sync 仍分别承接 | FR3/7/9, NFR4 | C1+C3 | P0/Blocker | 部分修复（ParaTranz 通道；待后续服务迁移） |
| R-041 | Agent ParaTranz 客户端构造和 API 方法错配 | FR3 P0-2/3；IR FR3 | S02 已以 ParaTranzPort/service 映射真实 projects/strings/history/export API；工具不再调用不存在方法，受控 HTTP 与 Agent 回归通过 | FR3/9 | C1 | P0/Blocker | 已修复（综合 QA 通过） |
| R-042 | 上传/下载/术语/Artifact 缺 typed response、事务 merge、partial、retry、cancel 与原子发布 | FR3 P0-4/P1；HC P1-7；IR 5.5 | S02 已覆盖 typed response、错误分类、有界 retry/cancel；S03 已提供只读 sync plan、stale 检测和确认令牌。查询后 upsert 竞态、事务 merge、partial/retry token 与 artifact `.part`/manifest 仍由 S04 承接 | FR3/9/15 | C2+C3 | P0/Critical | 部分修复（待 S04） |

### 3.8 fomod-pipeline-v2

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-043 | 必要阶段吞异常、取消后仍 pack、结果无互斥终态和事务发布 | FR15 P0-1/2；HQ Q-03；HC P1-6 | `_ai_translate` 返回 0；`_write_back` pass；随后无条件 pack | FR4/5/6/15 | C1+C3 | P0/Blocker | 确认有效 |
| R-044 | target_lang、TM STALE/Stage、冲突与 provenance 跨三条路径不一致 | FR15 P0-3/4/P1-2/3 | TM 套用不更新 Stage；无 locale scope；AI/XML 配置源不同 | FR2/5/15/16 | C2+C3 | P0/Blocker | 确认有效 |
| R-045 | 根归一化、skip-hash、默认过滤 FOMOD 图片与 source arbitration 错误 | FR15 P0-5/P1；FR16 P1-1/2 | 默认 strip 含图片；normalize 只向上；skip 直接 unchanged | FR15/16 | C1+C2 | P0/Critical | 确认有效 |
| R-046 | Archive 缺统一成员策略、资源预算、progress/cancel、staging/cleanup 与一致结果 | FR16 P0-5/P1-3；HQ Q-04/Q-10；HC P2-2 | ZIP 用字符串 startswith；7z extractall；mkdtemp 无 finally | FR15/16, NFR1/4 | C3 | P0/Blocker | 确认有效 |

### 3.9 quality-foundation / release-hardening

| ID | 根因/整改对象 | 正式来源 | 当前证据 | FR | 分类 | 级别 | 状态 |
|---|---|---|---|---|---|---|---|
| R-047 | 缺真实 parse/write/reparse、GUI/Agent/MCP/FOMOD parity 与正向成功链合同测试 | HQ Q-07～09；各 FR 测试结论 | 122 项定向测试通过但 6 个独立运行时阻断仍可复现 | FR1～16, NFR2/3/5 | C3 | P0/Blocker | 确认有效 |
| R-048 | QA 证据未绑定 commit/env/lock/JUnit/coverage/artifact；错误指标可被人工摘要为通过 | HQ Q-12；FR11 P0-2 | `.venv` 失效为误判；13% eval 与历史结果冲突仍有效；release S01 已增加不可篡改 verdict、环境/lock/Git/artifact 证据 | FR1～16 | C4 | P0/Critical | 部分修复（待各 Story 生成正式 evidence） |
| R-049 | NFR1 无可执行性能/内存/UI heartbeat/资源预算；同步 UI I/O 和 temp 泄漏 | HQ Q-10；FR1/3/8/14/16 | MainWindow 同步压缩/解压；FOMOD 临时目录无统一清理 | FR1/3/8/14/15/16, NFR1 | C2+C3 | P1/Major | 确认有效（预算待确认） |
| R-050 | NFR6 onefile 与实际 onedir+installer 冲突；无 clean build/install/upgrade smoke | HQ Q-01/Q-11；IR 5.1 | `transbridge.spec` 明示 onedir/COLLECT；requirements 仍要求单文件 | NFR6 | C2+C4 | P0/Critical | 确认有效；分发口径待确认 |

## 4. 正式报告覆盖矩阵

本矩阵确保原始发现未因根因合并而丢失。详细证据仍保留在原报告；后续回填时以本表定位到根因 ID，不重写历史上下文。

| 正式报告 | 对应根因 ID | Phase 0 判定 |
|---|---|---|
| `fr-01.md` | R-013～R-020、R-047、R-049 | ParaTranz JSON 已确认；DSD/SST/完整上下文等进入支持矩阵确认 |
| `fr-02.md` | R-008、R-017、R-020～R-022、R-044 | 核心状态合同有效；`id != key` 已按 ExternalEntryRef 校正 |
| `fr-03.md` | R-013、R-040～R-042、R-046 | Agent API 与安全/partial 问题确认 |
| `fr-04.md` | R-013、R-015～R-018、R-021、R-043 | 写回与多版本数据完整性问题确认 |
| `fr-05.md` | R-027、R-031、R-033～R-036、R-039 | BM25 “未声明”表述部分过期，其余风险有效 |
| `fr-06.md` | R-027、R-031、R-037、R-038 | 候选链、终态和报告单源问题确认 |
| `fr-07.md` | R-003～R-012、R-027～R-032 | 多 Agent 具体产品形态与知识注入需需求确认 |
| `fr-08.md` | R-008、R-020～R-026、R-049 | 数据生命周期与状态所有权问题确认 |
| `fr-09.md` | R-003～R-009、R-014/15、R-028、R-040～R-042、R-047 | 工具数量不代表成功链；根因已去重 |
| `fr-10.md` | R-003、R-007、R-012、R-029～R-032、R-048 | 文件拆分完成度与架构完成度分开处理 |
| `fr-11.md` | R-005、R-006、R-009、R-011、R-048 | 运行态帮助截断与 13% eval 已独立复现 |
| `fr-12.md` | R-006、R-025、R-027～R-030、R-047 | AWAITING_TASK、关联和状态校验问题确认 |
| `fr-13.md` | R-025/26、R-029、R-048 | 恢复、切换、安全和监听生命周期问题确认 |
| `fr-14.md` | R-027～R-031、R-047、R-049 | Task Monitor 只作为未来 JobSnapshot 投影 |
| `fr-15.md` | R-017、R-027、R-035、R-043～R-050 | TM 与 FOMOD 分开验收，假成功为发布阻断 |
| `fr-16.md` | R-002、R-004/5、R-014/15、R-020、R-044～R-049 | `migrate_entries` 占位、路径和归档风险确认 |
| `horizontal-architecture.md` | R-001～R-012、R-020～R-032、R-042～R-050 | 目标架构仍是候选；问题证据有效 |
| `horizontal-contracts.md` | R-001～R-008、R-013～R-032、R-042、R-046 | 四项 P0 与共享服务缺口均由当前事实支持 |
| `horizontal-quality.md` | R-001/2/4/6、R-031、R-040、R-043/46～R-050 | 质量/安全/发布门禁缺口确认 |
| `integrated-roadmap.md` | R-001～R-050 | 仅作为根因聚合候选，不直接接受其 ADR/Plan 方案 |
| `paratranz-json-compatibility-adjustment.md` | R-013、R-020、R-042 | 双 ID 是明确用户合同；不是 `id == key` 整改 |

## 5. 历史状态纠偏候选

Phase 1～3 不改写历史正文，只在相关旧 Plan 头部/状态区追加以下语义，并同步索引：

- `partially-verified`：有实现，但真实成功链/发布门禁未验证。
- `blocked_by: <new-plan/story>`：当前完成声明被已确认阻断项否定。
- `superseded_by: <new-plan>`：旧实现步骤或架构被新合同替代。
- `accepted-risk` / `deferred`：仅在用户明确确认后使用，必须有 owner、原因、到期/删除门禁。

首批需要纠偏的历史线：`file-parsing`、`file-writing`、`project-persistence`、`agent-upgrade`、`agent-tool-expansion`、`smart-assistant-refactor`、`tool-prompt-layering`、`session-controller`、`session-manager`、`task-monitor`、`ai-translation`、`ai-post-process`、`translation-memory`、`fomod-translation`、`agent-infra-tools`。

## 6. 建议复杂路线（待确认）

维持用户指定的阶段门禁：

```text
Phase 0 台账/路线确认（当前）
  -> Phase 1 需求增量/确认
  -> Phase 2 架构增量/确认
  -> Phase 3 Plan 与 Story/逐级确认
  -> Phase 4 按 Story 实现 + 测试 + 独立 changelog
  -> Phase 5 综合 QA（Blocker/Critical 暂停）
  -> Phase 6 正式报告回填
  -> Phase 7 索引与最终记录门禁
```

候选工作线保持为九组，具体 Plan 数量在 Phase 3 决定：

1. `architecture-contract-stabilization`：R-001～R-006、R-013、R-040、R-047/48/50 的 P0 基线。
2. `application-layer-foundation`：R-007～R-012。
3. `translation-io-kernel-v2`：R-013～R-020。
4. `project-session-persistence-v2`：R-021～R-026。
5. `unified-task-runtime`：R-027～R-032。
6. `translation-workflow-runtime`：R-033～R-039。
7. `paratranz-sync-service`：R-040～R-042；复用 R-013/R-020，不重做身份映射。
8. `fomod-pipeline-v2`：R-043～R-046。
9. `quality-foundation / release-hardening`：R-047～R-050；作为所有线的门禁，不在最后补测。

依赖顺序：

```text
architecture-contract-stabilization + quality-foundation
  -> application-layer-foundation
  -> {translation-io-kernel-v2, project-session-persistence-v2, unified-task-runtime}
  -> {translation-workflow-runtime, paratranz-sync-service, fomod-pipeline-v2}
  -> release-hardening / 综合 QA / 报告回填
```

编码阶段才启用无文件冲突的多 Agent；需求、ADR、Plan、Story 主文档与索引始终由主线串行修改。

## 7. 路线确认项

进入 Phase 1 前需要用户确认以下路线级选择；详细 Stage 矩阵、迁移字段和验收条款仍会在需求/架构/Plan/Story 各自门禁再次确认：

1. 长期目标采用“模块化单体 + Application Layer + Ports/Adapters + 单一 Composition Root”，以兼容 facade 渐进迁移，不做微服务化、全面事件溯源或一次性目录重写。
2. MCP 主拓扑采用独立 `transbridge-mcp` stdio 进程；桌面状态访问通过受控 RuntimeContext/后续 IPC。完成前 `mcp_enabled` 必须安全降级，不允许 GUI 崩溃或宣称可用。
3. Windows 正式分发口径改为“安装器 + onedir payload”，另提供便携包；不为旧 NFR 文本强制 onefile。
4. Phase 0 已确认的格式范围：ParaTranz JSON 双 ID、当前仍公开的 ESP/EET/XT Agent/UI 成功链、Localized Strings 完整性。SST Writer 保持 experimental/关闭；DSD、SST 当前工作台入口、BSA/Strings 互补和完整结构化上下文进入需求支持矩阵，保留则修、退役则移除公开能力描述。
5. 采用九组跨模块工作线，而不是按 FR1～FR16 逐个修补；`quality-foundation/release-hardening` 从第一批 Story 即作为门禁。
6. 旧 Plan/Story 只追加 `partially-verified`、`blocked_by`、`superseded_by` 等纠偏，不重写或伪造历史。

## 8. Phase 0 出口门禁

- [x] 已读取 Pilot/路径配置和仓库约束。
- [x] 已核对 Git 状态和既有 BM 产物。
- [x] 已排除 `.partial.md` 作为正式结论来源。
- [x] 已建立 50 项去重根因台账和正式报告覆盖矩阵。
- [x] 已记录确认有效、待产品确认、历史状态失真和描述偏差。
- [x] 已用运行时探针验证关键断裂，并证明现有 122 项定向测试未覆盖这些合同。
- [x] 用户已于 2026-08-18 确认复杂路线和六项路线级选择。
- [x] Phase 0 记录门禁完成，允许进入 Phase 1；业务代码仍保持未修改。

## 9. Phase 1 需求追溯与确认门禁

Phase 1 已在 `docs/requirements.md` 新增 FR17～FR23 与 NFR 增量草稿，未改变 FR1～FR16 的历史正文。根因到需求的承接关系如下：

- R-001～R-006 → FR17、NFR3.1、NFR6.1 及共同验收规则；
- R-007～R-012 → FR17、FR20.7、FR21.8；
- R-013～R-020 → FR18；
- R-021～R-026 → FR19；
- R-027～R-032 → FR20；
- R-033～R-039 → FR21；
- R-040～R-042 → FR18、FR22；
- R-043～R-046 → FR23；
- R-047～R-050 → NFR1.1～NFR6.1 及共同验收规则。

Phase 1 出口状态：

- [x] 需求以可验证行为、边界、失败语义和验收条件表达，未提前指定未确认的内部模块实现。
- [x] 已明确 P0 格式范围、兼容入口删除门禁、真实成功链和非 mock-only 证据要求。
- [x] 已将历史“已实现”与当前“已验收”分离，并更新 `docs/INDEX.md` 当前状态。
- [x] 已保留三个 `.partial.md` 只读，未修改正式审查报告或业务代码。
- [x] Phase 1 changelog 与索引记录门禁完成。
- [x] 用户确认 `locked(9)` 空译文阻止正式发布，预览可回退原文但产生阻断诊断。
- [x] 用户确认 DSD/SST Reader 为实验性、SST Writer 关闭、BSA/Strings 与完整上下文为 P1。
- [x] 用户确认初始性能、取消、恢复和长时稳定性预算；放宽需重新确认。
- [x] 用户确认 FR17～FR23 与 NFR 增量整体需求，允许进入 Phase 2 架构增量。

## 10. Phase 2 架构追溯与确认门禁

### 10.1 新增 ADR

- ADR-016：模块化单体应用层、Ports/Adapters、单一 Composition Root、RuntimeContext、独立 MCP/CLI/GUI 入口和兼容 facade 删除门禁。
- ADR-017：FormatAdapter、Parse/Write request/result、EntryKey/ExternalEntryRef、ParaTranz JSON 双 ID、StagePolicy、source snapshot 与原子发布。
- ADR-018：Project/Variant/Session 四级状态所有权、Repository/UnitOfWork、replace materialization、两阶段切换和 schema migration/quarantine。
- ADR-019：JobSpec/JobRef、统一任务状态机、owner/run_id、取消提交屏障、CheckpointPort、可释放订阅和 Task Monitor projection。

### 10.2 既有 ADR 增量

ADR-001、002、004、006、008～015 已按追加方式记录并接受 V2 边界；ADR-003、005、007 经评估保留历史决策，但其执行受 ADR-016～019 约束。没有改写历史背景。

需求到架构的主要追溯：

- FR17 → ADR-016、ADR-019、ADR-012 增量；
- FR18 → ADR-017、ADR-001/002 增量；
- FR19 → ADR-018、ADR-006/008 增量；
- FR20 → ADR-019、ADR-004/011 增量；
- FR21 → ADR-016、ADR-019，保留 ADR-003/005/007/009/013 的领域策略；
- FR22 → ADR-016、ADR-017、ADR-019、ADR-012 增量；
- FR23 → ADR-016、ADR-017、ADR-019、ADR-014/015 增量；
- NFR1～6 增量 → ADR-016～019 及 ADR-012/013/014/015 增量。

Phase 2 出口状态：

- [x] 已核对 README/依赖/入口/模块边界/需求/ADR/测试与当前实现证据。
- [x] 已评估 ADR-001～ADR-015；相同决策域优先追加，未静默改写历史理由。
- [x] 四份新 ADR 均包含关键合同、备选方案、影响风险、渐进迁移和回退。
- [x] 已使用 MCP 与 PyInstaller 官方文档校正 stdio/认证/协议协商和 onedir 表述。
- [x] 已更新 `docs/INDEX.md`，区分历史决策与 2026-08-18 已接受增量。
- [x] Phase 2 changelog 与索引记录门禁完成。
- [x] 用户以“继续”确认 ADR-016～019 及既有 ADR 增量，允许进入 Phase 3 Plan。

### 10.3 架构确认结果

- 确认时间：2026-08-18。
- 接受范围：ADR-016～019 全文，以及 ADR-001、002、004、006、008～015 的 2026-08-18 增量。
- 迁移原则：保留既有资产，按 compatibility facade、合同测试和删除门禁渐进迁移；接受架构不代表现有实现已经验收。
- 下一门禁：只创建 V2 Plan 草案和历史状态纠偏提议；用户确认 Plan 前不展开 Story、不修改业务代码。

## 11. Phase 3 V2 Plan 草案与确认门禁

### 11.1 Plan 组织与根因覆盖

| 顺序 | V2 Plan | Story | 根因 | 主要合同 |
|---|---|---:|---|---|
| 1 | `platform-contract-foundation-v2` | 5 | R-001～R-012 | 包/依赖/入口、Operation、capability、Composition Root、Tool/MCP 安全 |
| 2 | `translation-io-kernel-v2` | 6 | R-013～R-020 | FormatAdapter、双 ID、EntryKey、StagePolicy、Localized Strings、原子发布 |
| 3 | `project-session-persistence-v2` | 5 | R-021～R-026 | V2 schema、replace materialization、UoW、Session owner、projection |
| 4 | `unified-task-translation-runtime-v2` | 7 | R-027～R-039 | TaskRuntime、checkpoint、翻译/后处理 workload、canonical report |
| 5 | `paratranz-sync-service-v2` | 4 | R-040～R-042 | secret、typed client、dry-run/确认、事务同步 |
| 6 | `fomod-pipeline-v2` | 5 | R-043～R-046 | ArchivePolicy、typed stages、TM/provenance、XML 保真、staging publish |
| 横切 | `release-hardening-v2` | 5 | R-047～R-050 | 成功链、证据、性能/稳定性、Windows、安全、installer+onedir |

R-001～R-050 各有且只有一个主要 Plan owner；共享合同以依赖关系引用，不复制第二套实现。7 个 Plan 共 37 个候选 Story。

### 11.2 依赖与执行波次

1. `release-hardening-v2/S01` 先建立可复现环境和证据 manifest，同时推进 platform S01～S03。
2. platform 公共合同稳定后，I/O S01～S03、TaskRuntime S01～S03 和 persistence schema 可按无文件冲突并行。
3. I/O identity/Stage 与 Session owner 就绪后推进翻译/后处理 runtime；随后推进 ParaTranz 和 FOMOD 特有工作流。
4. 每 Story 完成即运行相关测试并追加独立 changelog；release S02～S04 随业务增量，S05 作为综合 QA/发行门禁。

### 11.3 历史状态纠偏提议

每个 V2 Plan 已列出受影响旧 Plan 的增量关系。Plan 确认后才在旧 Plan 头部追加 `partially-verified`、`blocked_by`、`superseded_by`，并同步索引；不会改写历史 Story 或伪造当时状态。首批范围仍为第 5 节列出的 15 条历史线。

Phase 3A 出口状态：

- [x] 7 个 Plan 均包含范围、依赖、文件落点、实施步骤、测试、验收、迁移/回退和追溯。
- [x] R-001～R-050 完整覆盖并保持单一主要 owner。
- [x] `plans/INDEX.md` 与 `docs/INDEX.md` 已列出草案，历史完成记录明确不等于本轮验收。
- [x] 修复 `plans/INDEX.md` 两条历史坏链，未改变历史状态或正文。
- [x] 未生成 Story 细化文档，未修改业务代码。
- [x] Phase 3 Plan 草案 changelog 与索引记录完成。
- [x] 用户确认 7 个 V2 Plan、37 个候选 Story、依赖顺序及历史状态纠偏提议，允许细化 Story。

### 11.4 Plan 确认结果

- 用户于 2026-08-18 明确回复“确认”。
- 7 个 V2 Plan 已提升为“已确认”；37 个候选 Story 成为已确认的细化范围。
- 20 个受影响历史 Plan 已在末尾追加 `partially-verified`、`blocked_by`、`superseded_by` 或保留资产说明；历史正文与原交付记录未被重写。
- `plans/INDEX.md` 与 `docs/INDEX.md` 已同步历史纠偏矩阵。
- 下一门禁：按依赖顺序生成 Story 交接文档；用户确认 Story 前不进入业务代码。

## 12. Phase 3 Story 细化与确认门禁

### 12.1 批次结果

- platform-contract-foundation-v2：5/5；
- translation-io-kernel-v2：6/6；
- project-session-persistence-v2：5/5；
- unified-task-translation-runtime-v2：7/7；
- paratranz-sync-service-v2：4/4；
- fomod-pipeline-v2：5/5；
- release-hardening-v2：5/5。

共 37/37 份 Story 交接文档。每份文档保持 Plan 的产品边界和验收标准，补充了当前调用链、数据流或事件顺序、计划接口/数据结构、依赖有序步骤、文件落点、错误边界、迁移/回退和测试策略。

### 12.2 一致性验证

- [x] 7 个 Plan 的 Story 标题、详细链接和实际文件数量一致（37/37）。
- [x] 37 份文档均为“草稿，待 Story 确认”。
- [x] 37 份文档均包含验收、数据/事件流、接口、边界、迁移或回退、测试内容。
- [x] Story 到所属 Plan 的本地链接全部可解析。
- [x] 未修改业务代码、测试代码、正式审查报告或 `.partial.md`。
- [x] 用户显式调用 `$bm-pilot`，确认继续 37 份 Story 细化和依赖顺序，允许进入 Phase 4 编码。

### 12.3 Story 确认结果

- 37 份 Story 状态已提升为“已确认（2026-08-18）”。
- Phase 4 第一波：`release-hardening-v2/S01`、`platform-contract-foundation-v2/S01/S02`；按文件隔离实现并由主线统一集成。
- 每 Story 完成后立即运行相关测试、审查 diff、追加独立 changelog；不等待全量编码后集中补记录。

## 13. Phase 4 第一实现波次记录门禁

### 13.1 已完成 Story

- `platform-contract-foundation-v2/S01`：包身份、版本、CLI/MCP 入口、uv lock、依赖 capability 和构建版本基线已实现；R-001 已修复，R-002 的最终发行物验证由 release S05 承接。
- `platform-contract-foundation-v2/S02`：Operation/Diagnostic/DomainError/Deferred/Capability 公共合同和 schema v1 已实现；真实 Composition Root/入口采用由 S03～S05 承接。
- `release-hardening-v2/S01`：版本化 EvidenceManifest、真实退出码、环境/lock/Git/artifact hash、secret 边界和 replay 已实现；后续 Story 必须实际使用该机制。
- `platform-contract-foundation-v2/S03`：Application Ports、单一 Composition Root、隔离 RuntimeContext/生命周期、GUI projection 注入和旧 controller 隐式 AppContext 构造清除已实现；具体 I/O/Repository/Task adapter 由下游 ADR Story 承接。

四份 Story 文档均增量标记为“实现完成，增量验证通过；待综合 QA”，并分别追加独立 changelog；未执行 Git commit/push。

### 13.2 验证与证据校正

- uv 受管环境：Python 3.12.12，可执行；早期失败由沙箱拒绝访问 `%APPDATA%/uv/python` 导致，不再把 `.venv` 标为损坏。
- 专属测试：platform S01 7 passed；platform S02 31 passed；release S01 8 passed。
- 公共能力集成：38 passed。
- 全量回归：S01/S02/release S01 门禁为 644 passed；加入 S03 后为 655 passed，15 个既有 deprecation warnings。
- clean install smoke：`uv run --isolated --locked --no-editable --no-group dev` 成功；离开仓库目录后 `import transbridge`、metadata version、CLI/MCP `--help` 均通过。
- 三个 Story 均生成并通过 verify 的独立 EvidenceManifest：[platform S01](qa-evidence/platform-s01/qa-20260818T062041.485189Z-77706e96f3d9/manifest.json)、[platform S02](qa-evidence/platform-s02/qa-20260818T062045.181590Z-45ec5809386f/manifest.json)、[release S01](qa-evidence/release-s01/qa-20260818T062045.127691Z-3ccc8a8af032/manifest.json)。
- S03 独立 EvidenceManifest：[platform S03](qa-evidence/platform-s03/qa-20260818T063830.239025Z-8624db537c3a/manifest.json)，verify 结果 `passed`。
- `src/transbridge` 与 `tests` 已无 `src.transbridge` 导入；Ruff、format、compileall、`git diff --check` 通过。

### 13.3 下一波依赖

下一步进入 `platform-contract-foundation-v2/S04`（Tool schema/HITL/路径授权/结构化 Observation/redaction）。完成同一记录门禁后推进 S05 与 I/O 基础 Story。

## 14. Phase 4 第二实现波次记录门禁（进行中）

### 14.1 已完成 Story

- `translation-io-kernel-v2/S01`：统一 I/O request/result、SourceSnapshot、FormatAdapter、12 个明确 FormatId、证据式格式探测和能力 policy ceiling 已实现；合法空、partial/failed/cancelled 与 SST Writer unavailable 合同由 23 个测试固定。主线发现并修正 `tests/contracts/io/__init__.py` 与标准库 `io` 的收集冲突后，正式 uv 环境复验通过。
- `unified-task-translation-runtime-v2/S01`：TaskRuntime 的 OwnerRef、不可变 JobSpec/Snapshot、能力型控制、锁内 compare-and-transition、互斥终态、订阅句柄和事件序列已实现；旧 TaskManager 改为运行时 projection/facade，任意 `set_status` 被拒绝、迟到相反终态通知被丢弃。核心、facade 和既有回归共 50 个测试通过。

两份 Story 均生成并复验 EvidenceManifest，状态为 `passed`；分别追加独立 changelog。当前累计 6/37 个 Story 增量验证通过，均仍待 Phase 5 综合 QA；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.2 尚未越界声明的能力

- I/O S01 只建立合同、探测和能力上限；未注册旧 parser/writer adapter，因此默认能力保持 unavailable，真实成功链由 S03/S04/S05/S06 承接。
- TaskRuntime S01 只建立状态权威和旧 facade；cooperative cancellation、commit guard、backend/shutdown、checkpoint 与业务 workload 由 S02～S07 承接。
- `translation-io-kernel-v2/S02`：SourceNamespace、EntryKey、ExternalEntryRef、EntryRevision、Provenance 与 ChangeSet/MutationPort 已实现；Collection 只保留一个 EntryKey 主索引，legacy id/key 为只读扫描 facade。可信 RequestContext 授权、revision 冲突和 ExternalEntryRef 冲突均在原子交换前校验；旧 updater 使用 `replace` 保留 V2 envelope。

### 14.3 S04 与 TaskRuntime S02 记录门禁

- `platform-contract-foundation-v2/S04`：canonical Tool JSON Schema、注册后 wildcard 冻结、请求绑定的一次性 HITL、递归路径授权、共享 SecretRedactor 与完整结构化 Observation 已实现。正式 uv 专属测试 30 passed、1 skipped，Smart Assistant 广泛回归 463 passed；跳过项为会话无 symlink 创建权限，Windows junction 逃逸测试实际通过。
- `unified-task-translation-runtime-v2/S02`：原子 CancellationToken/状态/permit 失效、runtime 私有 nonce CommitPermit、唯一提交屏障、thread/thread-pool/callback backend、stop/shutdown 与 Composition Root 退出顺序已实现。正式 uv 定向回归 78 passed；取消后迟到 completed、伪造/重放 permit、shutdown timeout 和 backend 异常均有合同测试。
- 两个 Story 的 EvidenceManifest 均通过 verify，分别为 [platform S04](qa-evidence/platform-s04/qa-20260818T071340.520815Z-07a69ad86bde/manifest.json) 与 [TaskRuntime S02](qa-evidence/task-runtime-s02/qa-20260818T071347.127426Z-5d7602b9de06/manifest.json)。
- I/O S02 正式 uv 回归 74 passed，6 个 warning 均为兼容 facade 弃用提示；[EvidenceManifest](qa-evidence/io-s02/qa-20260818T071937.593080Z-34c60020e704/manifest.json) 通过 verify。
- 当前累计 9/37 个 Story 增量验证通过，均待 Phase 5 综合 QA；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.4 保留边界

- Platform S04 未宣称 MCP 生命周期与跨入口 parity 完成；旧 MCP schema 二次包装由 S05 迁移。
- TaskRuntime S02 的取消保持协作式；无法停止的系统调用会如实保留 cancelling/timeout 与 `backend_released=False`。CheckpointPort 与幂等恢复由 S03 承接。

### 14.5 Platform S05 记录门禁

- `platform-contract-foundation-v2/S05`：独立 headless MCP runtime、严格 JSON-RPC 生命周期、stdout/stderr 边界、环境 SecretPort、无 Project 安全降级、旧 MCP facade 与 GUI/Agent/CLI/MCP EntrypointOperations parity 已实现。
- 主线正式 uv 回归 36 passed，包含无 `PYTHONPATH`、非 ASCII cwd 下安装态 `transbridge-mcp.exe` 的握手、工具列表、调用和关闭真实成功链；[EvidenceManifest](qa-evidence/platform-s05/qa-20260818T073434.582283Z-655fb7943423/manifest.json) 通过 verify。
- Platform Plan 的 5/5 Story 均已增量验证，但整个 Epic 状态保持“待综合 QA”；完整业务工具/I/O/Task parity 和 clean release 仍由下游 Story/Phase 5 承接。
- 当前累计 10/37 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.6 TaskRuntime S03 记录门禁

- `unified-task-translation-runtime-v2/S03`：严格 CheckpointRecord、原子 filesystem port、revision 单调保护、Graph frontier/result/branch/loop/HITL 安全点、真实 pause/resume 与不拥有终态的 GraphWorkloadAdapter 已实现。
- 主线正式 uv 定向回归 137 passed；子任务全仓回归 844 passed、1 skipped。[EvidenceManifest](qa-evidence/task-runtime-s03/qa-20260818T073803.376366Z-84ed3aa63e5f/manifest.json) 通过 verify。
- Windows 目录 fsync、跨进程 revision 锁、旧非原子 checkpoint 自动恢复和生产入口 wiring 均未越界宣称完成。
- 当前累计 11/37 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.7 Translation I/O S03 记录门禁

- `translation-io-kernel-v2/S03`：ParaTranz JSON 离线 Adapter 将 `key` 映射为稳定 EntryKey、可选 `id` 映射为 type-stable ExternalEntryRef；缺失 id 不合成，null 与缺失保持可逆区分。数组重排不改变默认 namespace，重复 key/id、非法 stage、扩展字段冲突均返回带 source index 的结构化诊断。
- 旧 Smart Assistant ParaTranz JSON parser/export 改为 V2 facade；离线 Adapter 未导入网络 client、config 或凭据。空 JSON 仍在 DSD/ParaTranz/internal 间保持格式歧义，publish capability 在 S06 原子发布完成前保持 unavailable。
- 主线锁定 uv 成功链：`tests/contracts/io`、Collection 显式回归和 `tests/paratranz` 共 **118 passed**，12 个 warning 均为既有 compatibility facade 弃用提示；[EvidenceManifest](qa-evidence/io-s03/qa-20260818T074317.837175Z-46043dfd68ad/manifest.json) 通过 verify。
- 当前累计 **12/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.8 Project/Session Persistence S01 记录门禁

- `project-session-persistence-v2/S01`：新增 Project/Variant/Session V2 schema、DTO、Repository 与受限 opaque ID；记录、backup、quarantine 和 staging 均由编码身份与根内路径生成。Variant 路径以 ProjectId 命名空间隔离，避免跨项目同名 Variant 碰撞。
- V1 迁移在副本内确定性完成，并先创建验证备份；不可迁移内容生成同根 verified quarantine 副本而保留原文件。future schema、未迁移 V1、损坏和引用不匹配记录均拒绝保存，不能被新 DTO 覆盖；staging/verified-copy 故障会清理半成品并保留原件。
- 主线锁定 uv 运行迁移、备份、隔离、路径、故障注入及真实临时文件系统成功链：**37 passed**；[EvidenceManifest](qa-evidence/persistence-s01/qa-20260818T075850.252252Z-c000800275c6/manifest.json) 通过 verify。Ruff check/format 和定向 `git diff --check` 通过。
- S01 未迁移旧 ProjectHandle/VariantStore/SessionManager facade，未实现完整 Variant replace materialization、UnitOfWork 或 Session 生命周期；这些保留给 S02～S05。当前累计 **13/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.9 ParaTranz Sync S01 记录门禁

- `paratranz-sync-service-v2/S01`：INI 仅保存 CredentialRef；旧明文 token 在 secure store 写入及回读验证成功后才以 atomic replace 移除。迁移失败保留原文件、标记 degraded 并关闭网络凭据；环境变量覆盖只读且绝不回写。
- 新 `SecretValue` 默认无明文 `str/repr`，复用共享 SecretRedactor 并对已知 secret 做精确 canary 脱敏。客户端与两个 legacy direct-request 旁路均在请求边界生成 Authorization；401、响应正文、URL、传输异常与嵌套对象均被脱敏。空 token/旧 ctx.config 被规范为无凭据，真实网络边界返回 `PARATRANZ_CREDENTIAL_REQUIRED`。
- 主线锁定 uv 回归：credentials security、ParaTranz workflow 和旧 Agent tools 共 **57 passed**、6 个既有 Collection facade 弃用 warning；[EvidenceManifest](qa-evidence/paratranz-s01/qa-20260818T080441.998014Z-c1ebdc7078bb/manifest.json) 通过 verify。Ruff check/format 与定向 `git diff --check` 通过。
- 未调用真实 ParaTranz 服务或写入用户真实凭据；Windows Credential Manager 使用注入式合同测试。typed client、dry-run/确认和事务同步仍由 S02～S04 承接。当前累计 **14/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.10 Translation I/O S04 记录门禁

- `translation-io-kernel-v2/S04`：新增 ESP/EET/XT FormatAdapter 与统一 TranslationIoUseCase；默认 catalog 仅为三种已实现格式开放 adapter。Adapter 以 SourceSnapshot 保留 parser/template/source locator/encoding/BOM/fingerprint，写入后重解析，不假设旧 parser/writer 具有一致构造签名。
- Agent parser dispatch 改走同一 use case，CollectionSlot 保存 `source_snapshot` 与 `format_id`；GUI/Agent 在 process-adapter boundary 复用相同合同。具体 adapter 的公开导出改为惰性加载，消除 TranslationEntry→I/O→Collection 循环导入而不删除既有 import API。
- 主线锁定 uv 合同、真实小型 EET/XT fixture、仓库真实 ESP、旧 parser/writer 与 Agent 集成回归共 **231 passed**；[EvidenceManifest](qa-evidence/io-s04/qa-20260818T080656.455807Z-2da9174ec8f8/manifest.json) 通过 verify。Ruff check/format、定向 `git diff --check` 通过；`tool_parser.py` 的未修改历史 UP037/E302 lint 债务仅在该文件检查时显式忽略，S04 修改的长 schema 描述保留原文并以逐行 E501 注释界定。
- `PluginWriter.get_by_key` 产生 8,757 条既有 compatibility facade warning，未视为完成证据。atomic publish/TOCTOU、localized ESP 的完整支持与旧 GUI 屏幕入口删除均未越界宣称完成，分别由 S05/S06 和最终集成承接。当前累计 **15/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.11 ParaTranz Sync S02 记录门禁

- `paratranz-sync-service-v2/S02`：新增 typed ParaTranzPort/DTO/service，修复 Agent 对不存在 API 的调用；HTTP 状态、timeout、transport、畸形响应与 schema 均映射为 secret-free 稳定错误分类。GET/安全幂等操作或显式幂等键使用有界 backoff/Retry-After，普通 POST 不自动重试。
- Cancellation 在请求前、退避期、响应后和 artifact 流每个 chunk 检查；`update_file_translation` 与 `download_artifacts` 两条 direct-request 旁路改入 typed transport。受控服务成功、401/403/409/429/5xx、timeout/transport、Retry-After、非幂等零重试和 cancel 均有合同覆盖。
- 主线锁定 uv 回归 `tests/paratranz` 与旧 Agent 工具共 **73 passed**、6 个既有 downloader compatibility facade 弃用 warning；[EvidenceManifest](qa-evidence/paratranz-s02/qa-20260818T082822.222454Z-337db1fa87b8/manifest.json) 通过 verify。新 typed 模块 Ruff check/format、兼容 API F/E9 门禁和定向 `git diff --check` 通过；history/strings API 的 12 条 I001/UP045/E501 是 `HEAD` 既有基线，未无关重排。
- 未调用真实 ParaTranz 服务；查询后 upsert 竞态、dry-run/确认、事务合并和 artifact staging/原子发布仍由 S03/S04 承接。当前累计 **16/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.12 Translation I/O S05 记录门禁

- `translation-io-kernel-v2/S05`：七级离散 StagePolicy 已统一 AI、PostProcess、TM、FOMOD 与 EET/Strings writer；Stage0/hidden 固定投影原文，locked-empty 固定阻断正式发布，避免旧 range/text-equality 推断。
- STRINGS/DLSTRINGS/ILSTRINGS 现完整保存 snapshot、ID 顺序、raw chunk、编码/BOM 与 locator，只替换指定词条；SSE loose strings 由 adapter 统一 rebuild，缺 snapshot 明确拒绝写回。FOMOD 写回转经 TranslationIoUseCase，失败不再被吞后记为完成。
- 主线锁定 uv 的 I/O、SSE parser/writer、EET writer、Agent、TM 与 FOMOD 回归共 **259 passed**；[EvidenceManifest](qa-evidence/io-s05/qa-20260818T083955.212699Z-82001a337954/manifest.json) 通过 verify。26,262 条 warning 来自既有 compatibility facade/direct mutation，未计为功能完成证据。Ruff/format、F821 和定向 `git diff --check` 通过。
- 原子 staging/backup/verification/TOCTOU 由 S06 承接，BSA 内嵌 localized strings 继续 experimental。当前累计 **17/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.13 ParaTranz Sync S03 记录门禁

- `paratranz-sync-service-v2/S03`：sync plan 以 EntryKey、ExternalEntryRef、revision、项目/来源 scope 和全量哈希绑定，不将远端 id 用作本地身份。dry-run 只读取远端，行动投影不包含原文/译文；删除仅来自显式 tombstone。
- confirmation token 绑定 owner、operation、plan hash、scope、有效期与一次性 nonce；重放、跨 owner/plan/project/source、计划过期以及本地/远端 revision 或内容变化均拒绝。S03 没有执行/写端口，GUI Smart Assistant、Agent、legacy MCP 使用相同的只读 DTO。
- 主线锁定 uv 的 sync plan、确认、typed client、credential、workflow、Agent/MCP 回归共 **109 passed**、6 个既有 downloader compatibility facade 弃用 warning；[EvidenceManifest](qa-evidence/paratranz-s03/qa-20260818T084547.628796Z-bb5fa0a9d016/manifest.json) 通过 verify。新模块 Ruff/format 与定向 `git diff --check` 通过。
- 上传/下载 legacy facade 尚未转入 token executor；事务合并、partial retry 与 artifact staging/atomic publish 均由 S04 承接。当前累计 **18/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.14 Project/Session Persistence S02 记录门禁

- `project-session-persistence-v2/S02`：VariantAggregate/VariantChangeSet 将完整 EntryKey、空值/tombstone、stage、labels、provenance、revision、source namespace 与 fingerprint 预校验后一次 swap；A→空→B、重启清空及多来源同 key 隔离均固定为回归。
- 旧 VariantStore 改为兼容投影委托；缺 source baseline 时发出 DeprecationWarning，不伪称完成无损 replace。旧 facade 不能无损承载 labels/revision/provenance，权威状态仍在 V2 aggregate。
- 主线锁定 uv `tests/persistence/v2` 共 **49 passed**；[EvidenceManifest](qa-evidence/persistence-s02/qa-20260818T085700.495934Z-b3a3dc1fc4fb/manifest.json) 通过 verify。Ruff/format 与定向 `git diff --check` 通过。
- GUI/session active-variant 生命周期与 baseline 注入由 S03 承接，100k benchmark 由 release S03 承接。当前累计 **19/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.15 Translation I/O S06 记录门禁

- `translation-io-kernel-v2/S06`：PublishCoordinator 使用目标同目录、0600 随机 staging，执行 render→结构/重解析/fidelity 验证→manifest/hash→目标指纹/备份→fsync/atomic replace；无 delete-then-replace 降级。权限、磁盘、fsync、replace、cleanup 失败及取消竞态均保留既有目标。
- EET、XT 与 Localized Strings 具备真实 parse-write-reparse 成功链；ESP fixture 仍为 partial，正式发布按合同拒绝而非误报完成。UNC/cross-volume 能力不足拒绝；映射网络盘无法仅凭路径可靠识别，保留为综合 QA 风险。
- 主线锁定 uv 集成与合同测试 **127 passed、1 skipped**；跳过项为 Windows symlink 权限。26,247 条 warning 为既有 PluginWriter compatibility facade 弃用噪声，未被当作完成证明。[EvidenceManifest](qa-evidence/io-s06/qa-20260818T090452.960541Z-c71a10145b91/manifest.json) 已通过 verify；Ruff/format 与定向 diff-check 通过。当前累计 **20/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.16 Unified Task/Translation Runtime S04 记录门禁

- `unified-task-translation-runtime-v2/S04`：`transbridge.ini` v2 ConfigRepository 现为唯一生产 INI 所有者，含 revision、跨进程锁、原子写入/读回、future schema 与明文 secret fail-closed。旧 `paratranz_config.ini` 只读迁移，经 CredentialStore 读回验证后写入脱敏 validated backup；`llm_profiles` 和生产 direct ConfigParser 旁路已移除。
- LLM/ParaTranz facade 读取同一 immutable revision；provider/base_url/model 强制同次完整更新。TranslationRunSpec 固化配置摘要，ActionPlan 互斥分区并由 StagePolicy 排除 hidden/locked；ContextPlanner 固定 Quest barrier 和 unknown 降级，禁用 retrieval 不加载语料或 vector index。
- 锁定 uv 合同与兼容回归 **141 passed**、12 条既有弃用 warning；[EvidenceManifest](qa-evidence/task-s04/qa-20260818T092608.226604Z-74735433e0f6/manifest.json) 通过 verify。新模块 Ruff/format 与定向 diff-check 通过。完整入口的 RunSpec 生命周期/checkpoint 接线仍由 S07 承接。当前累计 **21/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.17 Unified Task/Translation Runtime S06 记录门禁

- `unified-task-translation-runtime-v2/S06`：后处理收敛为不可变候选链与单一报告源。`PostProcessCandidate` 经 refine→polish→arbitrate 逐阶段传递；新增 `CheckerStage`（legacy checker candidate DTO adapter）、`LlmPostProcessStage` 与真实 HTTP 的 `OpenAiPostProcessHttpPort`；arbitrate 产出 `accepted` 决策，拒绝/待定附诊断不覆盖正文。
- 阶段 typed 失败聚合为 `PARTIAL`、异常聚合为 `FAILED`、取消为 `CANCELLED`，异常不被吞；`REVISION_CONFLICT`（expected_revisions 不匹配）聚合为 PARTIAL/FAILED 并携带诊断。`PostProcessCheckpoint` 每阶段原子落盘 stage/candidate hash，身份/指纹绑定、revision 单调，`resume_after_phase` 按位置恢复且不重放 LLM。
- `ReportSnapshot` 增加 issues/failures/timing_ms/run_spec_summary；JSON/CSV/Excel renderer 只消费 snapshot，`render_report` 渲染失败不回滚已提交业务并附 `REPORT_RENDER_FAILED` 诊断；prompt/secret 不进入报告。
- 正式 uv 回归 `tests/contracts/translation` + `tests/integration/translation/test_http_postprocess_chain.py` 共 **51 passed**；受控 HTTP 成功链证明 refine 输出进入 polish/arbitrate 且幂等键唯一。[EvidenceManifest](qa-evidence/task-s06/qa-20260818T105914.225150Z-70f6d9a4f364/manifest.json) 通过 verify；Ruff/format 与定向 diff-check 通过。
- GUI/Agent 生产入口切换与旧 TaskManager/MixedWorker 删除门禁清单仍由 S07 承接。冻结基线 30 项（platform/io/persistence/paratranz/task-s05/fomod-s01~04/release-s01 的既有 evidence）保持冻结，本轮仅按既有 evidence 同步累计计数。当前累计 **31/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.18 Unified Task/Translation Runtime S07 记录门禁

- `unified-task-translation-runtime-v2/S07`：新增 `RuntimeTaskBridge`（submit 返回 `Deferred[JobRef]`，AWAITING_TASK 生产路径；wait_terminal 映射 `TerminalOutcome`；`to_operation_result` 按操作合同序列化——COMPLETED 携带 snapshot，FAILED/CANCELLED 不带 value）、`SessionJobGate`（只接受活动 session 的 FINISHED 事件，旧 session 迟到事件仅审计，无写路径）与只读投影 `job_snapshot_to_view`/`RuntimeTaskProjection`（保留旧 Monitor 键，按钮按 capability/状态经 runtime 控制，cleanup 为视图本地动作）。
- 兼容清单与删除门禁落地为 `docs/dev/task-runtime-compat.md`：登记 `tool_translator/proofreader/paratranz/writer` 的 `TaskManager()`/`set_status`/`start_thread`/`is_long_running`、`session_controller.handle_task_*`、`graph_executor TaskManager()` 全部调用方与六条删除门禁；本 Story 不删除公开入口。
- 正式 uv 回归 `tests/contracts/tasks` + task runtime/checkpoint 核心回归共 **63 passed**（S07 新增 11 项）；[EvidenceManifest](qa-evidence/task-s07/qa-20260818T110604.244537Z-52110446aece/manifest.json) 通过 verify。Ruff/format 与定向 diff-check 通过。`test_task_runtime_backends.py` 的既有 P95 取消预算用例在 evidence wrapper 负载下偶发抖动（直接运行 73 passed），归 release S03 性能门禁。
- unified-task-translation-runtime-v2 的 7/7 Story 全部完成增量验证，Epic 状态保持“待综合 QA”。当前累计 **32/37** 个 Story 增量验证通过；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.19 FOMOD Pipeline S05 记录门禁

- `fomod-pipeline-v2/S05`：新增 `application/fomod/publish.py` 的 `StagingPackPublisher`，打包写入与目标同卷的 `.name.<token>.stage<suffix>` 受限 staging（保留格式后缀供归档检测器分派），builder/pack 永不直接写正式路径；目标仅在唯一提交点原子替换、绝不先删旧产物；`fomod/stages.py::PublishStage` 由直接 pack 到正式路径改为调用该发布器，产出 `published_archive` + `publish_manifest` 工件。
- 重开验证（同 `ArchivePolicy` 核对条目/hash/size/root layout/预算，空档与重开失败聚合为 typed diagnostic）；成功后原子写 `FomodManifest`（对应 run_id/locale/config_hash/input hashes/policy ids/build fingerprint/artifact hash+size）；提交前检查取消/run_id/目标指纹（TARGET_FINGERPRINT_CONFLICT），失败保留既有目标并经验证备份；`CleanupPolicy` 驱动清理，清理失败记录 `STAGING_CLEANUP_FAILED` 并保留残留。
- 正式 uv `tests/test_fomod_staging_publish.py` 共 **10 passed**；全 FOMOD 回归（typed 九阶段、archive policy、TM/provenance、legacy facade）**84 passed**；[EvidenceManifest](qa-evidence/fomod-s05/qa-20260818T111850.957164Z-c5b4f49d0733/manifest.json) 通过 verify；Ruff/format 与定向 diff-check 通过。
- fomod-pipeline-v2 的 5/5 Story 全部完成增量验证，Epic 状态保持“待综合 QA”。Windows 共享 `.tmp_tests` 下个别 7z/typed 全管线用例偶现 os.replace/文件锁抖动（隔离与整批重跑均绿，既有基线环境现象）。当前累计 **33/37** 个 Story 增量验证通过（剩余 release-hardening-v2 S02～S05）；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.20 Release Hardening S04 记录门禁

- `release-hardening-v2/S04`（并发子代理交付，主线复核）：新增 `tests/capability/matrix_generator.py`（从 FormatCatalog + 依赖 report 生成闭合 CapabilityMatrix，11 格式 × 7 入口=77 cell 全闭合，缺结果抛 `CapabilityMatrixError`；SST Writer write/roundtrip/publish 固定 unavailable）、`tests/security/test_windows_paths.py`、`test_archive_attack_corpus.py`、`test_dependency_degraded.py`、`test_secret_canary.py`。
- 结论：UI/Agent/MCP 对同一 (format,entry) 返回同一 capability id/reason；归档恶意 corpus（zip-slip/绝对/驱动器/UNC/NUL/保留名/link/special/重复规范/超预算）在 ArchivePolicy 写前拒绝、真实恶意 ZIP 经 Extract 目标目录零写入；7z/RAR 缺库上报 capability；可选依赖缺失=degraded、disabled 检索零 corpus/零 vector 加载；secret canary 经共享 SecretRedactor 脱敏后全消失。junction/长路径/非法文件名字需特权或系统开关→ skip 并说明（不以跳过算通过）。
- 正式 uv 回归 `tests/security tests/capability`（TMP 隔离）共 **71 passed、3 skipped**；[EvidenceManifest](qa-evidence/release-s04/qa-20260818T113135.348795Z-4b1648a19b08/manifest.json) 通过主线 verify；ruff 0。当前累计 **34/37** 个 Story 增量验证通过（remaining：release S02/S03/S05）；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.21 Release Hardening S02 记录门禁

- `release-hardening-v2/S02`（并发子代理交付，主线复核）：新增 `tests/quality/success_chains.py`（SuccessChain 值对象 + 7 份真实 fixture 的 sha256 注册表/preflight 防漂移 + run_chain_deterministic(3 次确定性) + assert_entrypoint_parity + summarize 归一化）与 `tests/quality/test_success_chains.py`（12 项：EET/XT/Strings(SSE)/ESP/ParaTranz 各自真实 parse→write→reparse、受控 HTTP 后处理链 refine→polish→arbitrate、FOMOD typed 九阶段链、GUI/Agent 跨入口 parity、fixture checksum 防漂移）。全程真实组合根（TranslationIoUseCase/真实 adapter/受控 HTTP 真实网络栈/PipelineEngine），mock 仅用于外部故障注入。
- 正式 uv `tests/quality`（TMP 隔离）共 **20 passed**；[EvidenceManifest](qa-evidence/release-s02/qa-20260818T113458.115867Z-5a5e5494471d/manifest.json) 通过主线 verify；ruff 0。既有 `test_evidence.py` 整目录并发跑偶发 git-hash 竞态 PermissionError（单文件/重跑全绿，环境 flakiness，非本 Story）。当前累计 **35/37** 个 Story 增量验证通过（remaining：release S03/S05）；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.22 Release Hardening S03 记录门禁

- `release-hardening-v2/S03`（并发子代理交付，主线复核）：新增 `tests/performance/benchmark_cases.py`（HardwareTier/THRESHOLDS_V1 版本化预算唯一真源/BenchmarkCase + 8 用例注册表）、`measure.py`（自实现 P50/P95、perf_counter 采样独立于业务 clock、psutil RSS + tracemalloc 兜底、子进程隔离）、`test_performance_gates.py`。
- 本机早期反馈（鲜活实测）：小 ESP 解析 P95≈1.05s（≤3s）、中 parse P95≈660ms（≤30s，RSS<<1GiB）、取消 P95 亚毫秒（并发≤3、≤1s）、checkpoint save/load P95≈16/14ms（≤100ms，10k 校准样本）、500 轮 Session RSS 增长 0.14%（≤15%）、UI heartbeat/progress 边界探针 ≤200/≤500ms（PyQt 无法驱动 GUI 时用最小探针并明确边界，真实 GUI 归 S05）。100k checkpoint 真实探针 378/222ms 超 100ms 预算——记为早期反馈、阈值未放宽、权威复验留 S05。
- 正式 uv `tests/performance`（TMP 隔离）共 **12 passed**；[EvidenceManifest](qa-evidence/release-s03/qa-20260818T114219.894166Z-1051f99b5747/manifest.json) 通过主线 verify；ruff 0。唯一仓库级改动 `.gitignore` 追加 `/qa-tmp-*/`（消除 evidence 工具与并发会话临时文件的 git-hash 竞态）。当前累计 **36/37** 个 Story 增量验证通过（remaining：release S05）；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告。

### 14.23 Release Hardening S05 记录门禁（最终）

- `release-hardening-v2/S05`：新增 `tests/packaging/test_clean_release_smoke.py`（许可证清单、artifact SHA-256、安装态不依赖仓库 src、版本/AppId 单一来源与原地升级、卸载不静默删用户项目 via iss 审查）、`tests/packaging/test_final_qa_gate.py`（综合 evidence 门禁：每个 Story 目标最新 EvidenceManifest 的 schema v1 + 业务 verdict passed，历史 superseded 不重审）、`tools/qa/final_qa.py` → `final-release-qa-2026-08-18.md`。
- 最终 QA 汇总：36/36 Story 目标业务 verdict passed；19 项 input 回读漂移为整改全程代码/依赖演化所致、按冻结基线不重做、记录非 blocker；真实 Windows clean VM 安装/卸载不在开发机执行，以代码级 iss 审查 + non-editable 植入 smoke（S01）+ evidence 汇总作为等价证据。
- 正式 uv `tests/packaging/test_clean_release_smoke.py + test_final_qa_gate.py` 共 **8 passed**；[EvidenceManifest](qa-evidence/release-s05/qa-20260818T115036.612516Z-28b15265e093/manifest.json) 通过 verify；ruff 0。release-hardening-v2 的 5/5 Story 完成；当前累计 **37/37** 个 Story 增量验证通过（全部七个 V2 Plan 完成）；未执行 Git commit/push，未修改 `.partial.md` 或正式审查报告正文。下一步：正式审查报告回填（Phase 6）与最终索引一致性门禁。

## 15. 综合 QA 复核与防御性修复收口（2026-08-18）

- 重新核对 DSH 交付的当前代码、Story、测试和 evidence，而非沿用旧结论。确认并修复：PostProcess 生产入口未统一、partial report 被成功 commit 覆盖、TaskManager/Session 仍有双状态与迟到事件风险、FOMOD 提交后 manifest 失败误报、最终 evidence target 集合缺少显式基线，以及 Windows 原子替换/取消探针竞态。
- `task-s04`：ConfigRepository 使用稳定绝对锁键，WinError 5/32 有界重试且不降级非原子写；并发与故障测试连续 10 轮通过。[EvidenceManifest](qa-evidence/task-s04/qa-20260818T131518.084476Z-b513ef423500/manifest.json) 为 47 passed。
- `task-s06`：GUI/Agent/AutoTranslator 共用 candidate workload、canonical report 与唯一 CommitTranslations；组合 outcome 保留 partial/failed/cancelled。[EvidenceManifest](qa-evidence/task-s06/qa-20260818T125309.913470Z-4d4ed7c14d10/manifest.json) 为 72 passed。
- `task-s07`：GUI facade 绑定同一 AppRuntime.tasks，Session 使用真实 JobRef/run_id，timeout 不伪造 cancelled；checkpoint 锁键/revision/Windows replace 收口，取消与 checkpoint 竞态压力测试各连续 10 轮通过。[EvidenceManifest](qa-evidence/task-s07/qa-20260818T131148.537899Z-eba50a5ee88c/manifest.json) 为 146 passed。
- `fomod-s05`：archive 已提交而 manifest 失败时返回带工件证据的 PARTIAL；typed pipeline 不再丢失已发布事实，Windows staging replace 有界重试。[EvidenceManifest](qa-evidence/fomod-s05/qa-20260818T130414.983224Z-20c33bd4356c/manifest.json) 为 76 passed。
- `release-s05`：`expected_evidence_targets.json` 精确固定 37 个目标；缺失、额外或 latest 非 passed 均失败。release evidence 使用短 Windows basetemp 并排除 4 项自引用门禁：1370 passed、5 skipped；随后自检 4 passed，合计 **1374 passed、5 skipped、0 failed**。[EvidenceManifest](qa-evidence/release-s05/qa-20260818T131538.395439Z-e907afb40368/manifest.json) 已通过 verify，最终报告为 37/37 passed。
- 当前正式工具注册数 50 与 FR16/ADR 的七类工具及 `plan_sync` 合同一致；旧测试的“少于 50”是过期描述，已改为精确数量及必需集合验证，不产生无意义的工具删除。
- Ruff check 与 format-check 对本轮核心范围全部通过；`git diff --check` 作为最终门禁执行。历史失败/blocked evidence 均保留为过程证据，latest passed 才参与最终门禁；未修改任何 `.partial.md`，未执行 Git commit/push。

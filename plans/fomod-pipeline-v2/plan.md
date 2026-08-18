# FOMOD Pipeline V2

- **状态**：实现完成，综合 QA 通过（2026-08-18）
- **日期**：2026-08-18
- **需求**：FR23、FR18.7/9、FR20、FR21.1/5/6、NFR2.1、NFR4.1
- **架构**：ADR-014/015 的 2026-08-18 增量、ADR-016/017/019
- **问题**：R-043～R-046
- **依赖**：`platform-contract-foundation-v2` S02～S04；`translation-io-kernel-v2` S02/S05/S06；`unified-task-translation-runtime-v2` S01～S06

## 目标与边界

把 FOMOD 的发现、解包、差异、迁移、翻译、XML 更新、资源过滤、构建和发布建模为 typed stages。所有中间产物进入 staging；致命失败或取消阻断发布；target language、Stage、TM provenance 与报告贯通。

现有 builder、XML、TM、fileops 和 UI 资产作为 adapters 保留。本 Plan 不开放未经支持矩阵验证的格式，也不删除旧入口。

## Story 清单

### Story 01：ArchivePolicy、安全预算与 MOD 根归一化

[详细设计](stories/story-01-archive-policy-root.md)

- **目标**：对 ZIP/7z/RAR 使用同一成员策略，并拒绝路径逃逸、资源炸弹和歧义根目录。
- **文件落点**：`fileops/archive.py` facade、新 archive policy/adapter、FOMOD discovery、fixtures。
- **实施**：提取前逐成员规范化；限制条目数、总展开量、压缩比、深度、特殊文件和链接；先验证后写入；识别唯一 MOD 根，多候选要求确认；统一 progress/cancel。
- **验收**：所有支持归档执行同一 policy；恶意成员在写入前失败；多个候选根不选择第一个；取消清理 staging。
- **测试**：ZIP/7z/RAR 正向 corpus、zip-slip/链接/炸弹预算、歧义根、非 ASCII/长路径和取消。

### Story 02：Typed Pipeline、RunSpec 与阶段终态

[详细设计](stories/story-02-typed-pipeline-states.md)

- **目标**：每阶段返回结构化结果，必要阶段异常不再被吞，取消后不 pack。
- **文件落点**：重构 `fomod/pipeline.py` 为 application workload/stage adapters；TaskRuntime 集成。
- **实施**：FomodRunSpec 固化 target locale、输入摘要、策略和 run_id；stage result 包含 outcome/artifacts/diagnostics；fatal/partial/cancel 传播；阶段依赖与发布门禁显式。
- **验收**：任一必要阶段失败/取消阻止后续发布；终态与 TaskRuntime/报告一致；目标语言无隐式默认回退。
- **测试**：全阶段成功链、每阶段 fault injection、取消点矩阵、终态互斥和 target language spy。

### Story 03：TM、Key Migration、Stage 与 Provenance 合同

[详细设计](stories/story-03-tm-migration-provenance.md)

- **目标**：统一插件翻译、FOMOD XML、TM 套用和 AI 候选的身份/语言/冲突语义。
- **文件落点**：`translation_memory/`、`migrator/` adapters、FOMOD translation stages、CandidateSet commit。
- **实施**：TM 保存 locale/Stage/provenance/dictionary source；KeyMigrator 使用 source namespace/fingerprint；冲突输出候选并按显式策略仲裁；hidden/locked/empty 遵守 StagePolicy；正式状态只在唯一 commit 点更新。
- **验收**：不同 locale 不串用；STALE/冲突可见；TM/AI/XML 报告来源可追溯；locked-empty 阻断正式发布。
- **测试**：多词典/多 locale fixtures、冲突、source change、Stage 矩阵、候选提交/取消。

### Story 04：FOMOD XML 与资源保真、过滤规则修正

[详细设计](stories/story-04-xml-resource-fidelity.md)

- **目标**：更新可翻译文本但保留未参与节点、属性、namespace、图片引用和目录语义。
- **文件落点**：`fomod/fomod_xml.py`、`fileops/filter_rules.py`、builder adapters、golden fixtures。
- **实施**：XML round-trip 保真边界；资源过滤按目录/角色而非通用扩展名；skip-hash 仍验证来源/目标摘要；source arbitration 明确优先级和诊断。
- **验收**：图片和 UI 资源不被默认误删；未知节点/属性/namespace 保留；skip 只有摘要一致才成立。
- **测试**：XML golden、图片/目录语义、未知扩展、hash 命中/错配和差异报告。

### Story 05：Staging Build、验证与原子发布

[详细设计](stories/story-05-staging-build-publish.md)

- **目标**：构建和归档在隔离区完成，验证后一次发布并可靠清理。
- **文件落点**：`fomod/builder.py` facade、publish port、pipeline manifest、GUI adapter。
- **实施**：每 run 独立 staging；构建产物重开/清单/摘要验证；同卷原子替换和备份；成功/失败/取消清理策略；TaskRuntime commit guard。
- **验收**：既有产物在失败/取消时不变；成功归档可解包且 manifest 对应输入/策略/run_id；临时目录无泄漏。
- **测试**：构建成功链、磁盘/权限/压缩错误、取消 race、旧产物保留和 cleanup 检查。

## 追溯与历史状态纠偏提议

| 需求/问题 | Story | 历史 Plan 提议状态 |
|---|---|---|
| FR23.1/2/6；R-043 | S02/S05 | `fomod-translation`: `partially-verified`, pipeline/publish `superseded_by` 本 Plan |
| FR23.3；R-044 | S03 | `translation-memory`: `partially-verified`, `blocked_by: fomod-pipeline-v2/S03` |
| FR23.4/5；R-045/046 | S01 | `agent-infra-tools`: `partially-verified`, archive policy `superseded_by` 本 Plan |
| FR23.7；R-045 | S04 | `fomod-translation` builder/XML 完成声明 `blocked_by` 本 Plan S04 |

## 风险、回退与完成门禁

- 风险：归档库对链接/metadata 支持不一致。控制：能力矩阵与拒绝策略，不能安全检查的格式不发布。
- 风险：资源过滤收紧改变产物大小。控制：dry-run manifest 与差异报告，默认保留未知资源。
- 回退：可保留旧 builder 作为 stage adapter；不得回退到直接目标写入或吞异常后继续 pack。
- 完成门禁：所有 stage fault/cancel、三类归档安全 corpus、XML/资源 golden、真实 staging 发布链通过。

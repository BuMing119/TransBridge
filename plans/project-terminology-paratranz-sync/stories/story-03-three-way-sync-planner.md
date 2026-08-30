# Story 03：三方差异、回声阻断与可失效同步计划

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17.1、FR5.17.2、FR5.17.4～FR5.17.6
- **前置依赖**：[Story 01](story-01-typed-paratranz-terms-port.md)、[Story 02](story-02-sync-line-baseline-persistence.md)
- **下游调用方**：S04/S05 executor、S07 GUI/Agent/MCP plan projection

## 目标

以不可变已发布本地版本、稳定远端术语快照和最后成功共同基线执行纯三方比较，生成确定、无副作用、可分页检查且与所有输入 revision 绑定的术语同步计划。planner 只描述影响，不调用网络写、draft mutation 或 baseline commit。

## 原始验收标准

- planner 以“当前已发布本地版本 + 当前远端快照 + 最后成功共同基线”三方比较，稳定区分 local-only、remote-only、unchanged echo、local changed、remote changed、both changed、deleted 和 unknown。
- 备份模式只产生远端 create/update、受管 delete、conflict、lossy skip；远端独立项不会下载、覆盖或删除。
- 双向模式同时产生上传、入站候选、冲突、跳过和删除影响；冲突不自动选择本地或远端，远端删除只形成入站 suppression/delete proposal。
- 插件作用域或其他有损项始终可见且不可执行；同目标多 Variant 歧义在 plan ready 前阻断。
- plan hash 包含 local version ID/digest、remote snapshot digest、baseline revision、target/binding revision、sync profile 和全部 item；任一输入变化使确认失效。
- 相同三方输入重复规划产生相同排序、counts、item identity 和 plan hash；已同步回声不产生新候选或冲突。

## 输入、输出与受影响调用方

- 本地输入必须是 `EffectiveTerminologySnapshotStatus.READY` 的显式 version snapshot；`current` 只能在 create-plan 边界解析一次，plan 内保存确切 version ID/digest。
- 远端输入是 S01 `ParaTranzTermSnapshot`；`stable=False`、重复 remote ID 或分页漂移时 plan 为 blocked，不能由用户确认绕过。
- baseline 输入是 S02 `TerminologySyncBaseline` 和 item links；存储 unavailable 与“无 baseline”是不同状态。
- target 输入来自 `ParaTranzTargetResolver` 的可执行绑定，并保存 binding revision、endpoint、account user 和 remote project ID。
- 输出 `TerminologySyncPlan` 被 S04/S05 授权与执行，被 S07 分页展示；任何 adapter 只能投影，不能重新解释 action。

## 三方匹配与比较顺序

```text
published local snapshot ─┐
remote stable snapshot ───┼→ identity matching → base-relative classification → mode/policy action
persisted baseline/link ──┘
```

匹配必须按以下优先级进行：

1. baseline 中已确认的 `local term_id ↔ remote_id` link；这是唯一可直接认定 managed echo/managed delete 的身份。
2. 同一 baseline 的 tombstone/history link，用于识别旧副本复活和 remote ID 重用；不得自动重新绑定。
3. 首次无 baseline 时按规范 original/scope 构造 `safe_match_proposal`。同原文不等于已受管，只能在内容完全相同且用户确认 adopt-link 后建立 link；不同内容直接冲突。
4. 没有匹配的 remote term 是 independent remote；没有匹配的 local effective decision 是 local-only。

每侧相对 common digest 计算 `present/changed/deleted/unknown`。如果 baseline outcome 为 unknown、remote ID 被重用、共同内容不可验证或两个 local term 指向同一 remote ID，则分类为 conflict/blocked，不走默认偏好。

## 计划新增的关键接口

- `TerminologySyncMode`：`BACKUP`、`BIDIRECTIONAL`，不提供隐式 AUTO。
- `TerminologySyncAction`：至少包含 `CREATE_REMOTE`、`UPDATE_REMOTE`、`DELETE_REMOTE`、`PROPOSE_LOCAL_ADD`、`PROPOSE_LOCAL_UPDATE`、`PROPOSE_LOCAL_SUPPRESSION`、`ADOPT_LINK`、`SKIP`、`LOSSY_MAPPING`、`CONFLICT`、`BLOCKED`。
- `TerminologySyncReason`：稳定 machine code，覆盖 echo、independent remote、both changed、remote ID missing/reused、plugin scope、variant mapping、unknown outcome 等。
- `TerminologySyncPlanItem`：stable item ID、local/remote/base summaries、action/reason、managed ownership、destructive、requires review、可选 remote/local refs。
- `TerminologySyncPlan`：line/target/profile refs、三份 input digest/revision、items/counts、blocked/conflicts/destructive、plan hash。
- `CreateTerminologySyncPlanRequest` 与 `AuthorizeTerminologySyncPlanRequest`：显式 local Project/Variant/version、resolved target、mode、owner、可选 confirmation。
- `TerminologySyncPlanningUseCase`：create/issue_confirmation/authorize；复用 `ConfirmationAuthority` 的一次性 token，但 request hash 命名空间独立于 translation-entry sync。

## 依赖有序的实施步骤

1. 在 `plan_models.py` 定义 enum、summary、item、plan 和 canonical serialization。所有 tuple 排序和 counts 一致性在构造时验证。
2. 在 `mapping.py` 将 `TermDecision` 与 `ParaTranzTerm` 投影成可比较 canonical content；明确 scope、case sensitivity、variants、pos/note 的比较规则，不把只读 remote metadata 加入业务内容 digest。
3. 实现 identity matching，先 baseline link、后 safe-match proposal；产出 unmatched/duplicate/reused diagnostics，而不是在主 planner 中靠 dict 最后写入消重。
4. 实现 base-relative classification。用小型纯函数分别判断 local state、remote state 和 pair state，unknown 优先级高于 changed/deleted。
5. 实现 backup action policy：只对 managed 或 local-only 项写远端，independent remote 全部 skip；delete 必须 managed、remote ID 一致且 profile 允许。
6. 实现 bidirectional policy：remote-only/changed/deleted 只产生入站 proposal；both changed 冲突，不自动接受本地/远端；local changed 仍可上传。
7. 在映射前应用 scope capability：非 Project 全局、suppressed/replacement 的不可表达语义生成 `LOSSY_MAPPING`，永不生成可执行 remote payload。
8. 组装 plan hash，包含 target/binding/profile/baseline/local/remote identity 和所有 item；plan ID 由 hash 派生。
9. 实现 planning use case：读取并验证 active Variant mapping、exact effective snapshot、stable remote snapshot 和 baseline；create-plan 无写入，authorize 时全部重读并比较。
10. 复用 `ConfirmationAuthority`：所有 update/delete/映射替换和 inbound suppression proposal 都要求结果导向确认；token 绑定 owner、mode、line、plan hash 并一次性消费。

## 文件变更清单

- **新增** `src/transbridge/application/terminology_sync/plan_models.py`、`mapping.py`、`planner.py`、`use_case.py`。
- **最小修改** `src/transbridge/application/terminology_sync/__init__.py`：导出稳定 public DTO。
- **新增** `tests/application/terminology_sync/test_mapping.py`、`test_planner.py`、`test_planning_use_case.py`。
- **新增/更新** `tests/contracts/terminology_sync/fixtures/`：由 S00 remote fixture 与本地 version/baseline 组合的 golden plan。
- **参考但不修改语义** `src/transbridge/application/sync/models.py`、`planner.py`、`use_case.py`：复用确定 hash/confirmation 模式。

## 边界条件与错误处理

- local snapshot 非 READY、version digest 不一致或 Project/Variant 不匹配：`LOCAL_VERSION_UNAVAILABLE/CORRUPT`，不生成空计划。
- remote snapshot unstable：`REMOTE_SNAPSHOT_UNSTABLE`，只能重新预检。
- baseline store unavailable：`SYNC_BASELINE_UNAVAILABLE`；只有确认查询成功且无 line 时才是 first sync。
- 同 target 的 active Variant 不同：`VARIANT_MAPPING_CONFLICT`，在任何 remote payload 生成前 blocked。
- plugin scope/不可表达 suppression：lossy item 仍进入 counts/分页，但 payload 必须为 `None`。
- independent remote 不因 backup 计划缺少 local term 而被删除；remote-only delete 仅在 baseline 证明 managed 时存在。
- authorize fresh-check 失败不得消费 confirmation token；恢复 freshness 后同 token仍可按 `ConfirmationAuthority` 规则使用一次。

## 测试策略与建议命令

- golden matrix：首次/已有 baseline、纯 echo、local/remote/both changed、双方/单方 delete、remote-only/local-only、unknown、ID 重用和重复。
- policy：backup 与 bidirectional 的 action/count/destructive 差异；所有 conflict 不自动偏好。
- scope：Project global、plugin、suppressed/replacement、variants/case/note 字段差异。
- determinism：输入乱序、多次运行、序列化 round-trip、plan/item ID/hash 完全一致。
- authorization：target/binding/profile/baseline/local/remote 任一变化 stale；owner/token/hash/过期/重放拒绝。
- side-effect spy：create-plan 不调用 remote write、sync-state write、DraftService 或 effective pointer mutation。
- 建议命令：`uv run pytest tests/application/terminology_sync/test_mapping.py tests/application/terminology_sync/test_planner.py tests/application/terminology_sync/test_planning_use_case.py -q`。

## 风险、回退与未决问题

- 最大风险是首次同词匹配建立错误 ownership；因此只生成 adopt-link proposal，不在 planner 内自动建立 baseline。
- 字段比较规则必须与 ADR-027 writable payload 一致，否则会产生永久伪差异；adapter contract 变化时需要提升 mapping algorithm/profile revision。
- 回退可停用 planning capability；纯模型和历史 baseline 保留，不转换回 translation-entry `SyncPlan`。

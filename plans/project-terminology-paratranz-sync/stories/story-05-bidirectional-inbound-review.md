# Story 05：双向同步入站 change set 与本地复核边界

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17.1、FR5.17.4、FR5.17.6；FR5.16 draft/publish
- **前置依赖**：[Story 03](story-03-three-way-sync-planner.md)、[Story 04](story-04-backup-execution-retry.md)
- **下游调用方**：S07 入站复核 UI/Agent/MCP、S08 发布前后隔离验证

## 目标

让 `BIDIRECTIONAL` 计划在执行上传的同时，把真实远端新增、修改和删除冻结为可追溯的入站 change set。用户随后通过独立预览/确认命令把选中项合并进当前 FR5.16 draft；同步本身、change set 保存和导入都不得改变 effective version或自动发布。

## 原始验收标准

- 双向 executor 可与上传同一运行完成，但所有 remote-only/remote-changed/remote-deleted 内容先保存为 immutable `InboundTerminologyChangeSet`，保留 remote identity、目标、远端修订/摘要、baseline 和来源计划。
- 入站 change set 不改变 effective pointer；“导入待处理内容”通过独立命令合并到 active draft 或创建新 draft，并使用 expected draft/version revision 防止覆盖并发人工修改。
- 远端新增生成 review-required candidate；远端修改与本地人工决定不同则生成可见冲突；远端删除生成 suppression/delete proposal，绝不删除历史 version/evidence。
- 用户接受/拒绝/改写入站项均留下 actor、时间、before/after digest 和 remote provenance；只有后续 FR5.16 publish 才影响新 AI 任务。
- 重复拉取同一远端状态复用已有 change set/item identity，不制造重复候选、冲突或人工 action。

## 当前 draft 边界

- `DraftService.open()` 只允许 base version/digest等于当前 `DraftLineState`，并拒绝同 Project/Variant 已有 active draft。
- `DraftService.save()` 通过 `DraftWriteExpectation` 校验 draft ID/revision/decision digest/Project Variant line，保存 revision必须正好 +1。
- `DecisionService.apply()` 负责可信 actor、时区时间、`ManualAction` 和 before/after digest；入站导入不得绕过它伪造人工决定。
- `ConflictService.resolve()` 是后续用户解决冲突的边界；同步 import 只创建 review/conflict事实，不自动调用 UNIFY/PREFER_REMOTE。
- `TerminologyVersion` 和 effective pointer由 publish事务维护；sync state repository不能直接写这些表。

## 入站数据流

```text
BIDIRECTIONAL plan + stable remote snapshot
          ↓
execute remote writes / freeze inbound items
          ↓
immutable InboundTerminologyChangeSet（effective 不变）
          ↓ explicit preview + selection + confirmation
DraftImportProposal @ expected DraftLineState/DraftWriteExpectation
          ↓
new or revised active draft + audited disposition/provenance
          ↓ later FR5.16 publish
new effective version
```

## 计划新增的关键接口

- `InboundChangeKind`：`REMOTE_ADD`、`REMOTE_UPDATE`、`REMOTE_DELETE`、`REMOTE_CONFLICT`。
- `InboundReviewStatus`：change set级 `PENDING/PARTIALLY_REVIEWED/REVIEWED/STALE`，item级 `PENDING/ACCEPTED/REJECTED/EDITED/CONFLICT`。
- `InboundTerminologyChange`：stable item ID、kind、remote term/ref/revision、base/local summaries、proposed effect、reason。
- `InboundTerminologyChangeSet`：immutable ref、line/target/plan/baseline/remote snapshot、items、content digest、created time。
- `DraftImportSelection`：change set ref/revision、selected item IDs、每项 accept/reject/edit payload、expected line和可选 draft expectation。
- `DraftImportProposal`：不会写入的预览，包含将新增/修改/抑制/冲突的数量、生成 decisions、diagnostics 和 proposal digest。
- `InboundDraftImportService.preview()/commit()`：创建/修改 draft并保存 review disposition；不 publish。

## 入站投影规则

- remote add：生成 Project 全局、`DecisionStatus.REVIEW_REQUIRED` 的 draft decision候选；stable local term ID按现有 `term_id()`规则生成，remote provenance保存在 sync表。
- remote update：若 active/base local值相同则可生成 update proposal；若本地人工值、scope、suppression或base已变化，生成 conflict，不覆盖。
- remote delete：只生成 suppression/delete proposal。接受时使用现有 `DecisionOperation.SUPPRESS` 语义追加 `ManualAction`；绝不删除 version/evidence row。
- remote conflict/有损 scope：保持 change item conflict，不生成可提交 payload，等待用户显式选择或编辑。
- rejected：只保存 disposition和actor/reason，不改 draft；相同 remote snapshot重拉不重新创建 pending item。

## 依赖有序的实施步骤

1. 在 `inbound.py` 定义 change/item/ref/status 和 canonical digest；从 S03 plan item + remote snapshot构造 immutable set，拒绝 unstable/blocked输入。
2. 扩展 S02 schema/codec/state port保存 change set header/items和 mutable review disposition；immutable内容与review状态分表，避免更新冻结事实。
3. 在双向 executor 中先按 S04规则执行上传，再在同一本地 sync-state transaction中保存 inbound set/outcomes。远端写partial不应丢弃可确定的 inbound事实，但 set需标记来源run partial。
4. 实现 idempotent set identity：line + baseline revision + remote snapshot digest + canonical inbound items；重复读取返回已有ref。
5. 在 `draft_import.py` 实现 preview：读取 current `DraftLineState`、active draft和 selected items，生成决定性 proposal；不调用 DraftService写入。
6. 无 active draft时，commit使用 `DraftService.open(OpenDraftCommand(...))` 创建base=current effective的空/初始draft，再合并 selected proposal。已有draft时使用 `DraftWriteExpectation.from_draft()`。
7. 对 add/edit采用 `DecisionService.apply()` 或新增批量 command service，批量实现也必须一次 trusted actor解析、逐项 `ManualAction` 和单个原子 draft revision提交；不能逐项产生可见半导入。
8. 对 remote delete调用 SUPPRESS proposal；对冲突只把 unresolved review事实关联 draft，不调用 `ConflictService.resolve()`。
9. commit前重读 change set review revision、DraftLineState、draft expectation和 current effective；任一变化返回 stale并保留原 proposal。
10. draft transaction成功后在同一受控 application workflow中保存 accepted/rejected/edited dispositions和 provenance link。跨库原子性不可得时先保证 draft不被重复应用，使用 proposal/action identity reconcile sync disposition。
11. 提供 list/get/page query供 S07 展示；查询只返回安全摘要和 remote identity，不把整份术语库加载到 UI。

## 文件变更清单

- **新增** `src/transbridge/application/terminology_sync/inbound.py`、`draft_import.py`。
- **更新** `src/transbridge/application/terminology_sync/models.py`、`ports.py`、`executor.py`。
- **更新** `src/transbridge/persistence/terminology/sync_state.py`、`sync_codec.py`、必要 schema tables/indexes。
- **可能新增** `src/transbridge/application/terminology/batch_decisions.py`：只有为保证多 item单 draft revision原子提交所需；不把远端语义写进 `DecisionService`。
- **新增** `tests/application/terminology_sync/test_inbound.py`、`test_draft_import.py`。
- **新增** `tests/integration/terminology_sync/test_inbound_publish_boundary.py`。

## 边界条件与错误处理

- change set的 target/line/baseline/remote digest与当前不符时可读但不可导入，状态为 stale；用户可重新规划，不覆盖旧审计。
- active draft基于旧 effective version时先使用现有 rebase proposal流程；同步 import不能自动 rebase。
- selected item在预览后被他人review、draft revision变化或 effective version切换：commit拒绝，confirmation不应被错误消费。
- remote add与现有 normalized original冲突时不生成第二个可自动采用的 decision；进入 conflict。
- remote delete对应本地已suppressed项是 idempotent skip；对应人工修改项必须 conflict/明确确认。
- draft成功但sync disposition写失败时，用 stable proposal/action identity reconcile，不能再次把相同decision/action追加到draft。
- 任何入站路径都不能调用 `publish_version`、更新 `effective_versions` 或直接删 versions/evidence。

## 测试策略与建议命令

- change set：add/update/delete/conflict、partial source run、重复 snapshot复用ref、分页和immutability。
- preview/commit：无draft、有draft、draft revision race、effective changed、needs rebase、accept/reject/edit混合、trusted actor和confirmation。
- audit：每个accepted/edit/suppress有稳定 ManualAction和remote provenance；rejected只记disposition。
- idempotency：重复commit、draft成功/disposition失败、重启恢复、相同remote状态不重复candidate/action。
- publish boundary：导入前后 effective snapshot identity不变；显式 publish后新version才包含改变。
- 建议命令：`uv run pytest tests/application/terminology_sync/test_inbound.py tests/application/terminology_sync/test_draft_import.py tests/integration/terminology_sync/test_inbound_publish_boundary.py -q`。

## 风险、回退与未决问题

- 批量导入需要单 draft revision原子性；若现有 `DecisionService.apply()` 逐项提交无法满足，必须新增批量 application service，而不是在 sync层直接写 repository。
- terminology DB内部可单事务，但 Project lifecycle revision来自另一权威；`DraftTransactionPort` 已负责在提交内校验完整 line state，必须继续复用。
- 回退时未review change set保留只读，禁用 import capability即可；不得把它们转换成 effective version或删除远端事实。

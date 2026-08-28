# Story 07：草稿、人工决定与冲突处理

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿

## 目标

为每条 Project/Variant 版本线维护唯一 mutable draft，用 expected revision 和追加式 `ManualAction` 保存人工语义；自动重建只能更新证据关联与复核状态，不能覆盖人工字段、抑制或历史。

## 原始验收标准

- [ ] 每条 `(project_id, variant_id)` 版本线最多一个 mutable draft；draft 绑定 base version/content digest 和 expected revision，自动保存不创建正式版本。
- [ ] 人工支持修改译名、添加术语、调整 scope/variant/备注、统一译名、插件特例、忽略冲突、抑制/重新启用；每项追加 `ManualAction`，固定非空 actor、前后值、原因、基准版本和 replacement/suppression 关系。
- [ ] 改原名创建新 `TermDecision` 并 replacement 旧项；删除表达为抑制，证据不删除。重建不会覆盖人工字段或恢复已抑制项。
- [ ] 新证据与人工决定冲突时保留人工当前值并标记新增待复核；证据消失时保留决定并标记“当前无证据/可能过期”。
- [ ] effective/base/Variant 变化时拒绝静默覆盖 draft，提供 rebase、以历史版建新稿或放弃；这些动作产生新的 draft identity 或不同 digest。

## 依赖与当前事实

- 依赖 S02 models/identity、S03 reconciliation、S04 draft/action transaction/query、S06 run/expected guard。
- 下游 S08 发布 materialization/diff，S09 manual report，S10 suppression/shadow projection，S11 command UI。
- 当前没有 terminology draft/decision/conflict 模块；旧 `DynamicTermDatabase.add_many_and_save()` 是原地 JSON，不可复用。
- `RequestContext.owner_id` 已非空，但未证明它等价于可审计的人类 actor；实现前需确认或通过显式 identity port 提供 actor。

## 关键接口与数据流

- `drafts.py`：计划新增 open/save/abandon/from-history/rebase commands 与 `DraftService`。
- `decisions.py`：`DecisionService`、`DecisionCommand`、`DecisionOperation`。
- `conflicts.py`：`ConflictService`、`ConflictResolutionCommand`、`ConflictReconciliation`。

```text
command + RequestContext + expected draft/base/effective/Variant
  -> validate
  -> one repository transaction
       mutate draft decision set
       append ManualAction
       advance revision/digest

automatic rebuild -> reconcile evidence/status only -> no ManualAction
```

## 实施步骤

1. 定义 command、typed errors 和每种操作允许的 before/after/scope/variant/note 字段；不把可选原因擅自改为强制产品要求。
2. 以 DB unique constraint 保证每条线最多一个 active draft，固定 base ID/content digest、Variant/effective、draft ID/revision/decision digest。
3. save 使用 expected revision，在单事务中修改 decision 并追加 action；actor 从可信 context 固化，时间来自 `ClockPort`。
4. 实现改译名、添加、scope/variant/note、统一译名、插件特例、忽略、suppression/reactivation；改原名原子创建 replacement 并 suppress 旧项。
5. evidence reconciliation 保留人工值；新矛盾为 needs-review，证据消失为 no-evidence/possibly-stale，证据恢复不自动解除 suppression。
6. effective/base/Variant mismatch 拒写并保留双方；提供 rebase proposal+commit、历史版建新稿和 abandon 三条显式路径。
7. 新 draft identity/base/decision digest 防止放弃后 revision 数值重复导致 cache hit；接入 publish/report queries 并验证完整 action audit。

## 文件与测试

计划新增 `application/terminology/{drafts,decisions,conflicts}.py` 和对应 tests；计划修改 S03 reducer/build 与 S04 schema/repository/queries。

建议命令：

```powershell
uv run pytest tests/application/terminology/test_drafts.py tests/application/terminology/test_decisions.py tests/application/terminology/test_conflicts.py tests/persistence/terminology -q
```

## 边界、风险与回退

- revision/base/effective/Variant 冲突不得 last-write-wins；返回双方内容供显式处理。
- 关闭或取消草稿不移动 effective pointer；证据永不因 suppression/replacement 删除。
- 自动证据更新不得借 actor 伪造人工操作；plugin special 必须显式 scope。
- rebase 只生成 proposal 并要求显式命令；回退时可放弃 mutable draft，published/effective 历史保持不变。

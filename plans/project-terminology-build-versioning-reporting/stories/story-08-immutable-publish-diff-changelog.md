# Story 08：不可变发布、规范差异与冻结更新日志文档

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿（待 ADR-034 接受后实施）

## 目标

在一个受 TaskRuntime 与业务 revision 双重保护的 SQLite 事务中，把草稿物化为不可变术语版本、规范差异和冻结更新日志文档，并在所有事实成功持久化后原子移动当前生效指针。回退也通过发布新版本完成，不改写或删除历史。

## 原始验收标准

- [ ] 发布在一个 SQLite transaction 内验证 build/draft/base/revisions/run permit，物化 proposed terms，计算 parent diff，冻结版本冲突/无证据/人工/诊断 projection，生成并持久化 `ChangeLogDocument`，最后移动 `(project, variant)` effective pointer。
- [ ] 首版对空库 diff；后续 typed changes 至少区分新增、抑制、译名修改、原名替换、scope/属性变化、冲突状态变化、重新启用和仅证据变化。
- [ ] diff、发布事实 projection、narrative document 或事务失败时旧 pointer 不变；stale 一律拒绝，partial 默认拒绝，显式 partial policy 保留为后续开关且必须结果导向确认。
- [ ] `ChangeNarrativeProjector` 确定性生成最终用户术语更新说明和维护者完整明细，固定 locale、template/schema version、message args 和 digest；不调用 LLM，不从导出文件或当前来源重算。
- [ ] 回退以历史内容为基础、以当前 effective 为 parent 发布新版本；不移动到旧 pointer 或删除中间历史。
- [ ] 发布事务成功后 Markdown/Excel artifact ledger 为 pending；外部导出失败不回滚版本，并可从同一 document ref 重试。

## 前置依赖与受影响调用方

- 依赖 S02 的 `TerminologyVersionRef`、`CanonicalDiff`、`ChangeLogDocumentRef` 和 typed change 合同。
- 依赖 S04 的单事务 repository、不可变 membership、effective pointer 和 artifact ledger。
- 依赖 S06 的 workload、run lease、`TaskRuntime.commit_permit()` 与取消屏障。
- 依赖 S07 的 draft、manual action、rebase 和 effective materialization。
- 下游 S09 只从本 Story 冻结的 document ref 渲染更新日志；S10 只读取已提交的 effective pointer；S11 只投影 use-case 结果。
- ADR-034 仍为“提议”；在其接受前不得把正式 publish 注册为默认能力。

## 当前实现事实

- `src/transbridge/application/tasks/runtime.py` 的 `TaskRuntime.commit_permit()` / `try_commit()` 能阻止取消或迟到 run 提交，但不验证 Project、Variant、source graph、effective、draft 或 build freshness。
- `src/transbridge/application/io/publish/guards.py` 的 `TaskRuntimeCommitGuard` 和 `ImmediateCommitGuard` 是文件发布提交屏障，不是术语版本事务。
- `src/transbridge/application/projects/lifecycle.py`、`application/projects/models.py` 已提供 active Project/Variant revision 和请求 actor 上下文；当前没有 terminology version、diff、document 或 effective pointer。
- 现有 I/O `PublishCoordinator` 可作为 staging/validate/publish 失败语义参考，但不得被复用为跨多表 SQLite 事务协调器。

## 数据流与关键接口

```text
PublishTerminologyRequest
  -> validate run permit + build/draft/base/current revisions
  -> VersionMaterializer.materialize(...)
  -> CanonicalDiffEngine.compare(parent, proposed)
  -> freeze conflict/no-evidence/manual/diagnostic projections
  -> ChangeNarrativeProjector.project(...)
  -> repository.publish_version_atomically(...)
       version + membership + diff + document + ledger(pending)
       -> effective pointer last
  -> commit
  -> schedule Markdown/Excel rendering from ChangeLogDocumentRef
```

计划新增的应用符号：

- `versions.py`：`VersionMaterializer`，负责从 base/draft/build 得到 proposed immutable term state。
- `diff.py`：`CanonicalDiffEngine`，负责稳定 typed rows、排序和 digest。
- `narrative.py`：`ChangeNarrativeProjector`、版本化 `NarrativeTemplate`。
- `publish.py`：`PublishTerminologyRequest`、`PublishTerminologyResult`、`VersionPublisher`。
- repository port：单一 `publish_version_atomically(...)` 操作；application 不得把版本、diff、document、pointer 拆成多次提交。

## 实施步骤

1. 固定 typed change 的 canonical 序列化、排序、digest 和首版 parent=空库语义；字段必须保留 Variant 线身份与人工来源标记。
2. 实现 proposed state 物化：改译名保留 `term_id`；改原名生成新 term，并把旧 term 表达为 suppression/replacement；禁止直接删除证据。
3. 实现 diff engine，至少产出 added、suppressed、translation changed、original replaced、scope/attributes changed、conflict status changed、reenabled、evidence only。
4. 以 diff 和同一发布输入冻结冲突、无证据、人工调整和诊断 projection；随后用确定性 projector 生成最终用户摘要与维护者明细。
5. 在 transaction 内重新读取并验证 project/variant/source/effective/base/draft/build 状态，同时验证 run permit；任一不匹配返回结构化冲突。
6. 原子写入 version membership、diff、document、ledger pending；把 effective pointer 更新放在事务末尾，任何异常整体 rollback。
7. commit 后分别调度 Markdown/Excel renderer；单个 renderer 失败仅 CAS 更新 ledger，不修改 version/document/pointer。
8. 实现“以历史版本为基础再次发布”：历史内容是 proposed base，parent 始终是当前 effective，由正常 diff/publish 路径生成新版本。

## 文件变更清单

计划新增：

- `src/transbridge/application/terminology/versions.py`
- `src/transbridge/application/terminology/diff.py`
- `src/transbridge/application/terminology/narrative.py`
- `src/transbridge/application/terminology/publish.py`
- `tests/application/terminology/test_diff.py`
- `tests/application/terminology/test_narrative.py`
- `tests/application/terminology/test_publish.py`
- `tests/persistence/terminology/test_publish_transaction.py`

计划修改：

- S02 定义的 `models.py` / `ports.py`（只补齐实现所需窄合同，不改变验收边界）。
- S04 定义的 SQLite schema/repository/artifact ledger。
- S06 定义的 workload 注册与 composition wiring。

## 边界条件与错误处理

- stale 结果无条件拒绝；partial 在首版默认拒绝，不能借内部参数绕过。
- run permit 是必要条件但不是业务 freshness 证明；两类 guard 必须同时通过。
- diff、事实 projection、document、版本 membership 或 pointer 任一步失败均 rollback，旧 pointer 保持不变。
- renderer 不得进入发布事务，也不得在失败时回滚已发布版本。
- document 生成不得读取当前文件、当前 draft 或导出文件，不调用 LLM，不把术语决定表述为游戏文本已修改。
- 版本、diff、manual action 和 document 不参与普通 cache/report GC。

## 测试策略

- 首版与相邻版本 diff；逐一覆盖所有 typed change 和 evidence-only。
- narrative golden：固定 locale/schema/template/message args/digest，最终用户文案不泄漏内部标识，维护明细与 typed rows 一一对应。
- 对 transaction 每个写入 seam 做 fault injection，验证旧 pointer 与历史不变。
- stale、partial、base/draft/effective 并发冲突和 run permit 失效矩阵。
- Variant A 发布不改变 Variant B；回退后完整版本链仍可浏览和比较。
- commit 后 Markdown 或 Excel 失败时版本保持成功，ledger 可从同一 document ref 重试。

建议命令：

```powershell
uv run pytest tests/application/terminology/test_diff.py tests/application/terminology/test_narrative.py tests/application/terminology/test_publish.py tests/persistence/terminology/test_publish_transaction.py -q
```

## 风险、回退与未决问题

- 长期持久化合同受 ADR-034 约束；ADR 未接受是组合启用门禁，不是把设计默认为已批准的理由。
- SQLite 事务与 TaskRuntime 屏障若分散到多个 coordinator，容易出现双提交或 pointer 先移；必须由单一 repository transaction API 收口。
- 回退代码时无法读取新版本则保持 SQLite 资产只读并使用 legacy fallback；不得倒写旧 Project schema 或删除新版本资产。
- partial publish 仍是后续显式 policy，当前 Story 只保留扩展点，不开放默认路径。

## 交接不变量

1. 历史版本、diff 和 `ChangeLogDocument` 一经提交不可变。
2. effective pointer 只能在同一事务全部事实成功后移动。
3. 回退发布新版本，不移动到旧指针或删除中间历史。
4. 更新日志 renderer 只消费冻结 document ref，不重建业务事实。

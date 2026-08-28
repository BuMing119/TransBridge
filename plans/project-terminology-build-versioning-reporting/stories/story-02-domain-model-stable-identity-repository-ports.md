# Story 02：术语领域模型、稳定身份与仓储端口基线

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿

## 目标

建立不依赖 PyQt、sqlite3、openpyxl 或具体 LLM client 的术语领域合同、稳定身份、查询/事务端口和内存仓储基线，为后续构建、版本、报告与 SQLite adapter 提供唯一语义。

## 原始验收标准

- [ ] 明确定义 `BilingualEvidence`、`TermCandidate`、`ConflictGroup`、`TermDecision`、`ManualAction`、`BuildResult/Ref`、`DraftRef`、`TerminologyVersion/Ref`、`CanonicalDiff`、`TerminologyReportSnapshot/Ref`、`ChangeLogDocument/Ref` 和 artifact ledger 合同。
- [ ] `evidence_id/candidate_id/term_id/conflict_group_id/build_key` 使用带 schema namespace 的 canonical serialization；排除时间戳、UI 顺序、路径临时名和 run ID，并对摘要碰撞做内容复核。
- [ ] `term_id` 保留 Project/Variant 线和作用域身份；改译名不改 ID，改原名产生 replacement；draft cache identity 同时比较 draft ID、base/content digest、revision 和 decision-set digest。
- [ ] 模型校验禁止空 actor 冒充人工操作、禁止 unresolved/suppressed 项进入 effective projection、禁止 stale BuildResult 发布。
- [ ] 内存 repository 实现和 SQLite 端口共享合同测试，能够证明不可变对象、expected revision、分页 cursor 绑定和版本指针语义。

## 当前实现事实

- ADR-027 `TermEntry` 实际位于 `src/transbridge/ai_translator/term_formats.py`，只负责匹配与格式交换，不能承载证据、冲突、草稿或版本。
- `EntryKey`、`SourceNamespace` 与 `VariantSnapshot` 提供现有稳定身份和不可变 snapshot 先例。
- `TermDatabaseManager.resolve_term()` 有基础规范化，但最终是无 Project/Variant/scope 的平面 last-write-wins。
- 当前不存在 `application/terminology/`、terminology repository、分页 cursor 或 `CURSOR_STALE`。
- expected revision 可参考 `VariantAggregate.commit()` 与 `ProjectRepository.save_if_revision()`。

## 模型、身份与端口

- `models.py`：验收标准中的模型/ref，并定义 `TermScope`、质量状态、冲突/人工操作/typed change/artifact enums。
- `identity.py`：versioned canonical bytes/digest 与 evidence/candidate/term/conflict/build 身份函数；摘要碰撞必须复核 canonical payload。
- `errors.py`：revision conflict、digest collision、stale build、`CursorStaleError(code="CURSOR_STALE")`。
- `ports.py`：`TerminologyRepositoryPort`、`TerminologyQueryPort`、`PageRequest`、`Page`、`SnapshotCursor`、`ClockPort`。
- `in_memory.py`：`InMemoryTerminologyRepository`，与 S04 SQLite adapter 运行同一 contract suite。

## 实施步骤

1. 固定 normalization schema/version、canonical field order 与 JSON 编码；原名 NFKC+空白+casefold，译名 NFKC+空白，不删除标点。
2. 定义 line/scope/ref、证据、候选、冲突、决定和人工 action；集合字段 tuple 化并稳定排序。
3. 定义 build/draft/version/diff/report/changelog/artifact 模型与交叉状态校验，人工 action actor 必须非空。
4. 实现所有稳定 ID 和 build key；排除时间、run ID、扫描顺序、临时路径与数据库自增 ID。
5. 定义 summary/term/conflict/manual/evidence/history/compare 查询；cursor 绑定 snapshot digest、query fingerprint、sort values 和 stable ID。
6. 定义 expected revision、唯一 active draft、draft identity/base/decision digest、immutable version 与 effective pointer 语义。
7. 实现内存 repository，返回不可变快照且不泄漏内部 dict/list。
8. 建立 adapter-neutral contract suite，S04 必须原样复用，而非为 SQLite 另写语义。

## 文件与测试

计划新增：

- `src/transbridge/application/terminology/{__init__,models,identity,errors,ports,in_memory}.py`
- `tests/application/terminology/test_models.py`
- `tests/application/terminology/test_identity.py`
- `tests/contracts/terminology/test_repository_contract.py`

建议命令：

```powershell
uv run pytest tests/application/terminology tests/contracts/terminology -q
```

## 边界、风险与未决问题

- `BuildInputSnapshot` 字段以 S01 为准，S02 不定义平行 DTO。
- digest 碰撞测试通过可注入 hash provider 完成，不能依赖制造真实碰撞。
- `effective.py` 边界建立前，领域模型不得导入 `TermEntry`。
- SQLite schema、物理索引与事务实现属于 S04；内存 adapter 不预埋 SQL 假设。

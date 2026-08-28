# Story 09：统一质量报告、Markdown/Excel 更新日志与 artifact ledger

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿

## 目标

以冻结的 `TerminologyReportSnapshotRef` 和 `ChangeLogDocumentRef` 分别作为质量报告与版本更新日志的唯一事实源，提供应用内分页预览、四表质量 Excel、Markdown/Excel 更新日志及可重试 artifact ledger；所有 renderer 只负责布局和编码。

## 原始验收标准

- [ ] `TerminologyReportSnapshot` 由 `BuildResultRef + pinned draft/no-draft identity/base/digest/revision` 冻结；构建后人工调整产生新 snapshot，不回写旧 BuildResult。
- [ ] UI preview 与质量 Excel 读取同一 snapshot ref；Excel 固定“构建摘要”“术语对照”“同名异译”“人工调整记录”四表，零数据仍有完整表头。
- [ ] 大表采用 write-only/流式分页，超过 Excel 行容量确定性拆表/分卷并在摘要记录清单，不静默截断；所有用户字符串做公式注入防护。
- [ ] Markdown 与 Excel 更新日志只读取同一 `ChangeLogDocumentRef`，都包含最终用户摘要和完整维护明细；布局可不同但 typed facts、计数和 narrative message 一致。
- [ ] 默认不覆盖同名用户文件；ledger 保存 document/snapshot digest、renderer/version、目标、状态、诊断和重试次数。质量报告失败不改变 BuildResult，更新日志失败不改变 version/document。
- [ ] 相同 document、格式和 renderer version 的重建内容可验证一致；版本仍存在时对应日志不被普通报告清理。

## 前置依赖与受影响调用方

- 依赖 S04 的 snapshot-bound 分页 repository、artifact ledger、路径 guard 和事务状态。
- 依赖 S07 的 pinned draft/no-draft identity 与 manual action projection。
- 依赖 S08 的不可变 `CanonicalDiff` 与 `ChangeLogDocumentRef`。
- S11 的 UI preview 必须调用本 Story 的 report query service，不能直接查询 SQLite 或自行统计。
- S12 将基于本 Story renderer 做 5 万术语/5 千冲突与 5 万项 changelog 性能门禁。

## 当前实现事实

- `src/transbridge/application/translation/postprocess.py` 的 `ReportSnapshot` 已证明“先冻结、后渲染”模式，但模型只属于翻译后处理报告。
- `src/transbridge/application/translation/postprocess_report.py` 的 `ReportRendererPort`、`ReportRenderResult`、`ExcelReportRenderer` 与 `_spreadsheet_value()` 可作为错误隔离和公式注入防护参考。
- 现有 Excel renderer 不是 write-only；`_artifact()` 直接生成文件，`render_report_bundle()` 的轮转策略会保留最近 20 份，不能用于与版本同生命周期的 changelog。
- 当前没有 terminology report manifest、分页 section store 或 artifact ledger。

## 数据流与关键接口

```text
BuildResultRef + pinned draft/no-draft identity
  -> TerminologyReportSnapshotFactory.freeze()
  -> TerminologyReportSnapshotRef
       -> report query service -> UI preview pages
       -> QualityExcelRenderer -> quality artifact ledger

ChangeLogDocumentRef
  -> ChangeLogMarkdownRenderer -> changelog ledger row
  -> ChangeLogExcelRenderer    -> changelog ledger row
```

计划新增：

- `reports.py`：`TerminologyReportSnapshotFactory`、`TerminologyReportService`。
- `report_queries.py`：绑定 snapshot digest 的 summary/terms/conflicts/manual 分页查询。
- `persistence/terminology/report_snapshot.py`：immutable manifest 与分页区段存储。
- `renderers/quality_excel.py`、`changelog_markdown.py`、`changelog_excel.py`、`spreadsheet_safety.py`。
- artifact ledger 以 `(fact_ref, format, renderer_version, target)` 为受 revision 保护的运行状态，不反写 immutable facts。

## 实施步骤

1. 用 `BuildResultRef + draft identity/base/content digest/revision/decision-set digest` 冻结 snapshot；无草稿必须写入显式 no-draft sentinel。
2. 固化 snapshot manifest、summary 和各分页 section 的 digest；所有 UI/renderer 查询带同一 snapshot ref，cursor stale 语义沿用 S04。
3. 实现质量 Excel 的 write-only 输出，始终创建“构建摘要”“术语对照”“同名异译”“人工调整记录”四表并写表头。
4. 在写单元格前统一处理公式前缀、非法 Unicode、Excel 32767 字符上限和受支持标量类型；不可静默丢值。
5. 达到单 sheet/workbook 容量时按稳定行顺序拆 sheet 或分卷，并在构建摘要记录卷/表清单和范围。
6. 实现两个 changelog renderer；两者只读同一 document ref，最终用户摘要、typed row、计数和 message args 语义相同。
7. 文件先写 staging，再按 `fail-if-exists | rename | overwrite` 显式策略发布；默认 `fail-if-exists`。
8. ledger 用 expected revision/CAS 流转 pending、rendering、succeeded、failed，并记录 digest、renderer version、target、diagnostic、retry count。
9. 为相同 fact、格式和 renderer 版本生成稳定 manifest/hash；重试不重新扫描来源或读取当前 draft。

## 文件变更清单

计划新增：

- `src/transbridge/application/terminology/reports.py`
- `src/transbridge/application/terminology/report_queries.py`
- `src/transbridge/application/terminology/renderers/__init__.py`
- `src/transbridge/application/terminology/renderers/quality_excel.py`
- `src/transbridge/application/terminology/renderers/changelog_markdown.py`
- `src/transbridge/application/terminology/renderers/changelog_excel.py`
- `src/transbridge/application/terminology/renderers/spreadsheet_safety.py`
- `src/transbridge/persistence/terminology/report_snapshot.py`
- `tests/application/terminology/test_reports.py`
- `tests/contracts/terminology/test_renderer_parity.py`
- `tests/persistence/terminology/test_report_snapshot.py`
- `tests/persistence/terminology/test_artifacts.py`

计划修改：

- S04 的 `artifacts.py`、repository queries 与 schema。
- S06 的 report/changelog workloads 与 composition wiring。

明确不扩张 `application/translation/postprocess_report.py`；新域只抽取可证明通用且不会改变旧行为的窄 spreadsheet safety helper。

## 边界条件与错误处理

- 空结果仍生成四表与完整表头；“零数据”不是缺表条件。
- 超限必须拆分或返回可操作错误，不得截断行、列或证据引用。
- quality render 失败不改变 `BuildResult` / snapshot；changelog render 失败不改变 version/document。
- 文件已成功而 ledger 更新失败时，恢复流程应按目标文件 digest 对账，不盲目覆盖或重复生成。
- 版本日志不得使用现有普通报告轮转/GC；版本存在时 document 与其 ledger 必须保留。
- renderer 不得重算冲突、diff、narrative 或人工分类。

## 测试策略

- 四表 schema、顺序、空表表头和 snapshot/UI/Excel parity。
- 公式前缀（`= + - @`）、非法 Unicode、超长单元格和非字符串标量。
- Excel 行容量边界、稳定拆表/分卷、摘要清单和无丢行验证。
- 目标已存在、rename、显式 overwrite、staging 写失败、磁盘不足和 ledger CAS 冲突。
- 发布后修改或删除当前来源/draft，再从旧 document ref 重建，语义 manifest 保持一致。
- Markdown/Excel 对比稳定语义 manifest，而不是比较布局或二进制文件字节。
- 5 万术语/5 千冲突规模标记 `slow`，验证 renderer 使用分页且不会把全部 rows 常驻内存。

建议命令：

```powershell
uv run pytest tests/application/terminology/test_reports.py tests/contracts/terminology/test_renderer_parity.py tests/persistence/terminology/test_report_snapshot.py tests/persistence/terminology/test_artifacts.py -q
uv run pytest tests/contracts/terminology/test_renderer_parity.py -m slow -q
```

## 风险、回退与未决问题

- openpyxl write-only 对样式、公式和 sheet 生命周期有限制；先以固定 schema 和流式正确性为主，视觉增强不得破坏内存预算。
- “文件写成功、ledger 更新失败”是跨边界恢复风险，必须用 digest 与幂等 retry 对账。
- 报告渲染失败或被取消只停止本次新渲染；冻结 snapshot、version、document 和既有 ledger 保持可读。
- Excel 超长单元格的确定性切分/诊断细节需在实现时固化为 renderer contract，但不得改变业务内容或静默截断。

## 交接不变量

1. UI 与质量 Excel 读取同一 report snapshot ref。
2. Markdown 与 Excel 更新日志读取同一 changelog document ref。
3. renderer 只做布局/编码，不拥有统计、diff 或 narrative 规则。
4. 外部 artifact 失败只影响 ledger，不回滚业务事实。

# Story 10：EffectiveTerminologyPort 与现有匹配器迁移

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿（默认消费路径受 ADR-034 接受门禁约束）

## 目标

以带 Project/Variant/plugin scope 的窄端口把当前已发布术语版本投影为 ADR-027 `TermEntry`，并渐进迁移翻译、后处理和向量索引调用方；无项目版本时完整保留 legacy 优先级。

## 原始验收标准

- [ ] `snapshot(project, variant, version|current)` 和 `resolve(term, TerminologyLookupContext)` 只返回已采用/人工确认且作用域适用的 ADR-027 `TermEntry` projection。
- [ ] unresolved、待复核和 suppressed 不进入强制匹配；suppression/shadow decision 会阻止 legacy fallback 在同一作用域重新引入该词。
- [ ] 插件特例只在 matching plugin context 覆盖项目全局项；旧 `load_all()/resolve_term()` 继续只返回 legacy 或全局兼容项，不泄漏插件特例。
- [ ] 无 effective version 时现有 dynamic/ParaTranz/JSON/CSV/Excel 优先级保持；存在项目版本时项目版本只在项目和作用域内为最高优先级，未覆盖项可 fallback。
- [ ] 翻译、后处理和向量索引的 context-aware 调用方迁移后通过合同测试；不反写或同步 ParaTranz。

## 当前实现事实

- ADR-027 `TermEntry` 位于 `src/transbridge/ai_translator/term_formats.py`。
- `TermDatabaseManager` 已超过规模门禁，通过 `_load_all_with_metadata()` 和 dict last-write-wins 合并 dynamic/ParaTranz/JSON/CSV/Excel；公开 API 都没有 Project/Variant/plugin lookup context。
- `TermDatabaseManager.project_id: int | None` 当前表示 ParaTranz 远端 ID，不能与新本地 Project string identity 混用。
- 调用方包括 `translator.py`、proofread/postprocess classes、`ConsistencyChecker`、smart assistant translation/postprocess tools、mixed/polish UI runtime 和 `TermVectorIndex`。

## 关键接口与解析顺序

- `application/terminology/effective.py` / `ports.py`：计划新增 `TerminologyLookupContext`、`EffectiveTerminologySnapshot`、`EffectiveTerminologyPort`。
- `ai_translator/project_terminology_adapter.py`：计划新增 `ProjectTerminologyAdapter`。
- `TermDatabaseManager` 只增加窄 `effective_loader` 注入与 context-aware resolve/match 委托；旧 API 保持 legacy/global-only。

```text
resolve(term, context)
  -> matching plugin special
  -> project-global decision
  -> scoped suppression/shadow? stop
  -> legacy fallback
```

## 实施步骤

1. 从 immutable version membership 投影 adopted/manual-confirmed `TermEntry` 与 suppression/shadow 索引；unresolved/review/suppressed 不进入 entries。
2. 实现 plugin special > project-global > legacy 的解析顺序；相同 scope 的 suppression/shadow 必须阻断 fallback。
3. 无 version 返回明确 no-project-version，让旧优先级完全接管；损坏 version 返回只读诊断，不伪装空版本。
4. 将 adapter 注入 `TermDatabaseManager`，禁止 manager 读取项目 SQLite 或路径；避免继续扩张其解析职责。
5. 先迁移 `translator.py` 与 proofread/postprocess resolver，使每个 `TranslationEntry` 携带明确 source/plugin context。
6. 修改 `ConsistencyChecker`，不再把 merged cache 当项目上下文事实；逐步迁移 smart assistant/UI 调用方。
7. 向量索引以 `(project, variant, version, scope/content digest)` 建 snapshot；插件特例不得进入无 context 全局索引。
8. composition 注入 project loader；只有该 Project/Variant 首次成功发布后才启用，否则保持 legacy fallback。

## 文件与测试

计划新增 `effective.py`、`project_terminology_adapter.py` 与 effective port/adapter tests；计划修改 `term_database.py` 和明确的 translation/postprocess/vector call sites。

建议命令：

```powershell
uv run pytest tests/application/terminology/test_effective.py tests/contracts/terminology/test_effective_port.py tests/ai_translator/test_project_terminology_adapter.py tests/ai_translator/test_term_database.py tests/ai_translator/test_translator_term_conflicts.py -q
```

覆盖 global/plugin precedence、suppression/shadow、unresolved、无版本 legacy parity、损坏版本、Variant 切换、翻译/后处理/向量同 context parity，以及旧无 context API 不泄漏插件项。

## 边界、风险与回退

- 最大兼容风险是 context 丢失导致插件特例泄漏；context 不完整时宁可退回 global/legacy，也不能猜 scope。
- 本地 Project identity 与 ParaTranz remote project ID 必须使用不同字段/类型。
- suppression 不能再用平面 dict last-write-wins 表达。
- 回退只关闭 effective-loader gate；旧来源优先级不变，项目 SQLite 只读保留且不反写 ParaTranz。

# 修复强制术语的片段误匹配与重叠冲突

- 日期：2026-10-03
- Epic/Story：proofread-terminology-closure / S02、S03
- 关联：[计划](../../../../plans/proofread-terminology-closure/plan.md)

## 证据与原因

用户运行结果有 3290 条失败，其中 3279 条为术语修复仍不一致。只读检查本地该次请求日志确认：Aetherial 命中 Ria，Morvic 命中 Mor，Translated 命中 Ed；完整物品名与内部短词同时成为强制要求。原因是 `match_terms_for_entry` 直接调用用于参考召回的宽松 `match_terms`，包含任意子串和反向前后缀召回。

## 变更

- 新增 `src/transbridge/ai_translator/required_term_matching.py`：以完整拉丁词边界匹配，支持显式变体和大小写设置；按命中跨度优先最长非重叠术语，短词在别处独立出现时仍保留。不做反向召回或隐式冠词改写。
- 修改 `src/transbridge/ai_translator/term_database.py`：逐条强制术语入口委托新匹配器，继续使用原有项目/插件作用域的 effective matcher map；原 `match_terms` 参考检索保持原行为。该既有大模块仅作入口委托，新增匹配职责独立在新文件。
- 新增 `tests/ai_translator/test_required_term_matching.py`：覆盖日志中的三个误匹配、长短词重叠、独立重复短词、变体、大小写、非拉丁文本、空译名以及宽松检索与强制匹配的区别。
- 更新 `docs/changelogs/INDEX.md`：增加本记录链接。

## 验证

```powershell
uv run pytest tests/ai_translator/test_required_term_matching.py tests/ai_translator/test_term_database.py tests/ai_translator/test_project_terminology_adapter.py tests/integration/terminology/test_published_translation_consumption.py tests/ai_translator/post_processor tests/application/translation/test_proofread_stage.py tests/application/translation/test_proofread_terminology_closure.py tests/application/translation/test_terminology_refinement_concurrency.py -q
```

231 passed，3 条既有弃用警告。`uv run ruff check src tests`、`uv run ruff format --check src tests` 和 `git diff --check` 通过。

## 限制

未重新调用真实 LLM 或重跑用户的完整数据，不能把全部 3279 条归为误判。超长内容、保护标记和模型调用失败仍按既有规则处理。已结束任务不会自动重判，需要新代码重新运行。未改写用户日志、术语库、译文或整体提交策略；无需数据迁移，未打包发布。

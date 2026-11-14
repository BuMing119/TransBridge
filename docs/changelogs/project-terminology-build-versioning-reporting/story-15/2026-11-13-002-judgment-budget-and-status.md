# S15：修正冲突判断预算与未执行状态

- 日期：2026-11-13
- Epic / Story：project-terminology-build-versioning-reporting / S15
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)

## 根因与修复

原判断把 max_tokens_per_batch（默认 2000，业务正文预算）误当成包含提示词、schema 的请求总预算。兼容模型名无法匹配 tokenizer 时使用保守字节估算，再加完整长证据，导致请求前被整组跳过。跳过又被显示为“LLM 判断：无法确定”，误导用户以为模型已分析。

- 新增 `application/terminology/judgment_batches.py`：沿用业务正文预算；必要时取术语附近上下文并明确标记摘录，按预算分批，每批保留候选对照，每条选中的少数证据都参与。完整证据仍留在构建和导出中。最小对照仍不能容纳时明确报告估算 token 数与预算，不伪造模型结论。
- 修改 `infra/terminology_conflict_judge.py`：每批实际调用并验证；只有所有批均支持同一个代表译名才自动归并，冲突/不确定或批间不一致保留处理。提示要求指出具体词句和译名，不泛泛免责。
- 修改 `application/terminology/conflict_judgment.py`、`conflict_judgment_projection.py`：区分 completed/not_run/failed，记录真实请求次数；未执行和失败不计为模型已判断或模型无法确定。
- 修改 `bootstrap/terminology_conflict_judgment.py` 初始化失败状态，以及 `ui/tools/terminology/build_projection.py` 的请求数、未执行和失败统计。
- 更新模型适配与构建测试：默认 2000 预算、未知模型名、长中英文本仍发请求；少数证据记录覆盖；极小预算零请求；未执行文案与统计不冒充 LLM 输出。

## 验证

```powershell
uv run --no-sync --cache-dir .tmp-judgment-budget/uv pytest tests/infra/test_terminology_conflict_judge.py tests/application/terminology/test_judged_build.py tests/ui/tools/terminology/test_conflict_judgment_presentation.py tests/integration/bootstrap/test_terminology_conflict_judgment.py tests/integration/bootstrap/test_terminology_llm_build.py -q --basetemp .tmp-judgment-budget/final --tb=short
uv run --no-sync --cache-dir .tmp-judgment-budget/uv pytest tests/application/terminology/test_judged_build.py -q --basetemp .tmp-judgment-budget/status --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

联合 39 项通过，追加状态回归后构建 6 项通过；全仓 Ruff 与格式检查通过。未请求真实付费模型、未运行全仓 pytest、未打包发布。旧结果需要重新提取/导出才能更新判断；抽取缓存仍可复用。临时测试目录完成后清理。

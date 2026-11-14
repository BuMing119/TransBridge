# S13：术语只复核差异

- 日期：2026-11-13
- Epic / Story：`project-terminology-build-versioning-reporting` / S13
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)
- 前一增量：[LLM 接入](2026-11-13-001-project-llm-extraction.md)

## 行为与动机

按用户明确要求，取消所有新 LLM 术语一律待复核的策略。通过证据校验且没有异译、没有与已有决定冲突的新候选默认采用；只有存在差异的项需要处理。默认采用只写入草稿，仍需统一发布才对后续翻译生效。

重新提取时，旧草稿中未经人工调整、未禁用、有原证据且当前所有 LLM 候选译名一致的自动待复核项可以自动采用。人工操作、未解决异译、证据消失和实际译名差异不会被此规则清除。

## 文件变更

- 修改 `src/transbridge/application/terminology/build_drafts.py`：`decisions_from_build` 对无冲突候选统一采用；`BuildDraftCoordinator` 在当前证据下重新评估未人工处理的自动复核项，保留差异和人工保护。
- 修改 `src/transbridge/ui/tools/terminology/extraction_options.py`、`build_projection.py`：说明无差异项默认采用，只需处理异译与已有决定不一致的项。
- 修改 `tests/application/terminology/test_build_drafts.py`：新增无差异/差异、双译冲突、旧稿恢复以及人工/禁用/无证据保护回归。
- 修改 `tests/integration/bootstrap/test_terminology_llm_build.py`：验证生产 LLM 候选无需逐条确认即可发布，更新真实 SDK 离线请求的状态预期。
- 修改 `tests/ui/tools/terminology/test_llm_extraction_workflow.py`：验证默认采用的用户提示。
- 更新 S13 计划与索引中的当前策略；前一增量保持历史原文。

## 验证

使用现有 uv 环境，`$qa` 为本次独立临时目录，已在结束时清理。

```powershell
uv run --no-sync --cache-dir "$qa/uv" pytest tests/application/terminology/test_build_drafts.py tests/application/terminology/test_conflicts.py tests/application/terminology/test_decisions.py tests/application/terminology/test_publish.py tests/persistence/terminology/test_draft_transactions.py tests/persistence/terminology/test_publish_transaction.py tests/integration/bootstrap/test_terminology_build_review.py tests/integration/bootstrap/test_terminology_presenter_build.py tests/integration/bootstrap/test_terminology_llm_build.py tests/ui/tools/terminology/test_llm_extraction_workflow.py tests/ui/tools/terminology/test_workflow_guidance.py -q --basetemp "$qa/regression" --tb=short
uv run --no-sync --cache-dir "$qa/uv" pytest tests/ui/tools/terminology/test_workflow_guidance.py -q --basetemp "$qa/ui" --tb=short
uv run --no-sync --cache-dir "$qa/uv" ruff check src tests
uv run --no-sync --cache-dir "$qa/uv" ruff format --check src tests
git diff --check
```

首轮 89 项通过，16 项窗口测试被沙箱 `os.link` 权限阻止；提升权限后这 16 项全部通过，共 **105 个不同用例通过**。全仓 Ruff check、2558 文件 format check、diff check 通过。未重跑前一增量的全部 725 项，因为本次只修改候选采用和复核策略，相关草稿、发布、持久化、生产接线与界面回归已覆盖。未请求真实付费模型，未打包发布。

## 兼容

无需数据库迁移；已生成的旧待复核草稿可通过重新提取应用新策略，成功批次仍可复用。已有发布版本和人工决定保持保护。

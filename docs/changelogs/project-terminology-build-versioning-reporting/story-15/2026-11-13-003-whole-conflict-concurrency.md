# S15：整组冲突判断与 50 路并发

- 日期：2026-11-13
- Epic / Story：project-terminology-build-versioning-reporting / S15
- 计划：[项目术语计划](../../../../plans/project-terminology-build-versioning-reporting/plan.md)
- 前序：[预算与执行状态修复](2026-11-13-002-judgment-budget-and-status.md)

## 目的与范围

用户明确要求同一个术语的证据放在同一请求中，不受业务合批预算限制；不同冲突并发处理，上限 50，尽量复用已有设施。此前按 2000 token 拆组且串行请求造成重复对照和较长等待。本记录取代前序记录中的分批、摘录行为描述，历史记录保持不变。

范围仅为本轮冲突判断调整。相关生产文件与测试在 Git 中仍属前序任务新增、未跟踪文件；通过当前文件内容、前序增量及本次执行上下文确认归属，不将工作区其他修改归入本次。

## 文件与符号变化

- 修改 `src/transbridge/infra/terminology_conflict_judge.py`：`judge` 使用标准库 ThreadPoolExecutor / FIRST_COMPLETED，有界调度至多 50 个独立冲突；按完成数量报告进度，返回结果保持输入顺序。新增 `_judge_one`，每组完整 payload 一次逻辑 chat 调用，沿用结构化输出校验与单组错误隔离。移除组内分批及 `_combine`，提示词同步为整组判断。构造参数 max_input_tokens/token_counter 仍保留兼容，但不再用于拆组；输出长度配置仍生效。
- 修改 `src/transbridge/bootstrap/terminology_conflict_judgment.py`：生产 LimitedLLMClient 使用 AiRequestBudget(CONFLICT_MAX_CONCURRENT)，与线程池共享 50 的常量；沿用既有取消监视线程、客户端关闭和模型配置。此上限独立于抽取阶段 max_concurrent。
- 删除 `src/transbridge/application/terminology/judgment_batches.py`：移除本会话此前新增但未提交的组内分批适配模块，没有迁移目标。中间曾改用 StableContentBatcher 保留超预算单条；最终按用户整组请求要求取消该步骤。
- 修改 `tests/infra/test_terminology_conflict_judge.py`：完整长文本与少数证据覆盖断言，默认预算下整组只调用一次；缺失证据不冒充模型结论；新增 55 组并发测试，验证峰值 50、稳定结果顺序、逐组一次请求与连续进度。
- 修改 `plans/project-terminology-build-versioning-reporting/plan.md`：S15 约束改为整组请求和固定并发上限。

取证规则未改变：少数译名全部去重记录，多数译名最多 3 条代表记录；“整组”指这些选定证据，不意味着发送多数译名的所有记录。未改变人工决定优先、抽取缓存复用后仍判断、待处理与发布规则。

## 实际验证

以下命令在实现阶段执行，本次归档没有重跑业务测试：

```powershell
uv run --no-sync --cache-dir .tmp-judge-parallel/uv pytest tests/infra/test_terminology_conflict_judge.py tests/application/terminology/test_judged_build.py -q --basetemp .tmp-judge-parallel/tests --tb=short
uv run --no-sync --cache-dir .tmp-judge-parallel/uv pytest tests/integration/bootstrap/test_terminology_conflict_judgment.py -q --basetemp .tmp-judge-parallel/integration --tb=short
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

结果：27 项聚焦测试、3 项生产接线集成测试通过；后者覆盖缓存命中仍判断、失败重试与取消保留原构建。全仓 Ruff、格式检查与差异检查通过。测试临时目录已核实路径并清理。

## 遗留与兼容

- 未调用真实模型、未做实际 API 并发压测、未运行全仓 pytest、未打包发布。
- 业务合批预算不再限制冲突请求；服务端真实上下文限制与限流仍适用，失败保留待处理。50 是逻辑请求并发上限，客户端内部重试不等于新增冲突判断。
- 取消依赖已有客户端中断能力；线程池退出仍等待在途调用结束。
- 旧结果不会自动改写；重启更新后的程序重新生成草稿，可复用抽取缓存并执行新判断。

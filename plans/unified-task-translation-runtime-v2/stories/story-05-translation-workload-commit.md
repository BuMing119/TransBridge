# Story 05：AI 翻译 Workload、候选缓冲与唯一提交

- 所属 Plan：[Unified Task and Translation Runtime V2](../plan.md)
- 状态：已确认（2026-08-18）
- 追溯：FR21.4～21.6、NFR2.1；ADR-019；R-033/R-036
- 依赖：S02～S04、I/O ChangeSet

## 目标与验收

真实 mixed 成功链可执行；workload 只产候选；失败批次汇总 partial/failed；取消候选不覆盖正式集合；checkpoint 恢复不重复副作用/提交。

## 数据流与接口

RunSpec+ActionPlan → TaskRuntime workload → bounded batches → LLM Port → `CandidateTranslation(entry_key, before_revision, text, diagnostics, provenance)` → CandidateSet/checkpoint → final validation → 单一 `CommitTranslations` 生成 ChangeSet → CollectionMutationPort。报告读取 candidate/outcome，不读取临时 UI state。

## 实施步骤

1. 适配 AutoTranslator/MixedWorker 构造为 TranslationWorkload，移除 `_update_collection` 直接写路径。
2. LLM 调用前后检查 cancellation；并发由 runtime semaphore/backpressure 控制。
3. 批次重试仅限可重试错误，有界且记录 attempt；解析异常保留 raw response 摘要。
4. checkpoint 保存完成 batch/candidate idempotency key；恢复跳过已接受结果。
5. commit 校验 run_id、terminal、Entry revision、StagePolicy；冲突进入 partial，不盲覆盖用户编辑。

## 测试、边界与迁移

受控 HTTP server 覆盖成功、429、timeout、畸形响应、单批失败、取消和重放；真实 UI/Agent 构造链不允许 mock-only。审计 Collection mutation 次数，取消后为零、重复恢复只提交一次；同运行用户编辑触发 revision conflict。旧 worker 可包装 workload，但不再写正式集合。

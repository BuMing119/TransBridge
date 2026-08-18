# Story 03：性能、取消、恢复与长期稳定性门禁

- 所属 Plan：[Quality Foundation and Release Hardening V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：NFR1.1～1.3；R-049
- 依赖：S01；I/O、Task、Persistence、FOMOD 对应实现

## 验收记录

- 新增 `tests/performance/`（benchmark_cases.py / measure.py / test_performance_gates.py），12 条门禁 pytest 全绿（约 10s 整套）。
- Evidence run_id `qa-20260818T114219.894166Z-1051f99b5747`，`verify_evidence.py` 复验 `VALID ... (passed)`。
- Ruff 0 错误、format 通过；未 commit/push；未改 4 个全局索引。
- 本机小样本为早期反馈，全部预算项余量充足且边界不越；真实 Windows 硬件最终证据由 S05 给出。
- 详见 `docs/changelogs/release-hardening-v2/story-03-performance-stability/2026-08-18-001-性能取消恢复长期稳定门禁.md`。

## 目标与验收

按固定 corpus/硬件档位报告 P50/P95：中 ESP≤30s/RSS≤1GiB，小 ESP≤3s，UI heartbeat≤200ms、progress≤500ms；并发≤3、取消 P95≤1s；100k checkpoint P95≤100ms、恢复100%；500轮 Session RSS增长≤15%。

## 测量接口与流程

BenchmarkCase(corpus hash, warmup, repetitions, hardware tier) → isolated runner → raw samples(time/RSS/concurrency/heartbeat/resource count) → percentile calculator → threshold verdict → evidence artifacts。所有时钟/采样器独立于业务 clock；故障恢复验证 committed IDs 而非只看文件存在。

## 实施步骤

1. 固定 small/medium/100k/100-call/session/archive corpus 与生成器 seed。
2. 进程隔离测 parse/RSS，Qt heartbeat probe 测事件循环；记录冷/热运行。
3. 受控 LLM server 记录活动请求/新副作用时间，驱动 cancel。
4. 注入 checkpoint/commit 崩溃，比较预期已提交集合；Session 循环后 GC/稳定窗口采样。
5. 阈值存版本化配置，任何放宽需需求确认与 changelog。

## 测试边界与迁移

开发机结果可作早期反馈，最终硬件/Windows 证据由 S05 环境给出；噪声过大增加样本而非挑最好结果。超限是 QA failure/blocker，不自动调阈值。

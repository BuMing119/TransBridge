# Story 12: MixedWorker+统一进度窗口

**所属方案**: `plans/ai-translation/plan.md`
**状态**: 🚧 待编码
**对应需求**: FR5.11.5, FR5.11.8
**引用 ADR**: ADR-007, ADR-004

## 概述

实现 `_MixedWorker` 统一调度线程和统一进度窗口，支持串行/并行两种执行模式，独立失败隔离。

## 验收标准

- [ ] `_MixedWorker(QThread)` 统一调度翻译+润色
- [ ] 信号协议：`progress(part, current, total, msg)`, `translate_finished`, `polish_finished`, `all_finished`
- [ ] 串行模式：先翻译后润色，翻译产出作为润色输入
- [ ] 并行模式：翻译和润色同时执行，共享 max_concurrent 配额
- [ ] 统一进度窗口：双进度条（翻译/润色）+ 各自状态统计
- [ ] 暂停/停止同时控制两个子任务
- [ ] 翻译失败不阻断润色（反之亦然）

## 实现步骤

### 步骤 1: _MixedWorker 实现
- 新建 `_mixed_worker.py`
- 构造函数：`__init__(translator_cfg, polisher, translate_entries, polish_entries, execution_order)`
- `run()`: 根据 `execution_order` 决定串行/并行
  - 串行：`_run_translate()` → 结果写入 collection → `_run_polish()`
  - 并行：`ThreadPoolExecutor(max_workers=2)` 同时提交
- stop_event / pause_event 共享控制
- 信号：`progress`, `translate_finished`, `polish_finished`, `all_finished`, `error`
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_mixed_worker.py` (新)

### 步骤 2: 统一进度窗口
- 新建或扩展进度窗口，支持双进度区域
- 翻译区域：进度条 + 成功/失败/跳过统计
- 润色区域：进度条 + 接受/拒绝/失败统计
- 底部统一暂停/停止按钮
- 连接 `_MixedWorker` 的信号更新 UI
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_mixed_progress_window.py` (新)

### 步骤 3: 失败隔离
- `_run_translate()` 异常不抛出，记录到 `translate_error`
- `_run_polish()` 异常不抛出，记录到 `polish_error`
- `all_finished` 信号携带两部分的结果和错误状态
- 报告标注各部分执行状态
- 涉及文件: `src/transbridge/ui/tools/ai_translator/_mixed_worker.py`

## 涉及文件

| 文件 | 操作 |
|------|------|
| `src/transbridge/ui/tools/ai_translator/_mixed_worker.py` | 新建 |
| `src/transbridge/ui/tools/ai_translator/_mixed_progress_window.py` | 新建 |

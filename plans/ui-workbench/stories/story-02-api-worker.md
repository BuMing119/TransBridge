# Story 02: ApiWorker 后台线程 + 全局信号总线

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

QThread 后台执行器 + 全局 HTTP 错误总线和 API 状态总线。所有 API 请求和耗时操作必须通过 ApiWorker 执行。

## 关键设计

- **ApiWorker(QThread)**: result/error/progress 信号，run() 中执行 callable
- **_http_error_bus**: 全局 HTTP 错误信号（401→弹出配置对话框，403→显示权限不足）
- **_api_status_bus**: 全局 API 状态信号（request_started/request_finished → _ApiStatusIndicator 绿点/转圈/红点）
- **401/403 路由**: 不在 worker.error 中触发，统一走 _http_error_bus
- **Worker 引用保留**: self._workers.append(worker) 防止 GC
- **进度回调**: make_progress_callback() 生成线程安全的进度回调

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/workers.py` | ApiWorker, _http_error_bus, _api_status_bus |

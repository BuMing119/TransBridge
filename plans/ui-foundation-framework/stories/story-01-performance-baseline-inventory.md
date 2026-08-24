# Story-01：UI 性能基线与样式迁移清单

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：已完成（2026-08-24）
- **优先级**：P0
- **前置依赖**：无
- **下游依赖**：S02～S09；本 Story 的基线和清单是后续完成声明的证据源

## 目标

在视觉实现变化之前建立可复现基线和完整迁移边界，避免主题方案只能凭感觉判断性能。

## 原始验收标准

- [x] 代表性窗口树包含 MainWindow、Workbench、AI Translator、Smart Assistant、ParaTranz 和一个大表格/对话框组合，并固定 fixture fingerprint、字体、DPI、平台、Qt/PyQt 版本。
- [x] 窗口树覆盖 FR26 当前生产路径：Start Center、恢复后的 Workbench、Guidance、Task Center、Command Palette/Context Help、Operation Plan 与 FOMOD；历史 Step1/Step3 只有在可达性审计证明生产入口仍会构造时才纳入。
- [x] 冷进程记录启动至首个可交互窗口、RSS、UI Foundation 占位初始化耗时；热进程记录窗口打开 P50/P95、heartbeat 和 100 次空切换控制组。
- [x] 阈值扩展到现有 `THRESHOLDS_V1` 或明确的新 version；同一数字只有一个真源。
- [x] 生成机器可检查的硬编码颜色、`setStyleSheet`、自定义绘制、QSettings 与可访问属性清单，按子系统/风险分组。
- [x] 基线测试在无显示服务器时明确 skip/降级，不得用同步 callback 假装真实 Qt 窗口性能通过。

## 当前调用链与约束

- `transbridge.ui.app.main()` 创建 `QApplication`、`AppRuntime`/binding、`AppContext` 和 `MainWindow`；真实冷启动测量应从隔离子进程进入该边界，但使用受控 runtime/fixture，不能访问真实网络或用户项目。
- FR26 已取消缺少可选服务配置时的启动阻塞模态框。性能 fixture 必须分别覆盖无活动工程的 Start Center 和成功恢复后的 Workbench，且断言两条本地路径都不会因 ParaTranz/LLM 配置弹窗而失真。
- `WorkbenchWidget` 当前生产组合不再以 Step1/Step3 为主页面；审计需记录 compatibility 模块的真实 import/construction reachability，避免为不可达窗口制造视觉迁移工作量。
- 现有 `tests/performance/benchmark_cases.py` 是阈值单一真源，`measure.py` 已提供 P50/P95、RSS 和隔离子进程工具。
- 现有 UI heartbeat 只是 callback boundary probe；本 Story 必须增加真实 Qt event loop/window tree 测量，并保留对无法运行 GUI 环境的明确标记。

## 计划新增的数据结构

```text
UiBenchmarkProfile
  profile_id
  fixture_fingerprint
  qt_version / pyqt_version / platform
  font_family / point_size / logical_dpi / device_pixel_ratio
  warmup / repetitions

UiBenchmarkResult
  case_id / profile_id / environment
  samples / p50 / p95
  rss_before / rss_after / rss_peak
  heartbeat_max / skip_reason
```

`fixture_fingerprint` 由窗口类型列表、固定条目数、字体/DPI 参数和 fixture schema 计算；业务数据内容变化必须显式更新 fingerprint，不能静默改变负载。

审计输出至少包含：`path`、`line`、`kind`、`subsystem`、`risk`、`snippet_hash`、`status`、`exemption`。`kind` 固定为 `hex_color | stylesheet | custom_paint | qsettings | accessibility | rich_text` 等稳定枚举。

## 实施步骤

1. 在 `benchmark_cases.py` 为 UI Foundation 增加版本化预算字段和 case registry，直接引用 NFR1.4 数字；不得在测试文件重复常量。
2. 建立 `ui_foundation_window_tree.py`：用固定数量的 `TranslationEntry`/Task/消息/ParaTranz DTO 构造真实 widget，禁用网络和真实持久化，显示并推进 Qt event loop 到稳定状态。
3. 增加隔离冷启动 runner：记录 `QApplication` 创建、Foundation 占位、MainWindow 首次 show/事件循环稳定点和 RSS。若不能安全构造完整 MainWindow，结果必须标为 `partial-boundary`，不得写成最终 GUI 通过。
4. 增加热测 runner：重复打开代表性窗口、推进事件循环、关闭并清理；采集 P50/P95 和 heartbeat。建立 100 次“无实际 palette 变化”的控制组，为 S03 幂等开销比较。
5. 编写审计脚本。优先用 AST 定位 `setStyleSheet`/`QColor`/`QBrush`/`QSettings` 调用和字符串常量；对 f-string/QSS 片段用受限文本扫描补充。脚本只读源码并输出稳定 JSON/Markdown。
6. 在 FR26 已交付的 `migration-inventory.md` 上补齐 shell/guidance/task/command/operation/FOMOD 与 legacy reachability，按当前 composition root 标出表格、Delegate、Markdown、报告等高风险路径。
7. 增加测试确保清单连续运行稳定、所有路径存在、无重复 record，且基线环境字段完整。

## 边界与失败处理

- 无 DISPLAY/offscreen plugin 或 Qt 初始化失败：测试 skip 并记录原因；CI 不得用空窗口替代真实 fixture 后宣称满足预算。
- 可选服务配置：fixture 分别使用无 token 和受控已就绪状态，断言无 token 的纯本地启动不产生配置模态框，也不修改用户配置。
- RSS 无法精确测量：遵循现有 `psutil`/tracemalloc 语义，近似值必须标注 proxy，不作为 Windows 权威证据。
- audit 解析失败：该文件产生 `audit_parse_failed`，命令非零退出，不能把失败当作零违规。
- 用户工作区存在其他改动：只生成本 feature 的基线/清单，不重写其他测试阈值或索引。

## 测试策略

- 单元：fingerprint 稳定性、P95 registry、审计分类/去重/豁免格式。
- Qt 集成：真实窗口树 show/close、event loop heartbeat、重复构造释放。
- 隔离进程：冷启动与 RSS，不复用 pytest 进程污染。
- 建议命令：
  - `python scripts/audit_ui_foundation.py --check`
  - `pytest tests/performance/test_ui_foundation_performance.py -q`
  - `pytest tests/ui -q`

## 文件变更清单

- 修改 `tests/performance/benchmark_cases.py`
- 新增 `tests/performance/test_ui_foundation_performance.py`
- 新增 `tests/ui/fixtures/ui_foundation_window_tree.py`
- 新增 `scripts/audit_ui_foundation.py`
- 修改 `plans/ui-foundation-framework/migration-inventory.md`
- 复用 `scripts/benchmark_ui_modularization.py`、`scripts/compare_ui_modularization_benchmark.py` 与 FR26 J01～J09 fixture，新增 theme-specific probe 而不复制阈值

## 风险与回退

测试 fixture 若过度 mock 会给出虚假性能结论；必须保留真实 Qt widget 数量和主要绘制路径。此 Story 不改生产代码，回退只需移除新增 benchmark/audit 文件，不影响业务状态。

## 未决问题

- Windows 权威硬件档沿用 `WINDOWS_S05` 还是建立 UI 专用档位，由实现时结合现有发布设施决定，但必须在结果中固定硬件描述。

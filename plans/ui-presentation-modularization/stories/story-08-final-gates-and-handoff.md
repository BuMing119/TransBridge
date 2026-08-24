# Story-08：全量终验与 UI Foundation 交接

- **所属 Plan**：[UI 展示层模块化与上帝窗口拆分](../plan.md)
- **状态**：已完成（2026-08-19）
- **优先级**：P0
- **前置依赖**：S01～S07
- **下游**：解除 `ui-foundation-framework` 生产实现阻塞

## 目标

用可追溯证据证明拆分没有改变行为、视觉、性能、状态所有权或生命周期，并把稳定的主题接入点正式交给 FR24。

## 原始验收标准

- [x] S01 的全部可观察行为序列在新 composition 下等价；视觉抽检无非预期布局/文案/焦点变化。
- [x] 冷启动、窗口打开和关键交互 P95/RSS 回归不超过 5% 或 10 ms 中较宽者，GUI heartbeat ≤200 ms。
- [x] 100 次关键窗口创建/销毁后 subscriptions/timers/workers 与 RSS 回到预热容差，无 deleted QObject 回调。
- [x] 模块规模、import cycle、私有跨访、parent lookup、View-infra 和 singleton 审计通过；剩余例外有 owner/原因/退出条件且不阻断 FR24。
- [x] 公开 import/行为 compatibility tests、现有 UI/integration tests 和 Windows 权威性能报告通过。
- [x] 更新 ADR-020/FR24 迁移清单的稳定接入点，明确解除 `ui-foundation-framework` S02～S09 阻塞所需证据。
- [x] 回退演练证明单个 feature facade 可切回旧 composition，不损坏业务数据、项目或统一配置。

## 最终证据包

- `Behavior parity`：S01 scenario ID 对应新 trace，command 数量、可见状态、revision/generation、终态和错误分类一致。
- `Performance`：同一 registry/硬件/数据集的 before/after P50/P95/RSS/heartbeat，标出噪声和样本数。
- `Lifecycle`：100 次 Main/Workbench/AI/Chat/代表性 dialog 创建销毁，UI-owned subscription/timer/worker 清零或回 warm baseline。
- `Architecture`：尺寸、方法数、cycle、private access、parent lookup、View-infra、singleton 和豁免清单。
- `Compatibility`：公共 import、旧 facade、legacy/V2 context 与关键插件/工具入口。
- `Handoff`：每个稳定 View/adapter 的 ThemeService/Palette/token 接入点；哪些组件仍需 compatibility provider。

## 交接到 FR24 的接口

- 应用级 ThemeService 只在 `ui/app.py`/MainWindow composition 注入，不进入 application/domain。
- shell、Workbench、AI、Chat 等 View 从 Theme adapter/公共 component 获取视觉状态；Presenter 不依赖主题。
- table/message/progress 的 theme revision 与 projection/render/run generation 分离，主题切换不得重启业务 command 或复制全量数据。
- Binding/Subscription 的 close 合同供 Theme/Locale 订阅复用；不得重新引入窗口树扫描或 parent lookup。

## 实施步骤

1. 冻结代码候选并运行全量 characterization、public contract、UI/integration tests；逐 scenario 比较 S01。
2. 在 Windows 权威环境运行相同 performance cases；对超预算项归因到具体切片，不通过调整阈值掩盖。
3. 运行 100 次 lifecycle、迟到事件、重复终态和 close 演练；检查 QObject warning、资源数与 RSS。
4. 运行全仓 architecture audit；清理到期豁免，评审仍存例外是否真正不阻断 FR24。
5. 对每个 feature facade 做一次受控旧 composition 回退/恢复演练，确认没有数据迁移和双写依赖。
6. 更新 ADR-020/FR24 plan 与 migration inventory 的真实文件落点、owner 和接入顺序；只有全部 hard gate 通过才解除 S02～S09 阻塞。
7. 实现阶段按 bm-qa 生成真实 QA/性能报告；本设计阶段不创建虚假通过记录或把状态改为完成。

## 边界与失败判定

- 视觉抽检发现差异时先区分平台渲染噪声与布局/文案/焦点变更；后者为阻塞。
- 任何重复 command、重复网络调用、额外 polling、迟到事件改 UI、deleted QObject callback 为阻塞，即使行数达标。
- P95/RSS 超预算必须修复或经用户基于证据重新批准；不得自行放宽。
- 有 owner 的豁免也不能包含会让 ThemeService 再耦合上帝类的路径；否则 FR24 保持 blocked。
- 回退演练若依赖旧新双写或数据转换，说明切换边界不成立，必须回到对应 Story 修复。

## 文件变更

- 完成 `scripts/audit_ui_modularity.py`、performance/public contract/integration tests
- 更新 `plans/ui-presentation-modularization/dependency-inventory.md`
- 更新 `plans/ui-foundation-framework/plan.md` 与 migration inventory
- 实现阶段新增项目规范位置下的 QA/性能报告

## 测试与建议命令

- `pytest tests/ui tests/contracts/ui tests/integration/gui -q`
- `pytest tests/performance/test_ui_modularization_performance.py -q`
- 仓库全量回归与静态检查命令以实现时项目配置为准；Windows 权威性能不得用开发机单次计时替代。

## 风险与回退

终验失败不回滚全部迁移：定位到 feature facade，切回该切片旧 composition，保持其余已通过切片。若架构边界本身错误，则保留 FR24 blocked 并回到相应 Story/ADR 重新评审。

## 未决问题

- QA 报告文件名、最终性能阈值和豁免退出日期必须由实现时真实证据填写；设计阶段只规定门禁，不预填结果。

# Story-09：扩展合同、全量审计与最终性能门禁

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：草稿
- **优先级**：P0
- **前置依赖**：S05 设置、S06 Main/Workbench、S07 Tools/ParaTranz、S08 i18n/a11y
- **下游依赖**：实现完成/发布候选；本 Story 不自动发布

## 目标

证明 Provider 接口真实可扩展、迁移没有留下双主题状态，并完成发布前性能、资源、失败回退和审计证据。

## 原始验收标准

- [ ] 使用一个仅存在于测试的第三方 Provider 证明无需改业务组件即可注册、resolve、apply 和回退；不实现动态发现或用户安装。
- [ ] forward schema、缺 token、超资源预算、ID 冲突、非法路径、异常 provider 均原子拒绝，当前主题不变。
- [ ] 审计阻止新裸颜色、颜色型局部 QSS、UI theme QSettings、直接 Provider 执行和无界 theme cache；豁免有 owner 与移除条件。
- [ ] 冷初始化新增 P95 ≤75 ms、RSS ≤12 MiB；热切换 P95 ≤250 ms、heartbeat ≤200 ms；窗口打开回归 ≤5% 或 10 ms；100 次切换预热后 RSS 增长 ≤2 MiB。
- [ ] idle 期间 Theme/Locale 无 timer、无窗口树扫描；重复选择当前主题零 apply/零 signal。
- [ ] compatibility provider/旧 QSS 删除门禁满足，或把残留项以明确 blocker 和后续 Story 保留，不能伪称全量完成。
- [ ] 回退到内置浅色后 GUI 核心操作可用，业务数据和统一配置不损坏。
- [ ] FR26 J01～J09 固定旅程保持相同 canonical intent、D/M/N、默认焦点、取消点与返回上下文；主题切换期间 application command、网络/文件副作用、preflight、confirm token 与 Task Run ID 计数均不增加。

## 测试 Provider

在 `tests/contracts/ui/` 定义一个最小 `acme.test-theme` Provider：

- manifest/schema/version 与完整 light/dark tokens；
- 一个小型受预算图标资源；
- 与内置主题明显不同但满足对比度的 accent/domain colors；
- 无生产注册、无目录扫描、无动态 import。

合同测试从空 Registry 注册它，通过 ThemeService/Settings preview/apply 到代表性业务组件，验证组件只按 semantic/domain key 工作。随后 unregister/模拟下次启动缺失，系统回退内置 default 并保留稳定诊断。

负例 provider 分别只破坏一个边界，避免一个 fixture 同时触发多错误导致误判：forward schema、missing token、resource oversize、absolute/traversal path、ID conflict、load exception、non-declarative payload。

## 性能测量协议

沿用 S01 固定 profile 和同一 `THRESHOLDS` 真源：

1. **冷初始化**：隔离进程，先测无 Foundation 基线，再测 Registry+builtins+ThemeService start；报告差值 P95 与 RSS，不用不同窗口树相减。
2. **热切换**：light/dark 已编译预热后，代表性窗口全开，采集 switch 调用至 event loop 稳定、heartbeat max 和实际 apply/signal 计数。
3. **窗口打开**：相同 active snapshot 下比较 S01 基线，预算为相对 ≤5% 或绝对 ≤10 ms，取较宽者。
4. **长期稳定**：先完成 light/dark 双主题预热，GC 稳定采样，再 100 次往返；最终 RSS 相对预热后 ≤2 MiB，同时 cache cost ≤配置上限。
5. **Idle**：instrument timer 注册、styleHints callback、Theme/Locale method counts；固定空闲区间内除操作系统真实 signal 外为零。
6. **幂等**：重复 preference 100 次，palette apply=0、theme_changed=0、config writes=0。
7. **FR26 行为保真**：分别在 light/dark/system 与运行中切换场景复跑 J01～J09，比较 intent trace、D/M/N、focus/cancel/return context、operation digest 和 Run ID/side-effect counters。

开发机只作早期门禁；Windows 10/11 固定硬件档输出权威 JSON 和人工/截图检查摘要。任何阈值调整必须先更新需求状态并附前后证据。

## 审计门禁

`scripts/audit_ui_foundation.py --check` 至少阻止：

- Foundation 已覆盖目录中新 hex/rgb/named theme colors；
- `setStyleSheet`/QSS 中颜色、gradient、background image；
- theme/locale 使用 `QSettings` 或直接 `ConfigParser`；
- `QApplication.allWidgets()`、全量 polish/unpolish 和 theme polling timer；
- Provider payload 含 callback/widget/raw QSS/URL/绝对路径；
- 无成本上限的 cache dict；
- 公共组件用户可见裸文案和关键交互缺 accessibleName。

豁免格式必须包含 `rule`、`path/symbol`、`owner`、`reason`、`expires_when`。过期/不存在路径/无 owner 的豁免使门禁失败。

## 失败恢复集成场景

```text
valid light active
  -> request invalid external definition
  -> registry rejects, revision/palette unchanged

valid dark active
  -> inject Qt apply failure
  -> restore last-good dark, no candidate signal/persist

persisted external theme, provider missing on restart
  -> diagnostic + builtin light fallback
  -> user can open Settings and select builtin

theme switch during project edit/task/network request
  -> UI converges, business hashes/run ids/request counts unchanged
```

配置验证比较 `[ui]` section 和完整 repository revision；回退不得覆盖其他 LLM/ParaTranz/credential sections。

## 实施步骤

1. 完成 contract provider 和单故障负例，覆盖 Registry → ThemeService → components → Settings 的真实链。
2. 将 S01 benchmark 从 baseline mode 扩展为 Foundation final mode，冻结环境元数据和 JSON result schema。
3. 实现 apply/signal/config-write/timer/window-scan/cache-cost instrumentation，仅在测试/诊断接口暴露，不给生产热路径增加常驻开销。
4. 收敛 audit rules，逐项处理 migration inventory；能删除的 compatibility QSS/provider 删除，不能删除的转为显式 blocker/后续 Story。
5. 执行 failure recovery integration，比较业务数据 snapshot/hash、Task run_id/status、network call count 和 config sections。
6. 执行开发机全量门禁；在 Windows 固定硬件运行权威性能与关键窗口 render/人工可读性检查。
7. 复核 Requirement → ADR → Plan → Story → Tests 追溯。只有全部 P0、性能证据和残留声明一致时才能把实现状态改为完成。

## 边界与错误处理

- Windows runner 不可用：Story 保持未完成/待权威证据，不能用 Linux offscreen 代替最终通过。
- 结果噪声超过阈值：检查固定 CPU/后台负载、样本数和窗口树 fingerprint；不得只删异常样本。
- 一项门禁失败：定位本次回归并修复；若预算确实不合理，走需求重新确认，不在 tests 调宽。
- 残留旧 QSS 有业务阻塞：登记 path/owner/blocker/superseded_by，Plan 状态不得写全量完成。
- 外部 provider 测试通过不等于开放安装能力；用户界面仍不出现导入/市场。

## 测试策略与建议命令

- `python scripts/audit_ui_foundation.py --check`
- `pytest tests/contracts/ui tests/ui/foundation -q`
- `pytest tests/ui tests/integration/gui -q`
- `pytest tests/performance/test_ui_foundation_performance.py -q`
- 按发布流程在 Windows 权威档生成 JSON/报告，不把截图当性能或功能证据。

## 文件变更清单

- 修改 `scripts/audit_ui_foundation.py`
- 修改 `tests/performance/benchmark_cases.py`
- 完成 `tests/performance/test_ui_foundation_performance.py`
- 新增 `tests/contracts/ui/test_theme_provider_contract.py`
- 新增 `tests/integration/gui/test_theme_failure_recovery.py`
- 扩展 `tests/ui/ux/test_current_user_journeys.py` 的 theme matrix；复用 `plans/guided-ui-workflows/p0-journey-evidence.md` 固定 fixture
- 复用 `scripts/benchmark_ui_modularization.py` 与 `scripts/compare_ui_modularization_benchmark.py`，新增 FR24 theme case 而不复制相对阈值
- 更新 `plans/ui-foundation-framework/migration-inventory.md`

## 风险与回退

最终门禁可能暴露 Palette-first 在某类控件上的真实限制。优先优化 adapter/cache/绘制范围；若需要 QProxyStyle 或第三方框架，必须新 ADR，不允许在 S09 临时引入。随时可回退内置/compat light，业务数据和 AppRuntime 不变。

## 未决问题

- compatibility provider 能否在 S09 删除取决于 inventory 和真实窗口验证；若保留，必须给出明确移除条件和版本，不设无限期豁免。

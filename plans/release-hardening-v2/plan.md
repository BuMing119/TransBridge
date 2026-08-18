# Quality Foundation and Release Hardening V2

- **状态**：实现完成，综合 QA 通过（2026-08-18）
- **日期**：2026-08-18
- **需求**：所有 FR17～FR23 共同验收规则、NFR1.1～1.3、NFR2.1、NFR3.1、NFR4.1、NFR5.1、NFR6.1
- **架构**：ADR-016～019 及 ADR-012～015 的 2026-08-18 增量
- **问题**：R-047～R-050，并作为 R-001～R-046 的最终证据门禁
- **依赖**：S01 可先行；S02～S04 随各业务 Plan 增量执行；S05 在其余六个 V2 Plan 完成后执行

## 目标与边界

建立可复现、证据驱动的质量底座：真实成功链合同、固定语料和性能预算、Windows 安全/兼容、clean build/install/import/CLI/MCP smoke，以及绑定环境、lock、命令、JUnit/coverage/artifact 的 QA 报告。

本 Plan 不以 mock-only 或负路径代替业务成功链，也不把“类存在/测试数量/历史通过摘要”当作验收证据。发现 Blocker/Critical 时按 bm-pilot 门禁暂停。

## Story 清单

### Story 01：可复现测试环境、证据 Manifest 与质量基线

[详细设计](stories/story-01-reproducible-evidence.md)

- **目标**：验证 uv 受管 Python 环境并修复无法绑定环境/锁/产物的 QA 记录；受限执行上下文不得被误判为环境失效。
- **文件落点**：测试/QA 脚本、fixture manifest、CI/local 配置、`docs/test-reports/` 模板；依赖平台 Plan S01。
- **实施**：定义 Python 3.12 重建流程；记录 Git diff/工作树状态、OS、Python、lock hash、依赖、命令、JUnit、coverage 和 artifact hash；测试失败不允许人工摘要为通过。
- **验收**：另一干净环境可按 manifest 重放；13% 指标只能判失败/未达标；不可用环境明确 blocker 而非静默换系统 Python。
- **测试**：环境 bootstrap smoke、manifest schema、故意失败传播、artifact hash 重放。

### Story 02：真实成功链与跨入口合同测试资产

[详细设计](stories/story-02-success-chain-parity.md)

- **目标**：为 ESP/EET/XT/Strings、翻译/后处理、Project/Session、ParaTranz、FOMOD 建立非 mock-only 成功链。
- **文件落点**：`tests/fixtures/`、`tests/contracts/`、`tests/integration/`、必要受控 HTTP/LLM servers。
- **实施**：每项能力至少一条真实 fixture 或受控集成；parse→write→reparse；GUI/Agent/MCP/FOMOD 对同一 use case parity；结果包含 run_id/diagnostic/artifact 摘要。
- **验收**：122 个旧单测不能单独完成验收；每个 V2 Story 的成功链可独立运行；mock 仅用于外部故障注入，不替代核心序列化/组合根。
- **测试**：本 Story 本身由 fixture checksum、golden 更新审批和重复运行确定性验证。

### Story 03：性能、取消、恢复与长期稳定性门禁

[详细设计](stories/story-03-performance-stability.md)

- **目标**：把已确认预算变成固定语料、硬件档位和 P95 测试。
- **文件落点**：`tests/performance/`、benchmark corpus manifest、UI heartbeat probe、资源泄漏探针。
- **实施**：中/小 ESP 解析时间与 RSS；UI 心跳/进度；并发 3 与取消延迟；100k checkpoint；故障恢复 100%；500 轮 Session RSS 增长≤15%；归档预算。
- **验收**：报告含样本数、P50/P95、硬件和原始结果；预算放宽必须重新确认，不能改测试阈值规避失败。
- **测试**：基准重复性、慢机分档、故意超限、资源清理和 timeout 行为。

### Story 04：Windows 路径、安全与格式/依赖能力矩阵

[详细设计](stories/story-04-windows-security-capability.md)

- **目标**：覆盖 Windows 10/11、非 ASCII/长路径、链接/路径逃逸、归档预算和可选依赖。
- **文件落点**：Windows integration tests、安全 corpus、capability matrix generator。
- **实施**：所有入口复用规范化授权；格式读/写/往返/入口/发布状态由测试生成；缺依赖验证 degraded；disabled 检索零加载；secret canary 全产物扫描。
- **验收**：不支持/实验能力不会被 UI/Agent/MCP 宣称支持；路径与归档攻击在写前阻断；Windows 目标组合有可复验证据。
- **测试**：矩阵参数化、symlink/junction、Unicode/长路径、依赖删除环境、secret scan。

### Story 05：Clean Build/Installer/Upgrade/Uninstall 与最终 QA

[详细设计](stories/story-05-clean-release-qa.md)

- **目标**：交付 installer + onedir 主发行，并验证所有入口和许可证/版本。
- **文件落点**：PyInstaller spec、installer 配置、release scripts、license inventory、最终 QA 报告。
- **实施**：从干净 checkout/lock 构建；安装/升级/卸载；核心 import、GUI 启动、CLI `--help`、MCP stdio、可选能力、版本、许可证；汇总全部业务 Plan 的 JUnit/coverage/artifacts；Blocker/Critical 停门禁。
- **验收**：安装态不依赖仓库 `src` 路径或系统偶然包；卸载不删除用户项目；便携包若提供也通过 smoke；所有 P0 合同与正式报告发现有证据状态。
- **测试**：Windows clean VM/等价隔离环境 smoke、upgrade/downgrade policy、uninstall user-data preservation、artifact checksum/signing policy（若项目采用）。

## 追溯与历史状态纠偏提议

| 需求/问题 | Story | 历史 Plan 提议状态 |
|---|---|---|
| 共同验收规则；R-047/048 | S01/S02/S05 | 所有受影响旧 Plan 的“已实现”保留为历史交付，但索引改为 `partially-verified` |
| NFR1；R-049 | S03 | 涉及 parser/task/session/fomod 的历史性能结论 `blocked_by: release-hardening-v2/S03` |
| NFR3/4/5 | S02/S04 | 格式、入口、安全完成声明 `blocked_by` 对应合同矩阵 |
| NFR6；R-050 | S01/S05 | 历史发行口径由 installer+onedir 计划取代；不伪造过去 onefile 完成状态 |

## 风险、回退与完成门禁

- 风险：没有可用 Windows 10/11 干净环境。处理：记录为 QA Blocker 并暂停用户决策，不用开发机替代最终证据。
- 风险：真实 fixture 有版权/体积限制。控制：使用最小自有/可再分发 corpus，并记录来源与 checksum。
- 回退：质量阈值和证据 schema不能因失败回退；只能修复实现、经用户确认调整预算或明确接受风险。
- 完成门禁：所有六个业务 Plan 通过各自 QA；S01～S05 证据完整；Blocker/Critical 清零或经用户明确接受风险；之后才进入正式审查报告回填。

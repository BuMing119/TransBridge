# Story 01：可复现测试环境、证据 Manifest 与质量基线

- 所属 Plan：[Quality Foundation and Release Hardening V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：共同验收规则、NFR6.1；ADR-016；R-047/R-048
- 依赖：可先行；platform S01 最终闭环

## 目标与验收

另一干净环境可按 manifest 重放；测试失败/13% 指标不能被摘要为通过；不可用项目环境明确 blocker，不静默用系统 Python 替代。当前仓库 uv 环境经授权访问其受管目录后可正常执行 Python 3.12.12，先前失败属于受限执行上下文证据，不重建有效环境。

## 数据流与接口

test invocation → `EvidenceManifest` 捕获 repository/worktree descriptor、OS/hardware/Python、lock/config hashes、command、start/end/exit、JUnit/coverage/log/artifact hashes → immutable QA run directory → index/report 引用。未提交工作树用 diff hash 标识，不伪造 commit。

## 实施步骤

1. 定义 evidence schema/version、fixture/corpus manifest 和 run id；敏感环境变量只记 allowlist/摘要。
2. 提供 Python 3.12 环境 bootstrap/check 脚本，`uv sync --frozen` 失败直接阻断。
3. 测试 wrapper 传播真实 exit code，生成 JUnit/coverage 原始文件和 checksum。
4. QA 报告从 manifest 汇总，不手工改写指标状态；阈值与结果分离。
5. 本地/CI 使用同一入口，时间/路径非确定字段规范化。

## 边界、迁移与测试

不上传 secret、用户项目或受版权保护 corpus；工作树 dirty 可测试但必须记录 diff hash。用“故意失败测试”“缺 lock”“损坏 artifact”验证状态；在第二临时 venv 重放 smoke 并比较 manifest。此 Story 不声称修复业务测试。

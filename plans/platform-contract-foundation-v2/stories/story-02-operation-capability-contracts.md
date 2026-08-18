# Story 02：公共 Operation 合同与能力注册

- 所属 Plan：[Platform Contract Foundation V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR17.1～17.3、FR20.1；ADR-016/019；R-003/R-005
- 前置依赖：S01 的稳定包路径

## 目标与原始验收

建立同步结果、后台 TaskRef、互斥 outcome、诊断、错误与能力可用性的唯一类型合同。同一条件下返回类型稳定，异常不映射为 completed，入口能预检 available/degraded/unavailable。

## 数据流与接口

入口 request + `RuntimeContext` → use case → `OperationResult[T]`；若后台执行，use case 明确返回 `Deferred[JobRef]`，不在同一方法随机返回字符串 ID。计划新增 `OperationOutcome`（completed/partial/failed/cancelled）、`Diagnostic`、`DomainError`、`CapabilityId/State/Report`、`RequestContext`。现有 `ToolResult.success/partial` 仅作为 adapter DTO，须无损映射公共合同。

## 实施步骤

1. 在 `src/transbridge/application/contracts/` 定义冻结 dataclass/enum 和 JSON schema；终态枚举不可同时成立。
2. 建立异常映射器：input/prerequisite/permission/conflict/external/cancel/internal；保留 cause chain 供日志，外部结果脱敏。
3. 建立 CapabilityRegistry，能力由实际 adapter/依赖/context 注册，不由类/模块存在推断。
4. 提供 ToolResult/MCP/GUI message adapters；展示截断不能修改结构化结果。
5. 加入 schema version 与向后兼容读取，禁止业务层依赖 UI 文案。

## 边界、迁移与错误处理

partial 必须含成功/失败统计与诊断；cancelled 不携带可提交结果；internal error 默认不暴露路径/secret。先让新 use case 使用合同，再包装旧工具；回退可恢复旧展示 adapter，但不得恢复 bool-only 业务判断。

## 测试策略

新增 `tests/contracts/test_operation_contracts.py`：枚举互斥/序列化 round-trip/异常映射/Deferred 类型/能力缺失；用属性测试验证任意异常路径不产生 completed。再对现有 ToolResult 构造 canonical adapter fixture，确保 `[PARTIAL]`、failed_items 与 diagnostic 不丢失。

## embedding 语义检索断连修复 — 测试报告

**日期**: 2026-08-13
**对应变更**: docs/changelogs/ai-translation/fix/2026-08-13-003

### 测试覆盖
| 测试项 | 状态 | 备注 |
|--------|------|------|
| 工厂函数 provider 分派（5 用例） | ✅ | local 默认 / local 带路径 / openai 完整 / openai 回退 LLM 主配置 / 未知 fallback |
| 全量回归 | ✅ | 540 passed（基线 535 + 新增 5） |
| 导入链验证 | ✅ | term_database 相对导入 `..infra.embedding_client` 解析成功 |

### 审查结论
- **方案一致性**: ✅ 两处修复对齐 ADR-010 infra 提取后的实际架构（embedding_client 已迁至 infra/，配置收进 EmbeddingConfig 子对象）
- **代码质量**: ✅ 最小化修复，符合现有代码风格，py_compile 通过
- **安全性**: ✅ 无新增安全风险（不涉及注入/越权/敏感信息处理变更）

### 发现的问题
- [Minor] 工厂函数忽略 `EmbeddingConfig.mode`（死配置：默认 "disabled" 且全项目无设置点），当前按 provider 分派是正确选择，但 mode/provider 双维度语义未统一，建议后续独立立项
- [Minor] 测试缺 `custom` provider 显式断言（该分支已由 `provider in ("openai","custom","api")` 覆盖，但无独立用例）
- [Minor] `config.embedding` 为直接属性访问，若传入非 LLMConfig 对象会 AttributeError；当前唯一调用点（TermDatabaseManager）确认传 LLMConfig，无实际影响

### 签名
QA 通过

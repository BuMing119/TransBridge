# ParaTranz OpenAPI 0.7.0 对齐

- **状态**：草稿
- **依据**：[官方 OpenAPI](https://paratranz.cn/api-docs?format=json)、[更新历史](https://paratranz.cn/api-docs/changelog)、[ADR-023](../../docs/adr/023-local-project-paratranz-binding.md)

目标是对齐远端 API 封装与现有调用流程。0.5.7 封装覆盖了当前规范的 67 项，0.7.0 共列出 88 项。新增管理能力通过 Python API 提供，现有讨论、私信和导出操作同步修正。项目数据和绑定无需迁移。

## Story 01 补齐 API 契约

- 在 `src/transbridge/paratranz/api/` 的对应领域增加 21 项操作和官方查询、请求体选项，保留既有默认值和位置参数。
- 按 OpenAPI 区分 multipart、JSON、空响应和重定向下载。覆盖权限受限、显式零值和批量条件。
- 验收使用真实 Requests 序列化和离线响应。在实现完成后与官方 88 项路由复比。

## Story 02 导出与错误处理

- 在 ParaTranz 客户端和应用端口中分类参数、资源锁定和上游错误，保留已脱敏的服务端请求 ID 与错误代码，只重试合适的瞬态错误。
- 从触发导出返回的 ID 查询构建状态，及时终止失败任务，保留取消和压缩包发布前的身份验证。
- 验收覆盖旧调用、排队至成功、失败、超时与重试。用离线请求模拟验证机翻凭据脱敏。

## Story 03 讨论与私信分页

- 讨论使用页码，私信会话使用 `before` 游标。分页动作沿用现有后台请求与目标切换保护。
- 验收包括第二页、旧消息、切换目标和错误状态。

## 实施顺序

HTTP 和路由可以并行推进。接口签名确定后接入导出与分页调用方，先跑定向回归，再验证 ParaTranz 相关测试和全仓 Ruff。整体回退可恢复旧 Python 适配器，已执行的远端写操作遵循服务端自己的权限和回滚规则。

## 验收记录

Story 01～03 本地实现和离线验证完成。OpenAPI 路由覆盖 88/88，1046 项联合回归和全仓 Ruff 检查通过。真实项目鉴权和写操作待线上验收。[实现与验证记录](../../docs/changelogs/paratranz-openapi-070/story-01-to-03/2026-11-16-001-openapi-070.md)

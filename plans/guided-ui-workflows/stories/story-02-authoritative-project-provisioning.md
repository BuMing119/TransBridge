# Story-02：权威的插件/空工程原子建项用例

- **所属计划**：[guided-ui-workflows](../plan.md)
- **状态**：草稿
- **需求**：FR26.3、FR26.14、NFR2.1
- **架构**：ADR-018、ADR-021

## 目标与验收边界

本 Story 原样承接 plan S02：建立 application 层唯一建项入口，覆盖插件驱动与显式空工程；在完整校验和 staging 提交前，不改变 repository 可见状态、active pointer 或 GUI projection。UI 不得继续调用 `ProjectHandle.create()` 形成 V2 旁路。

完成标准以 plan S02 的五项验收标准为准；本文件只细化接口、数据流和实施顺序，不扩展产品范围。

## 当前调用链与缺口

- `ProjectCoordinator.new_project()` 在 `uses_authoritative_projection` 下直接拒绝，旧分支才调用 `ProjectHandle.create()`。
- `GuiProjectCommandFacade` 提供 switch/save/create_variant/delete_variant，但没有 create_project。
- `ProjectLifecycleService.prepare_transition()/commit_transition()` 可以安全切换已存在候选，却不创建 Project/Variant/source baseline。
- `RepositoryLifecycleUnitOfWorkFactory` 和 `ProjectLifecycleTransactionStore` 已提供 lifecycle staging/commit/rollback，可扩展而不另建事务体系。
- `build_persistence_v2_services()` 是 repository、lifecycle、facade 和 projection rebuild 的组合根。

## 计划新增合同

名称为计划符号，实施时允许在不改变语义的前提下微调：

- `ProjectProvisioningRequest`：冻结值对象；包含 `project_name`、`default_variant_name`、零或一个主 source、迁移来源集合、解析选项摘要和 request fingerprint。
- `ProjectSourceRequest`：规范化位置、format hint、预期 fingerprint；不携带已解析的可写业务对象。
- `ProjectProvisioningPreview`：校验/识别后的只读摘要、诊断、预计 source/entry 数量和可提交 token。
- `ProjectProvisioningService.prepare(request, context)`：执行无正式副作用的路径/schema/能力/源解析及迁移候选构建。
- `ProjectProvisioningService.commit(token, context)`：owner-bound、one-shot；在同一事务中写 Project、默认 Variant、baseline/catalog 和 active pointer。
- `GuiProjectCommandFacade.create_project(...)`：GUI 薄适配，只返回 `OperationResult`，不拼 DTO。

`prepare` token 必须绑定 owner、request fingerprint、旧 lifecycle generation 和候选 identity；重复 commit、foreign owner 或 generation 变化均拒绝。

## 数据与事件顺序

```text
UI draft
  -> GuiProjectCommandFacade.prepare_create
  -> ProjectProvisioningService.prepare
       normalize/validate -> parse source -> migration candidate -> preview token
  -> UI shows summary / edits draft
  -> GuiProjectCommandFacade.commit_create
  -> one UoW: Project + Variant + baseline + active pointer
  -> commit
  -> one project projection rebuild/event
```

解析结果只能作为 staging candidate；正式 Variant materialization 使用稳定 EntryKey/source namespace。提交失败时 rollback staging，并丢弃 token；原活动工程和 generation 不变。

## 实施步骤

1. 在 `application/projects/` 定义冻结 request/preview/token 与稳定诊断；先写纯合同测试。
2. 扩展 project provisioning ports，使 source parser/migration candidate、repository identity 检查和 UoW 可注入；禁止 application import Qt。
3. 在 persistence V2 扩展 lifecycle transaction store，保证 Project/Variant/baseline/catalog/active pointer 同事务发布；增加逐阶段 fault injection。
4. 实现 `ProjectProvisioningService` 的 prepare/commit/discard；复用 lifecycle generation、owner 和 projection publisher。
5. 把服务和 GUI facade 注册到 `build_persistence_v2_services()` 与 AppRuntime use-case registry。
6. 为 legacy “新建工程”提供仅转发新 facade 的兼容入口；删除 V2 分支的拒绝消息仅属于后续 S04 UI 接线。

## 文件变更清单

- 新增：`src/transbridge/application/projects/provisioning.py`
- 修改：`src/transbridge/application/projects/ports.py`、`gui_facade.py`、`__init__.py`
- 修改：`src/transbridge/persistence/project_lifecycle_uow.py`、`persistence/v2/lifecycle_transactions.py`
- 修改：`src/transbridge/bootstrap/persistence.py`、`bootstrap/composition.py`
- 新增：`tests/application/projects/test_provisioning.py`、persistence fault/integration tests

## 边界与错误处理

- 同名工程、规范化后同路径、未来 schema、损坏/不支持源在 prepare 阶段失败，无 repository 写入。
- 解析成功但持久化失败时不得保留仅有 Project 或仅有 Variant 的可见记录。
- projection publish 发生在 commit 后；发布失败记录诊断并允许从 repository 重建，不能回滚已原子提交的数据冒充未发生。
- 空工程明确使用 `source=None`，不能用不存在的虚假路径绕过校验。
- 非 ASCII、长路径、符号链接和路径越界沿用现有 I/O/path policy，不在本 Story 自定义另一套规则。

## 测试与回退

建议命令：项目 application tests、persistence V2 repository/lifecycle tests、bootstrap composition integration tests。

回退只撤销 GUI/composition 对新 use case 的接线；已创建的 V2 Project 仍是合法数据。不得回退到 UI 直接写 legacy 文件，也不得以双写 V1/V2 作为兼容方案。

## 未决问题

- 主 source 解析是 prepare 阶段全量完成，还是先生成受 fingerprint 约束的解析 artifact；实施前以现有 parser 成本基线选择，但两种方案都必须保持 commit 无重复解析副作用。

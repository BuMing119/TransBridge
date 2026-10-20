# Story-02：权威的插件/空工程原子建项用例

- **所属计划**：[guided-ui-workflows](../plan.md)
- **状态**：草稿
- **实现对齐**：2026-10-20，单源/空工程及多插件扩展；[QA 证据](../../../docs/test-reports/guided-multi-plugin-project-qa-2026-10-20.md)
- **需求**：FR26.3、FR26.14、NFR2.1
- **架构**：ADR-018、ADR-021

## 目标与验收边界

本 Story 承接 plan S02：application 层统一建项入口覆盖一个或多个原始插件与显式空工程；全部所选插件进入同一个 Project 和默认 Variant。在完整校验和 staging 提交前，不改变 repository 可见状态、active pointer 或 GUI projection。UI 不得调用 `ProjectHandle.create()` 形成 V2 旁路。

完成标准以 plan S02 和 FR26.3 验收场景为准；本文件按已实现接口描述维护边界，不将未运行的原生交互或大插件性能验证标记为通过。

## 当前调用链与模块职责

- `GuiProjectCommandFacade.prepare_create/commit_create/discard_create` 委托 `ProjectProvisioningService`，`consume_create_hydration` 交付一次性的来源快照。
- `provisioning_models.py` 定义不可变请求、预览、提交与 hydration 契约；`provisioning_sources.py` 负责来源准备、去重、迁移配对和 registry；`provisioning.py` 保留两阶段服务及原公开 import。拆分避免继续扩张原超长模块。
- `TranslationIoProjectSourcePreparer` 通过现有 I/O 用例解析每个来源；原始插件均使用 `primary` 角色，只有已有译文使用 `migration`。
- `ProjectLifecycleService.commit_provisioning` 和既有 UnitOfWork 统一发布 Project、Variant、所有 baseline、catalog 与活动指针。
- `build_persistence_v2_services()` 继续作为组合根；本扩展不新增持久化 schema 或事务旁路。

## 现行合同

- `ProjectProvisioningRequest`：包含 `project_name`、`default_variant_name`、兼容单源的 `source`、其余原始来源 `additional_sources`、`migration_sources` 与 `parse_options`。`sources` 按原顺序返回全部原始来源；request fingerprint 包含额外来源。
- `ProjectSourceRequest`：规范化位置、format hint、预期 fingerprint；不携带已解析的可写业务对象。
- `ProjectProvisioningPreview`：校验/识别后的只读摘要、诊断、预计 source/entry 数量和可提交 token。
- `ProjectProvisioningService.prepare(request, context)`：执行无正式副作用的路径/schema/能力/源解析及迁移候选构建。
- `ProjectProvisioningService.commit(token, context)`：owner-bound、one-shot；在同一事务中写 Project、默认 Variant、baseline/catalog 和 active pointer。
- `GuiProjectCommandFacade.prepare_create/commit_create/discard_create`：GUI 薄适配，只返回 `OperationResult`，不拼 DTO；桌面单次确认在内部串联 prepare 与 commit。
- `ProjectProvisioningHydration`：`source` 保留兼容，`additional_sources` 保存其余快照，`sources` 提供全部独立基线来源，包含未折叠的迁移来源；配对合并的迁移副本不重复展示。仅成功提交后可由原 owner 消费一次，UI 不再次解析。

`prepare` token 必须绑定 owner、request fingerprint、旧 lifecycle generation 和候选 identity；重复 commit、foreign owner 或 generation 变化均拒绝。

## 数据与事件顺序

```text
UI draft（全部插件 + 可选已有译文）-> 用户点击一次“确定”
  -> GuiProjectCommandFacade.prepare_create
  -> ProjectProvisioningService.prepare
       parse all original sources -> parse migrations
       -> reject duplicate paths/identities -> fold matching translations
       -> one preview token
  -> GuiProjectCommandFacade.commit_create
  -> one UoW: Project + Variant + all baselines + catalog + active pointer
  -> commit
  -> one project projection rebuild/event
  -> consume_create_hydration -> 后台准备完整来源并整体替换工作台（不重复解析）
```

解析结果只能作为 staging candidate；正式 Variant materialization 使用稳定 EntryKey/source namespace。提交失败时 rollback staging，并丢弃 token；原活动工程和 generation 不变。

## 维护与验证步骤

1. 变更请求字段时同步 fingerprint 和单源调用兼容测试；不得将额外原始来源转存为 `migration_sources`。
2. 准备所有来源后按 namespace 校验；一个原始插件只允许与一个同身份的插件译文配对，多个原始插件共享身份或多个译文候选必须拒绝。
3. 将所有来源基线保存在同一候选中，仍由现有 owner/generation/token 与 UnitOfWork 保护一次提交。
4. 保留完整 hydration 及源身份，验证原始文本不被误标为译文、多个迁移对不串联、提交后不重复解析。
5. 运行后续插件损坏与版本/catalog/指针写入故障测试，再验证保存重开后全部来源与词条保持一致。

## 文件落点

- 应用：`src/transbridge/application/projects/provisioning.py`、`provisioning_models.py`、`provisioning_sources.py`。
- 复用：`gui_facade.py`、`src/transbridge/persistence/project_provisioning.py`、既有生命周期 UnitOfWork 与 bootstrap；本次未改这些复用模块。
- 验证：`tests/application/projects/test_provisioning.py`、`tests/persistence/v2/test_project_provisioning.py`、`test_project_provisioning_multiple_sources.py`。

## 边界与错误处理

- 同名工程、规范化后同路径、重复原始插件身份、损坏/不支持源在 prepare 阶段失败，无 repository 写入；来源解析诊断保留原 code/category/cause，并附带失败文件。
- 解析成功但持久化失败时不得保留仅有 Project 或仅有 Variant 的可见记录。
- projection publish 发生在 commit 后；发布失败记录诊断并允许从 repository 重建，不能回滚已原子提交的数据冒充未发生。
- 空工程明确使用 `source=None` 且 `additional_sources=()`；`source=None` 但额外来源非空时仍属于带来源的请求。不能用不存在的虚假路径绕过校验。
- 非 ASCII、长路径、符号链接和路径越界沿用现有 I/O/path policy，不在本 Story 自定义另一套规则。

## 测试与回退

聚焦命令：`uv run pytest tests/application/projects/test_provisioning.py tests/persistence/v2/test_project_provisioning.py tests/persistence/v2/test_project_provisioning_multiple_sources.py -q`。扩展命令：`uv run pytest tests/application/projects tests/persistence/v2 -q`。实际运行参数、结果及环境限制见关联 QA。

如回退多选入口，已创建的 V2 多来源 Project 仍是合法数据，必须继续允许打开；不得回退到 UI 直接写 legacy 文件，也不得以双写 V1/V2 作为兼容方案。

## 未决问题

- 本次契约无未决项：所有来源在 prepare 阶段解析并保留不可变 hydration，commit 不重复解析。真实大插件的端到端耗时尚未测量，后续性能优化必须保留工作台整体替换的原子性。

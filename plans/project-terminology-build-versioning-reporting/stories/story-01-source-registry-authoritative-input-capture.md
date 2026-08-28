# Story 01：Project 来源注册、关系图迁移与权威输入捕获

## 所属 Plan

[项目全来源术语构建、版本管理与统一报告计划](../plan.md)

## 状态

草稿（长期 schema 实施受 ADR-034 接受门禁约束）

## 目标

把 Project 的宽松 source 字典升级为稳定来源注册和显式关系图，并在 lifecycle/repository 的同一一致性边界捕获不可变 `BuildInputSnapshot`；构建范围不依赖 UI 或相邻文件猜测。

## 原始验收标准

- [ ] Project schema 使用受校验 `SourceRegistration` 与独立 `SourceRelation`；每个来源具有稳定、与内容 fingerprint 分离的 `source_id`、`enabled`、`format_id`、规范化位置、来源种类、双语能力、可选插件作用域和格式选项。
- [ ] `translation_for`、`localized_member_of` 关系具有稳定 `relation_id`、from/to、对齐 policy/version；N:M 关系可表达，歧义不自动选择。
- [ ] V2 `primary/migration` 项迁移后获得稳定项目内 ID；只有可证明唯一的关系自动建立，无法证明的项保留且产生待配置诊断。迁移失败保留已验证备份并不覆盖原 Project。
- [ ] `capture_build_input()` 在同一生命周期/repository 一致性边界内返回不可变 `BuildInputSnapshot`，包含 ADR-034 指定的 Project/Variant revision、排序后的来源/关系、受控 source snapshot/lease、actual fingerprint、adapter/capability、配置 digest、effective version、draft identity/base/revision/decision digest。
- [ ] 未打开 Project、无激活 Variant、无启用来源、需要关系但缺失、多个可能目标或 adapter capability 不足时返回结构化诊断；不读取 `AppContext`。
- [ ] FR5.16 的插件解析显式禁止 sibling `Strings/` 自动发现；来源在指纹捕获后变化则结果标记 stale/failed，不把另一份路径内容当同一快照。

## 当前实现事实

- `ProjectDto.envelope.data` 的 schema version 目前为 2；`sources` 仅校验为对象数组，迁移入口只有 `migrate_v1()`，尚无逐版本 migration chain。
- `ProjectSourceRequest`、`PreparedProjectSource` 和 `ProjectProvisioningService._prepare_sources()` 只表达 `primary/migration`；`TranslationIoProjectSourcePreparer.prepare_source()` 仍可能把 fingerprint 派生 namespace 当 `source_id`。
- `ProjectRepository.save_if_revision()`、`ProjectLifecycleService.active()/generation`、`VariantAggregate.snapshot()`、`VariantSnapshot`、`SourceFingerprint` 和完整 `EntryKey` 是现有一致性基础。
- plan 中的 `PluginFormatAdapter` 在现代码对应 `SsePluginAdapter`；其 `parse()` 与底层 `PluginParser.parse_plugin()` 都会发现 sibling `Strings/`。
- `RepositoryPaths` 已提供 root guard、backup、quarantine 和 staging，但没有 Project terminology root。

## 关键接口与数据流

计划新增：

- `application/projects/source_registry.py`：`SourceKind`、`BilingualCapability`、`SourceRelationKind`、`SourceRegistration`、`SourceRelation`、`SourceRegistrySnapshot`。
- `application/terminology/input_capture.py`：`CapturedSource`、`BuildInputSnapshot`、`TerminologyBuildInputPort`、`SourceLease`、`SourceLeasePort`。
- persistence migration：`migrate_v2_to_v3()` 与 `migrate_to_current()`；旧 `migrate_v1()` 保持兼容。
- plugin parse option：默认允许 sibling discovery，FR5.16 请求显式关闭。

```text
ProjectLifecycle/Repository lock
  -> clone Project registration/relation + complete Variant snapshot
  -> acquire immutable source lease/blob and actual fingerprint
  -> resolve adapter/version/capability without parsing UI collections
  -> pin effective/draft baseline and config digest
  -> canonical sort
  -> immutable BuildInputSnapshot
```

## 实施步骤

1. 定义 registration/relation 值对象、canonical ordering 和校验：稳定 ID、规范位置、format/options、双语能力、作用域、悬空/重复/自引用/环 policy。
2. 将 Project schema 升至下一版本，严格验证 `sources` 并新增 `source_relations`；不改变 Variant/Session envelope 的兼容行为。
3. 实现 V2→新版本迁移：分配稳定项目内 ID，旧 namespace/fingerprint 仅保留为追溯；只在关系唯一可证明时自动建边。
4. repository load 改为逐版本迁移链，继续使用 verified backup、staging、quarantine；迁移失败不覆盖原 Project。
5. 让 application/persistence provisioning 委托 source registry 构造器，旧请求 facade 保持签名和 `primary/migration` 映射。
6. 在 lifecycle/repository 一致性边界实现 `capture_build_input()`，一次捕获 Project、Variant、来源/关系、effective/draft 和 leases；禁止读取 `AppContext.slots`。
7. 对每个启用来源验证 read capability、actual digest 与 lease；捕获后摘要变化标 stale/failed。
8. 为 `SsePluginAdapter` / `PluginParser` 增加兼容默认的 sibling 控制；FR5.16 传 false，登记的 STRINGS 经 `LocalizedStringsAdapter` 和关系图单独进入。

## 文件与测试

计划新增 `source_registry.py`、`input_capture.py`、source registry/migration/input capture 测试；计划修改 `persistence/v2/schema.py`、`migration.py`、`repository.py`、两层 provisioning、`application/io/legacy_adapters.py` 和 `parser/plugin_parser.py`。

建议命令：

```powershell
uv run pytest tests/application/projects tests/persistence/v2/test_project_source_registry_migration.py tests/application/terminology/test_input_capture.py tests/contracts/io/test_legacy_format_adapters.py tests/parser/test_plugin_parser.py -q
```

## 边界、风险与回退

- `source_id` 不得继续由内容摘要生成；文件名、目录相邻或 master 引用都不是关系证据。
- lease 如何喂给只接受路径的 legacy adapter 必须在实现中固定为不可变 blob/lease path 或显式 snapshot 输入；不能摘要后重读原路径。
- 普通 plugin parse 默认行为保持 sibling discovery，避免破坏既有入口。
- ADR-034 未接受前不落长期 schema/default composition；若迁移失败，保留 verified backup 并进入只读/待配置状态。

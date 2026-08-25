# ADR-024：Project catalog 作为可重建派生索引

- **状态**：已接受并实现
- **日期**：2026-08-25
- **对应需求**：FR19.8、NFR2.1、NFR4.1
- **关联 ADR**：ADR-018、ADR-022

## 背景与约束

V2 Project 记录是工程本体，`project-catalog.json` 只保存工程 ID、显示名称和名称唯一性键，供创建查重与开始中心目录投影使用。现有创建事务会写 catalog，但普通打开和活动工程恢复只更新 `active-project.json`。因此，catalog 功能引入前已存在的 Project，或用户误删 catalog 后，可能出现“合法 Project 仍在、目录索引缺失”的兼容形态。

当前 `V2ProjectCatalog` 是明确的只读查询边界。它直接解析和验证 Project 记录，避免调用可能迁移、备份或隔离数据的 `ProjectRepository.load()`。ADR-018 又要求损坏或不可迁移数据保留现场并生成诊断，不得当作空数据覆盖。修复机制必须同时保持这两项约束。

桌面组合根在返回 Persistence V2 服务前已经拥有 root、文件系统 adapter 和 `ProjectRepository`，此时 UI、生命周期命令和目录查询尚未暴露，是执行一次启动维护的最小并发窗口。

## 决策

### 1. 独立的启动期修复服务

新增 Qt 无关的 `ProjectCatalogRepairService`，公开 `repair_if_missing()` 并返回不可变报告。它在 `build_persistence_v2_services()` 创建 `ProjectRepository` 后、暴露 lifecycle/query 服务前执行一次。

修复服务不实现 `ProjectCatalogQuery`，`V2ProjectCatalog.list_projects()` 继续保持完全只读。修复失败不得阻断应用启动；组合结果保留修复报告，现有活动工程只读兜底仍可工作。

### 2. 只有“文件缺失”触发自动重建

- catalog 存在且合法：不枚举 Project，不写盘。
- catalog 存在但 JSON、schema 或条目合同无效：保留原始字节并返回阻塞诊断，不自动覆盖。
- catalog 不存在且没有合法 Project：不创建空索引，保持全新安装行为。
- catalog 不存在且存在合法 Project：扫描、验证并重建。

“语法合法但人为遗漏条目”的 catalog 不属于自动缺失修复；未来若提供显式全量重扫，应先备份现有 catalog，并作为独立命令设计。

### 3. Project 记录发现与验证合同

文件系统 port 增加“列举一个目录的直接子文件”能力。修复仅扫描持久化根下 `projects` 的直接 `*.json` 文件，不递归进入 Variant 目录，也不读取 `workspace.json`、`current-workspace.json` 或其他历史索引作为权威来源。

每个候选依次满足：

1. canonical real path 仍位于授权 root；
2. strict UTF-8 JSON 可解析；
3. `schema_version` 等于当前 V2，`entity_type` 为 Project；
4. 内部 ID 可构造 `ProjectId`；
5. 候选路径与 `ProjectRepository.path_for(ProjectRef(id))` 完全一致；
6. `validate_v2(document, ref)` 通过全部 schema、身份和引用语义；
7. 显示名称经 trim 后长度为 1～80，且不包含 CR、LF、TAB；
8. `name_key = trimmed_name.casefold()` 在全部合法候选中唯一。

单个损坏、不可读、V1、未来 schema、错误 entity type、ID/路径不匹配或路径逃逸候选只产生安全诊断并被跳过，不迁移、不隔离、不改写源记录。两个合法记录若产生相同 `name_key`，整次自动重建停止，不擅自重命名、覆盖或挑选其中一个。

### 4. 确定性 catalog 与原子发布

重建结果继续使用 catalog schema 1：

```json
{
  "schema_version": 1,
  "projects": {
    "<project-id>": {
      "name": "<trimmed display name>",
      "name_key": "<casefolded name>"
    }
  }
}
```

条目按 Project ID 稳定序列化。将 lifecycle 内现有的 root-confined 原子 JSON 写入能力抽成共享持久化组件，供 provisioning 和 repair 复用：写 staging、复读字节校验、写前再次确认 catalog 仍缺失、原子 replace、发布后按 catalog 合同复读验证、尽力清理本次 staging。

修复只允许发布 catalog；不得修改 Project、Variant、active pointer、workspace、baseline 或 session 数据。任何写入/校验失败后 catalog 保持缺失或是完整的新文件，下次启动可重试。

### 5. 可观测结果

修复报告至少区分：无需修复、无可恢复记录、已重建、冲突阻塞、I/O/发布失败；包含恢复数量、跳过数量和稳定诊断码。报告保存在 `PersistenceV2Services` 中供日志、测试和未来支持入口使用，不复制成第二套可写目录状态。

## 备选方案

### 在 `list_projects()` 中发现并写回

调用时机最直接，但破坏只读 Query 合同，使 UI 渲染触发不可预期写盘，并与现有“查询不得迁移或隔离”测试冲突，拒绝。

### 在打开活动工程时顺便补一条 catalog

只能恢复当前工程，无法发现其他合法记录；活动指针损坏时也无能为力，并把派生索引维护混入项目激活事务，拒绝。

### 只提供手动“重建目录”

可让用户决定是否覆盖，但无法满足误删文件后自动恢复的目标。保留为未来处理“catalog 存在但内容不完整”的显式维护能力，不替代本次启动自愈。

### 从 legacy workspace/current-workspace 重建

这些文件不是 V2 Project 的权威状态，字段和生命周期不同，可能引用已删除或旧格式记录。基于它们重建会把历史投影误当作工程本体，拒绝。

## 影响与风险

- 正面：误删 catalog 后所有合法本地工程可在下次启动自动恢复；Project 本体和活动指针不被修改。
- 正面：查询只读性、损坏现场保护、路径约束和 schema 验证保持不变。
- 成本：文件系统 port 增加目录枚举；原子根文档能力需要从 lifecycle 私有实现抽取为共享组件。
- 风险：大量 Project 文件会增加一次启动扫描，但只在 catalog 缺失时发生，且仅枚举顶层小型 Project 元数据，不进入大型 Variant 数据。
- 风险：当前应用没有跨进程 catalog 锁。组合期执行和写前复查可消除同进程竞态，但不能为两个进程同时对同一持久化根 provisioning/repair 提供强一致性。真正的多进程支持需要所有 catalog 写入方共享跨进程锁或 compare-and-swap primitive；本 ADR 不扩大为全应用多实例改造，也不宣称具备该保证。

## 迁移与回退

无需修改 Project、Variant、active pointer 或 catalog schema。部署后首次启动若 catalog 缺失，将从合法 V2 Project 记录生成 schema-1 catalog；已存在 catalog 完全不变。

回退代码不会破坏已生成的 catalog，因为它与现有 provisioning 写出的格式一致。若修复发布失败，删除本功能自己的 staging 残留并保持 catalog 缺失；现有活动工程只读兜底继续可用。若生成后的 catalog 被人工判定有误，可在应用关闭时移走该派生索引，保留所有 Project 本体，再使用显式维护流程重建。

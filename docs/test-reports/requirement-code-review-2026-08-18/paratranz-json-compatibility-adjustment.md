# ParaTranz JSON 双 ID 兼容性：审查结论调整

- 调整日期：2026-08-18
- 用户提供样本：`C:/Users/admin/Downloads/Skyrim_1.json.json`
- 样本规模：11,933,771 bytes，32,372 条
- 本文件用于修订本审查目录中的架构与 Plan 建议，不表示代码已经实现

## 1. 用户的真实目标

主要兼容对象不是 DSD JSON，也不是 TransBridge 项目持久化 JSON，而是 ParaTranz 的词条交换格式：

```json
{
  "id": 918495206,
  "key": "Player:00000007|1~NPC_:FULL",
  "original": "Prisoner",
  "translation": "囚犯",
  "stage": 1,
  "context": "NPC_:FULL"
}
```

ParaTranz 会生成或重写数值 `id`。用户自己的词条 ID 必须放在 `key` 中，并在文件经过 ParaTranz 后仍能恢复。产品必须支持：

1. 将本地集合导出为这种 ParaTranz JSON；
2. 将 ParaTranz 导出的 JSON 重新导入；
3. 导入时不能用 ParaTranz 数值 `id` 覆盖本地稳定 ID；
4. 导出→ParaTranz 重写 `id`→导入后，仍按 `key` 找回同一条本地词条。

## 2. 对既有审查结论的校正

原审查多处把 `id != key` 作为需要消除的异常。这个结论需要收窄：

- 在 TransBridge 内部，业务关联键应统一使用 stable `key`，这是正确的。
- 在 ParaTranz 交换文件中，`id != key` 是正常且必要的：`id` 属于 ParaTranz，`key` 属于用户/TransBridge。
- 不应通过“导入后强制让所有外部字段 id == key”来假装消除差异。
- 正确做法是建立 typed identity boundary，明确两个 ID 的所有者和生命周期。

因此，后续 ADR/Plan 应从“把 id/key 合并成一个字段”调整为“内部主键唯一，但外部系统 ID 作为 namespaced reference 保存”。

## 3. 推荐身份合同

建议领域层至少区分：

```text
EntryKey
  value: str
  owner: TransBridge / user
  role: 本地唯一业务主键、合并键、版本键、标签键、任务结果键

ExternalEntryRef
  system: "paratranz"
  value: int | str
  project_id: optional
  file_id: optional
  role: 远端 API 定位和诊断，不参与本地集合唯一性
```

兼容当前 `TranslationEntry` 的渐进方案：

- `entry.key`：继续作为唯一主索引；
- 历史 `entry.id`：迁移期可保留，但内部新代码不得把它解释成 ParaTranz ID；
- ParaTranz 数值 `id`：进入 `external_refs["paratranz"]` 或等价的明确字段；
- JSON adapter 负责在外部 `id/key` 与内部 `EntryKey/ExternalEntryRef` 之间映射。

不推荐仅增加一个含糊的 `remote_id` 字段，因为未来可能同时存在多个 ParaTranz 项目、文件或其他平台。至少应带 `system`，最好带 project/file scope。

## 4. 文件适配器合同

应把 JSON 格式明确拆成不同 Adapter，而不是都走一个含糊的 `from_json_file()`：

1. `TransBridgeCollectionJsonAdapter`
   - 用于完整内部集合/调试交换；
   - 可包含 DSD、plugin、source metadata。

2. `ParaTranzJsonAdapter`
   - 固定处理 `id/key/original/translation/stage/context`；
   - `key` 是必需的 stable EntryKey；
   - `id` 是可选/不可信的外部引用；
   - 导入时绝不能把数值 `id` 设为集合主键。

3. `DsdJsonAdapter`
   - 处理 `form_id/type/string/...`；
   - 与 ParaTranz JSON 完全不同，不能依靠异常回退互相猜测。

### 导入规则

```text
external key -> EntryKey / collection primary index
external id  -> ExternalEntryRef(system="paratranz")
original     -> original
translation  -> translation
stage        -> validated ParaTranz stage enum
context      -> context
```

- `key` 为空或缺失：默认拒绝导入并给出逐条错误；不能静默使用远端 `id` 代替用户 ID。
- `key` 重复：输出冲突报告，默认拒绝覆盖；只有显式 overwrite policy 才允许覆盖。
- `id` 缺失：仍可导入，因为本地身份只依赖 `key`。
- `id` 类型为 int/string：均作为 opaque external value，不参与数值运算。
- 未知字段：建议保留到 adapter metadata，至少不能导致核心字段导入失败。
- Stage 只接受 `-1/0/1/2/3/5/9`；未知值进入 warning/error policy，不做大小比较。

### 导出规则

- `key` 必须写入内部 stable EntryKey；绝不能写 ParaTranz 数值 ID。
- `id`：若具有同 project/file scope 的 ParaTranz external ref，可原样写回；否则可以省略，或按 ParaTranz 已验证的创建文件合同输出占位值。
- `original/translation/stage/context` 按格式写出。
- 默认只输出 ParaTranz 认识的字段；TransBridge 私有 metadata 不混入标准文件，除非通过 namespaced extension 且已验证平台会保留。

## 5. UI 与 Agent 行为

- GUI 导入必须提供明确的“ParaTranz JSON”类型；可以有自动识别，但识别结果要展示给用户。
- GUI 写回/导出目标增加“ParaTranz JSON”。它不是 DSD JSON，也不是 `.transbridge` 项目导出。
- 导入结果报告：总数、成功、重复 key、缺 key、未知 Stage、保留的 external id 数量。
- 导出结果报告：总数、带远端 id、无远端 id、跳过与错误。
- Agent/MCP 提供 typed `import_paratranz_json` / `export_paratranz_json`，不要继续让含糊的 `import_json` 同时猜三种格式。
- 所有入口必须调用同一个 `ParaTranzJsonAdapter`，不能在 UI/Agent/同步 Workflow 各写一套字段映射。

## 6. 与 ParaTranz API 同步的关系

文件导入导出与在线同步应共享同一字段映射，但保持不同 Use Case：

- 文件交换：本地 path → adapter → collection/change set；
- 在线同步：Gateway response → adapter/DTO → merge plan；
- 两者都以 `key` 合并；
- ParaTranz `id` 仅在需要调用远端词条 API 时使用；
- project_id/file_id/entry_id 必须组成有 scope 的外部引用，避免把 A 项目的数值 ID 用于 B 项目。

## 7. 必须新增的测试与验收

以用户样本结构建立可公开的小型脱敏 fixture，并增加：

1. `id` 为数值、`key` 为字符串时，导入后集合按 `key` 建索引。
2. 导入后 local identity 不等于/不依赖 ParaTranz 数值 `id`。
3. 导出→模拟 ParaTranz 重写全部 `id`→再导入，EntryKey 集合和译文保持一致。
4. 仅改变远端 `id`，collection digest 不变。
5. 两条记录远端 `id` 相同但 `key` 不同：仍是两个本地条目，并报告异常 external ref，而不是覆盖。
6. 两条记录 `key` 相同：默认冲突，不静默保留最后一条。
7. 缺 `key`：明确失败；不能回退到 ParaTranz `id`。
8. 缺 `id`：可成功导入。
9. Stage `-1/0/1/2/3/5/9` round-trip；未知 Stage 按策略处理。
10. 32,372 条/约 12MB 样本的导入导出基准，条目数和 key 去重统计一致。
11. GUI、Agent、MCP 对同一 fixture 产生相同 digest 和错误报告。
12. 在线下载和离线 JSON 导入对同一词条集合生成相同 ChangeSet。

## 8. 对迭代优先级的影响

此要求应提前到 Phase 0，而不是等完整 ParaTranz Sync Service 才做，因为它是用户主要使用场景，也是验证 EntryKey 合同的最小端到端路径。

建议 Phase 0 增加一个独立 Story：

### `Sxx ParaTranz JSON Identity Adapter`

- 定义 EntryKey/ExternalEntryRef 映射；
- 实现显式 import/export adapter；
- GUI 导入/导出入口；
- Agent/MCP typed schema 可先排入后续 Story，但必须共用 adapter；
- 用户样本结构 round-trip fixture；
- 不依赖完整 ParaTranz 网络 API。

它应成为 `translation-io-kernel-v2` 与 `paratranz-sync-service` 的共同前置依赖。

## 9. 修订后的核心判断

ParaTranz 生成 `id` 本身不是缺陷，真正的缺陷是当前系统没有把 ID 所有权建模为正式合同。后续优化目标不是阻止 ParaTranz 改写 `id`，而是确保用户的 stable ID 永远存放在 `key`、内部所有业务关联都使用 `key`，并把远端 `id` 限定在有 scope 的外部引用中。

---

## 整改回填（2026-08-18，Phase 6）

本报告为综合整改正式输入审查结论，保留历史判定与证据不改写。Phase 0～7 已完成，对应根因（R-xxx）由各 V2 Plan/Story 承接并通过 EvidenceManifest 与综合 QA；完整根因→Story→evidence 追踪见 [remediation-ledger](./remediation-ledger.md)，最终汇总见 [final-release-qa-2026-08-18](./final-release-qa-2026-08-18.md)。综合整改 V2 共 37/37 Story 实现完成并通过综合 QA；最终锁定 uv 门禁合计 1374 passed、5 skipped、0 failed。

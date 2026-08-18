# ADR-017：Translation I/O Kernel V2、双层身份与原子发布

- **状态**：已接受（2026-08-18）
- **日期**：2026-08-18
- **对应需求**：FR18、FR22.1、FR23.3、NFR2.1、NFR3.1、NFR5.1
- **关联 ADR**：ADR-001、ADR-002、ADR-006、ADR-014、ADR-015、ADR-016
- **承接根因**：R-013～R-020、R-040～R-042、R-047、R-049

## 背景与约束

现有 parser/writer 构造和方法签名不统一，Agent dispatch 依赖不存在的模块或无参构造；`CollectionSlot` 未保存完整 parse/write context；内部 JSON、DSD JSON 与 ParaTranz JSON 共用模糊入口；Localized Strings 写出只收集有译文条目，无法证明未修改 string_id 得到保留。

ADR-001/002 已确立统一 `TranslationEntry` 和以 `key` 为主索引的 Collection，这一方向保留。但历史上 `id == key` 的假设、来源特有字段允许丢失、直接 mutation 和 Writer 直接写目标文件不满足已确认 FR18。

## 决策

### 1. FormatAdapter 是格式边界的唯一公共端口

Application 层仅依赖统一 `FormatAdapter` 合同，不直接依赖具体 parser/writer：

```text
probe(request) -> FormatProbe
parse(ParseRequest) -> ParseResult
validate_write(WriteRequest) -> ValidationResult
write(WriteRequest) -> WriteResult
capabilities() -> FormatCapabilities
```

`FormatCapabilities` 分别声明 read、write、round-trip、localized、streaming、cancel 和 fidelity 等能力；不得从扩展名或类存在性推断支持。

Adapter 按稳定 `format_id` 注册，例如 `plugin.sse`、`xml.eet`、`xml.xt`、`json.paratranz`、`json.dsd`、`json.transbridge`、`sst.ssu8/9`。扩展名相同且内容歧义时，probe 返回候选与证据，由 use case 要求调用方选择。

### 2. ParseRequest 与 ParseResult

`ParseRequest` 至少包含输入引用、显式或候选 format_id、来源命名空间、解析选项、取消 token 和 RuntimeContext。

`ParseResult` 至少包含：

- `status`: `completed | partial | failed | cancelled`；
- `source_snapshot`：写回所需的源模板、完整字符串映射、编码/BOM、格式元数据和来源 fingerprint；
- 条目集合；
- 可定位 diagnostics、warnings 和统计；
- adapter/version/capability 摘要。

合法空文件返回 completed 且条目数为 0；格式错误、部分损坏和取消具有不同状态。Parser 不修改 AppContext、活动槽位或持久化状态。

### 3. EntryKey 与 ExternalEntryRef 分离

统一条目继续使用 `TranslationEntry` 作为领域载体，但身份分成两层：

- `EntryKey`：`source_namespace + local_key` 的稳定内部身份；source namespace 至少区分项目源文件或内容 fingerprint，避免多源碰撞。
- `ExternalEntryRef`：`provider + scope + opaque_id`，保存 ParaTranz 等外部系统引用；一个条目 MAY 有多个外部引用。

历史 `id` 字段仅作为兼容 facade；新代码不得假设 `id == key`，不得解析远端 opaque ID 生成内部键。内容摘要、checkpoint 和 mutation 使用 EntryKey；远端 API 更新使用对应 ExternalEntryRef。

`TranslationEntry` 的 V2 持久化 envelope SHALL 同时保存：EntryKey、外部引用、original、translation、Stage、context、格式扩展 metadata、provenance 和 revision。来源特有字段进入有命名空间的 metadata，不得因统一模型而静默丢失。

### 4. Collection 是领域聚合，修改通过 mutation port

`TranslationEntryCollection` 保留为条目聚合与索引实现，但不再允许入口线程任意写 dataclass 字段。Application 层通过 mutation port 提交：

```text
apply(ChangeSet, expected_revision, run_id) -> MutationResult
```

ChangeSet 使用 EntryKey 定位，声明字段变化、provenance 和预期 revision；冲突返回结构化结果。成功提交后聚合 revision 单调递增并发布只读事件。Qt 信号、Task Monitor 或 UI 刷新是事件 adapter，不是聚合所有权。

兼容期允许 facade 将旧直接 mutation 包装为 ChangeSet；新 use case 不得新增直接 mutation。

### 5. ParaTranz JSON 双 ID Adapter

`json.paratranz` SHALL 独立于内部 JSON 和 DSD JSON：

- `key` 映射到 EntryKey.local_key；导入时结合来源 scope 构造完整 EntryKey。
- 可选 `id` 映射为 `ExternalEntryRef(provider="paratranz", scope=<project/file>, opaque_id=<原值>)`。
- 导出在 ref 存在时原样保留 `id`；不存在时省略，不按位置或 key 合成。
- `original/translation/context/stage/key/id` 和约定扩展字段按 schema 校验并可逆映射。
- 重复 key、冲突 id、类型错误和非法 Stage 形成 diagnostics；冲突策略由请求显式选择，不静默最后写入覆盖。

离线文件 Adapter 不调用网络；ParaTranz 网络同步通过 ADR-016 use case 复用相同 mapper 和 ExternalEntryRef，不复用文件 I/O 的副作用。

### 6. WriteRequest、StagePolicy 与 WriteResult

`WriteRequest` 至少包含 source snapshot 或明确新建模板、目标 format_id、条目快照、Variant revision、StagePolicy、冲突/备份/发布策略、取消 token 和 RuntimeContext。

StagePolicy 是离散矩阵：

- hidden(-1)：写原文，不进入 AI/TM 自动修改；
- locked(9)：不进入 AI/TM 自动修改；译文非空时写译文；译文为空时产生 fatal diagnostic，阻止正式发布；
- 其他 Stage 仅按显式枚举集合决定写原文或译文，不使用 `>=` 推断。

`WriteResult` 返回互斥终态、staging/正式 artifact refs、写入/跳过/阻断统计、fidelity 校验和 diagnostics。Writer 不返回“执行了”来代替发布成功。

### 7. Staging、验证与原子发布

所有正式写出遵循：

```text
render to staging
  -> format/schema/fidelity validation
  -> optional round-trip verification
  -> backup existing target when policy requires
  -> atomic replace/publish
```

失败或取消不改变正式目标。目标文件系统不支持单文件原子替换时，Adapter SHALL 报告受限能力并使用可恢复的目录级提交协议，不得悄悄降级为覆盖写。

Localized Strings 写回必须从完整 source snapshot 克隆 string_id 映射，只替换确定变更的 ID；未翻译、hidden 和非目标条目保持原值。正式发布前验证输入 string_id 集合与输出集合相等，除非请求明确允许新增/删除。

### 8. 支持矩阵和开放门禁

架构能力矩阵初始值：

| 格式/能力 | Read | Write | Round-trip | 入口级别 |
|---|---|---|---|---|
| ParaTranz JSON | 支持目标 | 支持目标 | P0 | GUI/Agent/MCP 共用 use case |
| ESP/ESM/ESL | 支持目标 | 支持目标 | P0 | GUI/Agent/FOMOD 共用合同 |
| EET XML | 支持目标 | 支持目标 | P0 | GUI/Agent 共用合同 |
| XT XML | 支持目标 | 支持目标 | P0 | GUI/Agent 共用合同 |
| Localized Strings | 支持目标 | 支持目标 | P0 完整性 | 通过 plugin snapshot |
| DSD JSON | 实验性 | 实验性或显式不支持 | P1 | 不宣称发布级 |
| SST Reader | 实验性 | — | P1 | SST Writer 关闭 |
| BSA/Strings 补全与完整上下文 | 渐进 | 渐进 | P1 | capability gate |

“支持目标”表示本 ADR 的验收目标，不表示当前代码已通过。只有合同语料、成功链和 fidelity 门禁通过后，capability 才能从 unavailable/experimental 提升为 supported。

## 备选方案

### 继续让每个入口适配 parser/writer

无法保证字段映射、Stage 和原子发布一致，拒绝。

### 为每种来源使用 TranslationEntry 子类

会让所有下游出现类型分支。选择统一 envelope + 命名空间 metadata，并把格式语义留在 Adapter。

### 以 ParaTranz id 替代内部 key

远端 ID 可缺失或重分配，无法稳定支撑本地项目、checkpoint 和多平台引用，拒绝。

## 影响与风险

- 正面：格式能力可审计；双 ID 不再互相污染；写出具备一致事务边界。
- 成本：需要 adapter wrapper、source snapshot 和 schema migration。
- 风险：EntryKey 加来源命名空间会改变现有序列化键。缓解：保留 legacy local_key，迁移时生成映射表，旧 facade 仅在唯一来源上下文中接受裸 key。

## 迁移与回退

1. 先实现 V2 合同和 legacy adapter，不修改现有入口。
2. ParaTranz JSON 作为首个完整 Adapter 固化双 ID 与诊断语义。
3. 依次包装 ESP/Localized Strings、EET、XT；每个 Adapter 独立通过 golden/round-trip/fault tests。
4. 入口切到 use case 后保留旧 parser/writer facade 委托 adapter。
5. 若某格式未过门禁，capability 保持 experimental/unavailable，并回退到原入口但不得宣称 V2 支持。

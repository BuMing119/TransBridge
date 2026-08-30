# Story 01：Typed ParaTranz 术语端口与规范映射

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17.5、FR5.17.6；ADR-027；S00 术语 HTTP 合同
- **前置依赖**：[Story 00](story-00-terms-contract-calibration.md)
- **下游调用方**：S03 planner、S04/S05 executor、S07 多入口 adapter

## 目标

在 application 边界提供术语专用的 typed ParaTranz port，把 raw HTTP 响应转换为稳定、可验证、可取消且不会泄漏凭据的远端术语快照和逐项写结果。该 Story 不决定本地权威、冲突、删除或同步 baseline。

## 原始验收标准

- `ParaTranzTerminologyPort` 分页读取完整术语库，并提供 create/update/delete；所有结果保留 remote ID、可用修订、只读字段、未知字段和安全诊断。
- ADR-027 `TermEntry` 与 remote DTO 的转换只提交 ParaTranz 可写字段，不回传项目、创建/更新时间等只读字段；未知字段在本地 remote snapshot 中保留但不盲写。
- 请求遵守现有 typed client 的认证、限流、Retry-After、超时、取消和 secret redaction；非幂等写不自动盲重试。
- 列表分页中发现目标或 revision 漂移时返回不稳定快照诊断，不生成可执行计划。

## 当前调用链与约束

- `ParatranzClient._request()` 是唯一应复用的网络核心，已区分 idempotent method、idempotency key、Retry-After、request ID 和 cooperative cancellation。
- `ParaTranzService` 展示了 typed facade 模式，但它只组合 project/string/history/export API；术语能力不得塞进现有 `ParaTranzPort` 的 translation-entry 语义。
- `ParatranzTermsAPI` 当前方法没有 cancellation 参数和 expected schema 约束；它可作为 endpoint adapter 被扩展，但分页/DTO 规则属于新 service。
- `TermEntry`/`term_entry_from_mapping()` 会保留未知字段到 `metadata`，`term_entry_to_paratranz_dict()` 已只输出服务端可写字段。

## 数据流

```text
ParaTranzTermsAPI raw response
        ↓  schema/wrapper/page validation
ParaTranzTermsService
        ↓  ADR-027 mapping + observed revision/digest
ParaTranzTermPage / ParaTranzTermSnapshot
        ↓
ParaTranzTerminologyPort（application consumer）
```

写路径为 `TermEntry/ParaTranzTermWrite → writable payload → raw API → ParaTranzTermWriteResult`。任何 response schema 错误都在 service 边界转换为 `ExternalServiceError(INVALID_RESPONSE)`。

## 计划新增的关键接口

- `ParaTranzTerm`：remote ID、canonical `TermEntry`、可选 server revision、`observed_digest`、只读 metadata。构造时验证 remote ID 和 canonical term/translation。
- `ParaTranzTermPage`：items、page/page_size、是否还有下一页、可选 snapshot revision/ETag、page digest。
- `ParaTranzTermSnapshot`：remote project identity、排序后的 items、聚合 digest、观测时间、稳定性状态和 diagnostics。
- `ParaTranzTermWrite`：明确的 create/update payload；update 必须带 remote ID 和预期 revision/digest（若合同支持）。
- `ParaTranzTermWriteResult`：operation、remote ID、服务端 revision/observed digest、request ID、confirmed/unknown 状态。
- `ParaTranzTerminologyPort`：`snapshot_terms()`、`create_term()`、`update_term()`、`delete_term()`；所有方法接受 `CancellationPort`，写方法不在 port 内做冲突策略。

这些符号均为计划新增，不应复用 `ParaTranzEntry`、`RemoteEntrySnapshot` 或 `SourceNamespace`。

## 依赖有序的实施步骤

1. 在 `application/ports/paratranz_terms.py` 定义冻结 DTO、enum 和 Protocol；所有远端 identity/digest 字段做空值、类型和 digest 格式校验。
2. 给 `ParatranzTermsAPI` 的 list/create/update/delete 增加 `cancellation` 透传和 `_request(expected_type=...)` 约束；保持旧位置参数调用兼容。
3. 新建 `ParaTranzTermsService`，复用 `ParaTranzService._items/_mapping/_typed` 的语义但不依赖其私有方法；如复用逻辑超过少量代码，抽成 `paratranz/response_mapping.py` 的无状态 helper。
4. 实现分页读取：检测重复页、重复 remote ID、页码不前进、上限超出和 S00 确认的 snapshot revision 漂移。没有服务端 revision 时聚合 canonical page digest。
5. 将每个 raw term 经 `term_entry_from_mapping(source="paratranz")` 转成 canonical `TermEntry`；缺少 term/translation 的记录不得被 adapter 静默丢弃，service 应返回 invalid response 或显式 skipped diagnostic（按 S00 合同结论）。
6. 写入前只调用 `term_entry_to_paratranz_dict()`；update/delete 必须使用已验证 remote ID。create POST 没有幂等合同则 `confirmed_idempotent=False`，timeout 直接上抛供 S04 reconcile。
7. 将 response request ID、revision/ETag 和 remote ID投影到 write result；空 body 的 update/delete 只能在 HTTP 成功且目标 identity 已知时标记 confirmed。
8. 在 `paratranz/__init__.py` 和 application ports facade 增加窄导出；不要把术语方法追加到旧 `ParaTranzPort`。

## 文件变更清单

- **新增** `src/transbridge/application/ports/paratranz_terms.py`：术语 DTO、snapshot 和 Protocol。
- **新增** `src/transbridge/paratranz/terms_service.py`：分页、映射、稳定性与写结果 adapter。
- **最小修改** `src/transbridge/paratranz/api/paratranz_terms_api.py`：取消和 typed request 参数。
- **可能新增** `src/transbridge/paratranz/response_mapping.py`：仅在 project/string/term service 确有共享需要时抽取。
- **最小修改** `src/transbridge/paratranz/__init__.py`、`src/transbridge/application/ports/__init__.py`：兼容导出。
- **新增** `tests/paratranz/test_terms_service.py`。
- **更新** `tests/contracts/paratranz/test_terms_api_contract.py`：S00 失败合同转为通过。
- **更新** `tests/ai_translator/test_term_formats.py`：remote metadata 和 writable payload round-trip。

## 边界条件与错误处理

- 空术语库是稳定空 snapshot；格式损坏、wrapper 不识别或缺少关键字段不是空库。
- remote ID 重复、同 ID 内容不一致或分页循环返回 `INVALID_RESPONSE/unstable`，不得只保留最后一项。
- 未知字段保存在 DTO 的只读 metadata；payload builder 只读取 ADR-027 可写字段。
- POST create 的 timeout/transport failure 是 unknown outcome；service 不自行重试，也不猜测 remote ID。
- PUT/DELETE 即使 HTTP 方法通常幂等，也只有在目标 remote ID 和 S00 合同证明语义稳定时才允许 client retry；否则交由 executor reconcile。
- 取消必须在每页和每次写前后检查；取消后迟到 response 仍由 S04 决定是否 reconcile，service 只返回/抛出 typed 状态。

## 测试策略与建议命令

- DTO：合法/非法 remote ID、digest、unknown metadata 深拷贝、稳定排序和 canonical hash。
- 分页：list wrapper 变体、空/末页、重复页/ID、页中变化、remote limit、取消。
- 写操作：create/update/delete payload、只读字段剔除、204/空 body、request ID、revision/ETag、timeout/429/401/403。
- 兼容：旧 `ParatranzTermsAPI` 调用签名、现有 `TermEntry` JSON/CSV/Excel/ParaTranz adapter 测试不回归。
- 建议命令：`uv run pytest tests/paratranz/test_terms_service.py tests/contracts/paratranz/test_terms_api_contract.py tests/ai_translator/test_term_formats.py -q`。

## 风险、回退与未决问题

- 如果 term API 没有全库 snapshot revision，`stable=False` 只能阻止 plan，不能通过重试掩盖；S03 每次计划都使用完整聚合 digest。
- 如果 create response 不返回 remote ID，S01 只能返回 unknown/needs-reconcile，S04 必须通过重新 list 匹配，不能将本地 term ID 作为 remote ID。
- 回退时可不注册 `ParaTranzTerminologyPort` capability；原 terms 管理 UI 和 legacy term source 继续使用 raw API，不删除新 DTO。

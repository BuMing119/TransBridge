# Story 00：锁定 ParaTranz 术语合同与产品校准

- **所属 Plan**：[项目术语库与 ParaTranz 备份/双向同步计划](../plan.md)
- **状态**：已确认
- **追溯**：FR5.17.5、FR5.17.6；ADR-023、ADR-027、ADR-034
- **后续 Story**：S01 typed 端口、S02 同步基线、S03 三方 planner

## 目标

在任何生产写逻辑出现前，以可重复的 HTTP 合同样本确认 ParaTranz 术语端点真实能力，并把用户已确认的三个产品/架构策略固化为可测试合同。该 Story 只产出合同证据和明确决策，不启用同步、不修改本地术语状态，也不要求普通测试环境访问真实 ParaTranz。

## 原始验收标准

- 以受控 HTTP fixture 或经脱敏保存的真实响应样本确认 list/create/update/delete 的分页结构、所有已知字段、remote ID、只读字段、错误响应、权限要求和删除返回语义。
- 明确接口是否提供远端 revision/ETag、条件写或幂等键；没有时记录 `observed_digest + observed_at + target identity` 的保守 fresh-check 方案。
- 本 Plan 的插件作用域、单 Variant 映射和受管删除三项建议被确认或替换为明确决策；任何替代方案仍满足无损/显式/可逆要求。
- 固定术语同步 fixture：纯回声、双方各自修改、远端新增、双方删除、remote ID 重用/缺失、独立远端项、插件特例、两个 Variant 冲突、分页中途变化和超时后未知结果。

## 前置依赖与受影响调用方

- 当前 raw 入口是 `ParatranzTermsAPI.list_terms/create_term/update_term/delete_term`，位于 `src/transbridge/paratranz/api/paratranz_terms_api.py`。
- `ParatranzClient._request()` 已提供 request ID、typed `ExternalServiceError`、Retry-After、取消检查和 secret redaction；合同测试应验证术语端点是否正确使用这些能力，不另造 HTTP stub 语义。
- `term_entry_from_mapping()` 和 `term_entry_to_paratranz_dict()` 已定义 ADR-027 的读写字段边界，可用来判定哪些响应字段是已知可写字段、服务端只读字段和未知字段。
- S01～S05 都依赖本 Story 产出的 response fixtures 和能力结论；若结论不稳定，后续不得把推测写成 production DTO。

## 合同证据结构

计划新增下列脱敏 fixture，每个 fixture 同时保存 HTTP status、有限响应 header、JSON body 和场景说明；不得保存 Authorization、Cookie、用户邮箱、真实项目名或用户翻译数据。

- `list-first-page.json`、`list-middle-page.json`、`list-last-page.json`：确认 wrapper key、分页参数和终止条件。
- `create-success.json`、`update-success.json`、`delete-success.json`：确认返回 body、remote ID、revision/header 和 204/空响应行为。
- `errors-401.json`、`errors-403.json`、`errors-404.json`、`errors-409.json`、`errors-429.json`、`errors-5xx.json`：确认错误分类与 retry metadata。
- `unknown-fields.json`、`missing-id.json`、`duplicate-id.json`：固定前向兼容与拒绝边界。
- `snapshot-changed-between-pages.json`：模拟分页期间 revision/ETag 或内容摘要变化。
- `timeout-after-write.json`：只描述客户端未知结果，不伪造服务端未执行。

每个术语记录至少覆盖 `term`、`translation`、`variants`、`caseSensitive`、`pos`、`note`、remote ID、创建/更新时间或其他实际只读字段。仅在真实响应证明存在时加入 revision/ETag/creator 等字段。

## 依赖有序的实施步骤

1. 在 `tests/contracts/paratranz/fixtures/terms/README.md` 写明采样来源、脱敏规则、端点版本、采样日期和禁止字段；fixture 内容必须可公开进入仓库。
2. 为 `ParatranzTermsAPI` 建立受控 `requests.Session`/HTTP server 测试，记录 GET/POST/PUT/DELETE 的 path、query、body、cancellation 和 response headers；不要依赖网络。
3. 用可选 `integration` smoke 对专用空测试项目采样实际响应。没有凭据时显式 skip，不把“未运行”解释为合同已证实。
4. 比较 raw 响应与 ADR-027 adapter：列出可写字段、只读字段、未知字段、remote identity 和任何服务端修订证据。
5. 固定分页终止和稳定性检查规则。若没有全局 snapshot token，明确使用每页 canonical digest 聚合、重复 ID/页检测和执行前完整重读。
6. 对 create/update/delete 分别记录幂等语义。POST 没有服务端幂等键时标记为“未知结果必须 reconcile”，不得授权自动 retry。
7. 将用户于 2026-08-30 已确认的三项结论——插件特例固定跳过、单目标只激活一个 Variant、只删除有 baseline 证明的受管项——固化到 fixture、profile 和 planner 合同。Story 状态为“已确认”仅表示详细设计获批，不代表该校准实现或合同测试已完成。
8. 从确认后的合同生成 S01 typed DTO 的输入/输出断言和 S03 golden scenarios；后续实现只能扩大未知字段保留，不得悄悄放宽身份或删除规则。

## 文件变更清单

- **新增** `tests/contracts/paratranz/fixtures/terms/README.md`：fixture 来源、脱敏和版本说明。
- **新增** `tests/contracts/paratranz/fixtures/terms/*.json`：成功、错误、分页、漂移和未知结果样本。
- **新增** `tests/contracts/paratranz/test_terms_api_contract.py`：raw endpoint 和 response schema 合同。
- **可能最小修改** `plans/project-terminology-paratranz-sync/plan.md`：只回填已确认校准结论，不改变 FR5.17 验收范围。
- **条件性架构更新**：如果结论要求改变 ADR-023 的持久绑定公共契约，暂停 S02 并先用 `bm-arch` 更新 ADR；本 Story 不直接改 ADR。

## 边界条件与错误处理

- 真实响应无法取得时，fixture 只能标记为“受控假设”，S01 可实现离线 DTO，但 live contract 仍是发行门禁缺口。
- 响应 wrapper、分页总数或 header 在不同版本不一致时，typed service 必须 fail closed 或记录 unstable snapshot，不能只接受最后一次观察。
- 401/403 必须分别映射认证/授权；429 保留 Retry-After；5xx/timeout 不泄漏 response 中的 token、endpoint query 或个人数据。
- 删除返回空 body 不表示可以按本地缺失自动删除；ownership 和 confirmation 仍由 S02/S03 决定。
- remote ID 缺失、重复或类型漂移时不得生成可执行 update/delete item。

## 测试策略与建议命令

- 聚焦合同：`uv run pytest tests/contracts/paratranz/test_terms_api_contract.py -q`。
- 现有 HTTP 错误回归：`uv run pytest tests/paratranz/test_typed_client.py tests/paratranz/test_credentials_security.py -q`。
- 可选 live smoke：`uv run pytest tests/contracts/paratranz/test_terms_api_contract.py -m integration -q`，仅在专用测试项目和显式凭据下运行。
- 文档/fixture 检查：`git diff --check`，并用 secret canary 扫描 fixture，确保没有真实 token 或个人数据。

## 风险、回退与未决问题

- 最大风险是根据旧 UI 代码或接口印象推断 revision/幂等能力；控制方式是所有能力结论必须指向 fixture/header 证据。
- live smoke 的清理只能删除本次采样创建且 remote ID 已确认的术语；没有确认身份时保留并报告，不能碰预存数据。
- 三项校准未确认前允许继续完善 fixture，但 S02 的活动 Variant 映射和 S03 的 delete/lossy action 不应冻结公共接口。

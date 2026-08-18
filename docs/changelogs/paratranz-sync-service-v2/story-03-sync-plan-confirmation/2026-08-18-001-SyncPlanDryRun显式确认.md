# Story 03：Sync Plan、Dry-run 与显式确认

- 日期：2026-08-18
- Epic/Story：`paratranz-sync-service-v2/S03`
- 追溯：FR22.1～22.4、ADR-016/017/019、R-042
- 状态：实现完成，增量验证通过；待综合 QA

## 实现增量

- 新增不可变同步计划、条目动作和远端快照模型：以 EntryKey、ExternalEntryRef、revision、项目/来源 scope 及完整哈希为身份，不将远端 id 当成本地 key。重复 key/id、缺失 id、跨 scope、截断分页与不可信 revision 都 fail-closed。
- `plan_sync` 只读取远端 entries 并投影行动计数/安全诊断，不包含原文/译文 canary 或任何写端口。删除只来自显式 tombstone，覆盖和删除都属于必须确认的破坏性动作。
- 确认令牌绑定 owner、operation、plan hash、scope、时限和一次性 nonce；本地/远端 revision 或内容指纹变化令计划 stale，stale 拒绝不消耗令牌。GUI Smart Assistant、Agent 与 legacy MCP 使用同一只读 ToolRegistry 路径和 DTO。

## 验证证据

- 锁定 uv（CPython 3.12.12）运行 sync-plan/confirmation 合同、Agent/MCP、typed client、凭据、workflow 与旧工具回归：**109 passed**；6 条 warning 都是既有 downloader 的 Collection compatibility facade 弃用提示。
- 受控测试覆盖 dry-run 无副作用、确定性 hash、confirmation 缺失/重放/跨 owner/跨计划/过期、local/remote revision 与内容指纹 stale、100k 上限分页、显式 tombstone、敏感数据投影与入口 parity。
- [EvidenceManifest](../../../test-reports/requirement-code-review-2026-08-18/qa-evidence/paratranz-s03/qa-20260818T084547.628796Z-bb5fa0a9d016/manifest.json)：`passed`，已用 verifier 校验 schema、verdict 与 hash。
- 新同步模块 Ruff check/format、兼容工具的 E501 忽略后静态检查、定向 `git diff --check` 通过；`tool_paratranz.py` 的 10 条 E501 是 `HEAD` 既有 schema/description 长行，未被大面积重排。

## 剩余门禁

S03 本身没有任何执行或写端口。旧 upload/download facade 尚未接入令牌执行器；事务合并、partial/retry token、artifact staging/atomic publish 和执行后状态投影由 S04 承接。未连接真实 ParaTranz 服务，未执行 Git commit/push。

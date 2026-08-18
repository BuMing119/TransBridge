# Story 04：不可变 TranslationRunSpec 与动作/上下文计划

- 所属 Plan：[Unified Task and Translation Runtime V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR21.1～21.3/21.8/21.9；ADR-003/005/007/013/019；R-033～R-035/R-039
- 依赖：S01、I/O S02/S05

## 目标与验收

运行时固化 scope、语言、Prompt/profile/model/retrieval/config；每条每轮恰属 translate/polish/both/skip；hidden/locked 不进 LLM；上下文只分配一次并保持 Quest 顺序；disabled 检索零加载。

## 综合 QA 收口（2026-08-18）

统一配置仓库补齐 Windows 分享冲突的有界原子替换重试与稳定锁键；47 项锁定 uv 回归通过，最新 task-s04 EvidenceManifest 为 `qa-20260818T131518.084476Z-b513ef423500`。

## 数据流与接口

入口配置 snapshot + Collection revision → `TranslationRunSpec`（hashable/frozen）→ `ActionPlanner` → `ActionPlan(partitions, reasons)` → `ContextPlanner` → ordered batches。RunSpec 记录 source/target locale、prompt/profile/model endpoint、参数、retrieval capability/manifest、scope EntryKeys、run_id/input revision。

## 实施步骤

1. 新增 planning models/use case，UI/Agent/MCP/FOMOD 只提供 request，不传 mutable config 对象。
2. 把 MixedWorker rule logic 移为纯 ActionPlanner，优先级命中一次；StagePolicy 先排除 hidden/locked。
3. ContextPlanner 对 NPC/INFO/DIAL/QUST 等产生唯一 group/order；unknown 返回 diagnostic 与显式 fallback。
4. profile 解析必须切换实际 endpoint/model；target language 贯穿 prompt builder。
5. retrieval disabled 不构造 TermVectorIndex/加载语料；degraded 写入 RunSpec。
6. 迁移所有应用 INI 读写到 `transbridge.ini` ConfigRepository；删除 `[llm_profiles]`/profile 参数，秘密迁入 credential reference，旧 `paratranz_config.ini` 保持可验证只读迁移与备份。

## 测试、边界与迁移

属性测试验证分区并集=输入且互斥；全 context fixture 验证不重复/不遗漏和 Quest barrier；运行中改全局配置不改变 spec hash；provider/base_url/model 与语言多入口 parity；mock import/load spy 证明 disabled 零加载。覆盖旧 INI、缺/未来 schema、secret、并发/replace 故障与 GUI/Agent/MCP/FOMOD 同 revision；旧 Mixed 规则 UI 保留，输出改为新 planner。

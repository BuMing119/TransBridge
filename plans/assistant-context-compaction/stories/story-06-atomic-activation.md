# S06：并发、取消与原子激活闭环

- 状态：本地实现与离线验收完成。
- 所属：[实施计划 S06](../plan.md#s06)。
- 契约：[ADR-041](../../../docs/adr/041-assistant-context-compaction.md)。
- 前置：[S05 摘要候选](story-05-budgeted-compaction.md)。

## 1. 实际接口与职责

`application/assistant_context/admission.py` 负责 require_admitted、publish_context、save_wait、clear_wait 和 available_requests。`smart_assistant/context_runtime.py` 负责读取权威快照、候选生成后的追加合并、stage 和条件发布。`request_context_preparation.py` 负责独立后台队列、配置快照、取消和 GUI 交付复核。

CandidateFence 是逻辑约束，没有额外同名状态机。围栏由 expected head、request revision、lease/scope、来源归属、配置摘要、取消标记及待决资格共同组成。ContextEpoch 保存冻结模型材料，StoredContext 保存 head 和附件引用。

## 2. 执行顺序

1. 在既有 Session 串行器中核验 admission，保存原始证据。
2. 读取 head、完整摘要链和当前请求状态，冻结配置；后台构建候选。
3. 模型调用不持 Session 事务锁，不执行工具，不更新业务状态。
4. 生成后重读真实状态。意图、归属或配置变化使候选失效；合法尾部追加可重新投影合并，但不得无限追着结果流生成。
5. AssistantContextStore.stage 写不可变附件；未提交附件不能成为当前输入。
6. publish_context 在既有 Session CAS 中检查 expected head、当前资格、来源和完整旧链，同时发布 head、可达引用及等待状态清理。
7. GUI 交付复核 admission，分派前再次检查配置；旧回调不能驱动新请求。业务工具仍经过原有权限、effect 与 TaskRuntime 守卫。

CAS 回调只检查与提交，不调用模型；竞争候选不能重复发布。无关 Session revision 变化可沿既有事务机制重试，不能跳过相关身份核验。

## 3. 输入、等待和取消

上下文使用独立 executor，用户输入仍进入既有单线程输入队列，不排在摘要网络调用之后。取消立即置位，独立连接在后台关闭；关闭 UI 不等待远端模型响应。

PreparationWait 走专用通路，释放当前轮次并显示上下文等待，不将容量或摘要问题记为业务失败。显式继续仅清除上下文等待，不解除用户暂停或待确认。同一 revision/config/material 的等待不会因自动唤醒反复计费；新有效材料或配置变化可以重新评估。

超大历史的未发布进度额外绑定原 request revision、expected head 与归属摘要。AMEND 后不复用旧候选；已提交摘要正文依然保留。FOLLOW_UP 只获取明确父请求的公共背景，不能继承其权限。

usage 回调捕获原会话、原轮次；取消、切换和 GUI 桥关闭后仍可保存已发生消费。诊断不重建已删除业务 Session，业务输出仍受独立围栏约束。

## 4. 崩溃与恢复

- 生成或 stage 后未发布：旧 head 生效，孤立附件由既有显式维护处理。
- 已发布但 GUI 未收到：重启读取 head，恢复全部摘要及顺序，不重复生成同段。
- head/索引或已激活摘要损坏：明确恢复等待，经既有完整备份恢复并核验原链；不扫描孤立段猜测活动顺序，不重新生成不同正文冒充原段。
- 必要原文缺失：报告缺口，不能用摘要代替业务证据。

当前、备份和保留引用共用 transcript manifest 的清理可达图；未知 artifact 版本拒绝清理。跨进程保证沿用既有 Session 写租约，进程内锁不被宣称为任意外部写者的事务保证。

## 5. 验证

`test_activation.py`、`test_context_review.py`、`test_context_store.py` 与 `test_long_sessions.py` 覆盖条件发布、双候选、过期资格、来源纠正、恢复与未发布进度。真实 Qt 请求生命周期覆盖新输入、暂停、关闭、资源等待、摘要失败、显式继续及目标/计划保持。`test_context_usage.py` 验证迟到用量归属。

```powershell
uv run --offline --no-sync --no-cache pytest tests/application/assistant_context tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py tests/smart_assistant/test_context_usage.py -q
```

列表刷新由 RequestViewRefresh 后台读取并合并重复请求；会话切换丢弃旧结果。回答接纳仍连续完成落盘与前台回执，避免中断创建冲突回执。一万条消息的真实 Qt/Session 单次测量及同步落盘限制见[验证报告](../../../docs/test-reports/assistant-context-compaction.md)，不以一次样本宣称 P95 达标。

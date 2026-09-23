# S03：控制工具结果的权威提交

- 状态：已实现并通过聚焦回归；[Plan](../plan.md)、[ADR-043](../../../docs/adr/043-assistant-turn-results-and-undo.md)。
- 目标：同一工具调用只有一条已接纳终结结果，提交和取消不由 GUI 回调决定。

## 合同与步骤

1. 新增 application/assistant_requests/control_results.py，接收 context、admission、tool_call_id/name、不可变结果及可选业务状态变换。核实父 assistant 消息存在、工具名匹配、当前请求归属，不接受模型选择 owner。
2. 锁顺序 service → scheduler → session lifecycle；scheduler 提供窄的序列化接口，成功检查与提交间不能释放租约。GUI 不调用会等待长锁的 release，改由后台终结路径执行。
3. 复用事务 append_messages，以稳定结果身份追加并记录所有权；重复同内容返回原回执，异内容拒绝。不要给后台共享活 ConversationManager。
4. 取消只处理明确由该用例拥有的调用，已提交成功仍保留；后台未提交则保存取消。中断 UI 立即停止续跑，不能先合成冲突回执。
5. 查询各自执行范围/digest 校验；回答覆盖状态与回执在同一事务。背景执行任务的取消不由此模块代替。
6. 路由同样使用后台 routing_results 用例：完整原始对话先保存，路由变更、直接回答和工具回执同事务提交；恢复仅终结悬挂调用，不重放旧路由。GUI 慢撤销持锁期间收到路由响应不等待 Session 锁。

## 验收

保存失败无内存成功；取消前后交错只有一条持久结果；原调用缺失与越权拒绝；同一消息重复安全，冲突可见；重新打开由持久结果恢复。薄接线之后慢存储 Qt 测试证明 GUI 不等待锁。现有未知结果保持未知，不将故障转为隐式重试。

# 助手稳定上下文实施验证

- 日期：2026-09-12。
- 范围：[实施计划](../../plans/assistant-context-compaction/plan.md)、[ADR-041](../adr/041-assistant-context-compaction.md)。
- 结论：本地实现及相关离线回归通过；真实模型收益、人工语义质量和固定硬件重复性能样本尚未验收。
- 环境：Windows、Python 3.12.12、pytest 9.1.1；现有 uv 环境与锁文件，未安装依赖。

## 1. 验证证据

相关广泛回归：1232 passed，34 warnings，76.06 秒。警告来自既有 SWIG 及兼容数据修改接口的弃用提示，无失败。

```powershell
uv run --offline --no-sync --no-cache pytest tests/application/assistant_context tests/application/assistant_requests tests/smart_assistant tests/ui/tools/smart_assistant tests/infra/test_assistant_prompt_cache.py tests/infra/test_llm_usage.py tests/infra/test_llm_client_prompt_cache.py tests/infra/test_openai_tool_calling.py tests/infra/test_anthropic_tool_calling.py tests/persistence/v2/test_assistant_session_migration.py tests/persistence/test_assistant_attachment_cleanup.py tests/persistence/test_assistant_attachment_cleanup_cli.py tests/config/test_assistant_context_settings.py tests/contracts/config/test_unified_repository.py tests/ui/test_ui_settings_dialog.py -q --tb=short
```

最后修正历史摘录迁移和旧系统提示后，另行聚焦复验 40 passed（21.31 秒）：

```powershell
uv run --offline --no-sync --no-cache pytest tests/application/assistant_context/test_migration.py tests/application/assistant_context/test_context_store.py tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py -q --tb=short
```

UI 最终复验：185 passed，3 warnings，37.19 秒，包含整个助手 UI、assistant_context 及 orchestrator lifecycle；回归确认回答提交与前台回执保持同一顺序。

```powershell
uv run --offline --no-sync --no-cache pytest tests/ui/tools/smart_assistant tests/application/assistant_context tests/smart_assistant/test_conversation_orchestrator_lifecycle.py -q --tb=short
```

以上数字有重复测试，不能相加宣称测试总数。原适配器基线为 27 passed。开发中暴露的真实配置复制、JSON 状态比较、分页再次摘要化、过期候选复用、摘要标记覆盖、清理格式白名单和共享输入队列问题均已修复并加入回归。

最后补充的列表跨会话/关闭回调回归 2 passed（0.67 秒）。最终 Ruff 静态检查通过，src/tests 及评测脚本共 1358 个文件格式检查通过；git diff --check 通过，5 份新增设计/计划/验证文档的 21 个本地链接有效。

```powershell
uv run --offline --no-sync --no-cache ruff check src tests
uv run --offline --no-sync --no-cache ruff format --check src tests
uv run --offline --no-sync --no-cache ruff check scripts/evaluate_assistant_context.py
uv run --offline --no-sync --no-cache pytest tests/ui/tools/smart_assistant/test_request_view_refresh.py -q --tb=short
git diff --check
```

沙箱内现有 Python 无法启动；经自动审批在沙箱外执行上述离线命令，未更改 Python、依赖或锁文件。

## 2. 功能与并发覆盖

- 50 次普通追加的模型前缀原样保留，每次只新写差量 artifact。
- 至少三次压缩及附件重开后，全部旧摘要正文按序出现在最终分派材料，新来源没有重复覆盖。
- 摘要链本身或当前必需材料超限时零自动删段、零递归改写；取消和异常不会隐式无限生成。
- stage 未发布不成为当前 head；两个竞争候选仅一个发布；旧 lease/配置不能发布或分派。
- AMEND 后旧 revision 的未提交摘要候选被丢弃；FOLLOW_UP 获取明确父请求的公共背景且不继承许可。
- 暂停、资源等待、关闭及新输入穿过真实 Qt 生命周期；输入持久化不等待摘要队列，取消不等待模型连接关闭。
- 来源归属、分页边界、查询回执保全、损坏/缺失附件、未来格式拒绝及清理递归可达性。
- 旧摘要允许历史 revision，经当前来源与权限核验后原样导入，覆盖集合为空；归档不会删除旧摘要。
- OpenAI/Anthropic 最终协议、助手专属缓存布局与旧翻译缓存兼容；实际 usage 零/未知/partial、每次重试计账、跨会话迟到回调及 UI 桥关闭。

## 3. 一万条合成消息性能

```powershell
uv run --offline --no-sync --no-cache python scripts/evaluate_assistant_context.py --messages 10000
```

单次运行、开启 tracemalloc，纯合成英文短消息；使用可容纳全部历史的离线预算，未调用模型。

- 旧 RequestContextAssembler 投影：约 674.0ms。
- 新首次投影：约 556.9ms；后续单条追加投影：约 228.0ms。
- 首次存储：约 284.3ms；追加存储：约 6.6ms；重读恢复：约 136.1ms。
- 追加只生成 1 个新 item、732 字节新 artifact；旧前缀一致。
- Python 跟踪的峰值内存约 20.2MB。

这里没有扣除或隐藏已测步骤，但未测步骤不能视为零：现有 Session 完整读写、整个准备入口和 Qt 心跳均不在该脚本测量范围。普通投影仍遍历来源身份/权限，恢复仍核验附件；这些时间不能被描述成严格 O(1) 或完整 UI 延迟。一次本地样本不足以推断供应商成本或延迟改善。

## 4. 未完成的 S09 验收与兼容边界

真实模型至少 12 条标注长任务、每策略至少三次的冷缓存/热缓存/压缩后缓存、语义保真、总实际 token、费用及 TTFT 均为 not_run。当前未授权付费模型评测，不读取或转发真实用户翻译数据。离线 fake 摘要只验证流程、来源及协议，不能证明模型不会遗漏语义。

完整 Qt/Session 测量已运行：一万条合成消息，fake 业务模型，整轮约 1235ms、最大心跳间隔约 188ms、73 次心跳、1 次业务调用、0 次摘要调用。请求列表已后台读取并合并刷新。回答持久化仍按既有前台顺序提交以避免中断回执冲突，本次耗时约 140ms；它是后续性能工作的明确落点。此前同路径样本曾约 204ms，因此不能把一次低于 200ms 的结果标成稳定性能达标，固定硬件多样本 P95 仍待验收。

```powershell
uv run --offline --no-sync --no-cache pytest tests/ui/tools/smart_assistant/test_context_performance.py -q -s --tb=short
```

坏 head/索引采用恢复等待和既有完整备份恢复，不自动扫描散落摘要附件重建顺序；已激活摘要损坏时不会重新生成正文。

新上下文附件使用内部 schema_version=1。旧二进制及旧清理工具无法理解新格式；回退使用升级前独立完整备份。全部旧摘要持续占用模型窗口，最终容量等待是设计边界。生成/修复调用有上限；供应商兼容降级可能增加一次单独记账的 wire attempt，费用必须按 attempt 汇总。

没有 commit、push、发布、联机模型调用或依赖变更。

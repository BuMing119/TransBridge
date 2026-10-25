# 默认 100 并发与流式处理优化

- 日期：2026-10-25。
- Epic / Story：AI 多插件并发与项目共享术语，S19～S23。
- 依据：[实施计划](../../../../plans/ai-project-concurrency/concurrency-100-plan.md)，用户明确调用 bm-pilot 执行。
- 归属：与本轮开始时保存的工作区源码副本逐文件比较；Git HEAD 为 `ee3a592013429aafcc67dc96c0dc7650e0af9825`，该副本包含此前未提交修改，不能将 HEAD 当作测量基线。本轮没有提交 Git、修改依赖、调用真实模型或改写用户项目。
- [原始数值、输入参数、验证集合及文件摘要](2026-10-25-001-concurrency-evidence.json)。临时输入、源码副本和 QA 目录在收尾清理。

## 交付行为

新配置默认 100 个在途逻辑请求，任务界面范围 1～128；统一设置和自定义流程缺省值共用同一常量。显式保存的 3、20、50、100、128 和恢复快照原值不变，不把原有 3 猜测为未配置。请求额度仍由任务共享，批次 Token 预算、每请求条目数、Quest 顺序、准备额度及 HTTP 连接池不变。

日志保留调用和批次两个通道，后台合并写入，100 并发可保留 216 个句柄，硬上限 272。待写文本按每字符 4 字节计费、总额 4MiB，通常每 100ms 或 64KiB 刷新，满载背压。调用结束、通道关闭和任务关闭排空尾部；写盘或线程启动失败保留诊断且不改判有效译文。仅缓冲诊断日志，候选检查点的可靠写盘不变。

JSON 提取和重复检测分别维护每请求增量状态。已完成条目只解码一次，重复 ID 撤销暂存结果；最终结构校验及 salvage 边界保留。重复检测保留原阈值、最先符合条件的短语重复链、完整重复块长度及截断位置，包括旧正则的单字符分支优先行为。

## 文件变化

### 默认值与入口

- 修改 `src/transbridge/config/llm.py`：权威默认 100、界面支持上限 128，配置序列化字段不变。
- 修改 `src/transbridge/application/translation/custom_workflow_profile.py`：缺省回退及范围引用权威常量。
- 修改 `src/transbridge/ui/tools/ai_translator/single_task_view.py`：初始值和范围同步，取消原 50 上限造成的截断。
- 修改 `src/transbridge/ui/settings/ai_service_page.py`：统一设置页缺失配置时也使用 100。

### 流处理与日志

- 新增 `src/transbridge/ai_translator/translation_stream.py`：增量 JSON 词法状态、完成条目解码和重复 ID 撤销。
- 新增 `src/transbridge/ai_translator/stream_repetition.py`：跨片段单字符/4～10 字符周期状态，只保留十字符尾部。
- 修改 `src/transbridge/ai_translator/prompt_builder.py`：无状态部分结果提取复用同一解析规则。
- 修改 `src/transbridge/ai_translator/translator.py`：每请求创建增量状态，按新增/撤销项处理；兼容静态检测和异常恢复接口。主控文件净减 9 行，新责任位于独立模块。
- 新增 `src/transbridge/ui/tools/ai_translator/workflow_log_writer.py`：有界队列、后台合并、LRU 文件池、刷新屏障、错误及关闭生命周期。
- 修改 `src/transbridge/ui/tools/ai_translator/workflow_log_store.py`：目录和通道门面委托给写入器，暴露显式 flush 和失败诊断。
- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`、`task_name_preparation.py`、`source_reference_review.py`、`run_controller.py`、`polish_runtime.py`、`_mixed_worker.py`、`_polish_worker.py`：向日志池传递实际并发额度；混合旧入口从请求预算取值，保留不完整配置适配。
- 修改 `src/transbridge/smart_assistant/tools/_workflow_llm_runtime.py`：助手工作流日志池同样使用实际额度。

### 回归、基准和文档

- 新增 `tests/ai_translator/test_translation_stream.py`、`test_stream_repetition.py`：以旧实现为冻结测试 oracle，覆盖任意切分、转义、截断、无效条目、重复 ID、不同周期/阈值/换行与随机块界限；验证完整项只解码一次。
- 新增 `tests/ui/tools/test_workflow_log_writer.py`：200 活跃通道合并、慢盘背压、周期可见、错误唤醒、重复关闭及线程启动失败。
- 修改 `tests/ui/tools/test_workflow_log_lifecycle.py`：适配异步可见边界和新的文件池上限，保留顺序、追加、取消/失败及句柄释放断言。
- 新增 `tests/config/test_ai_concurrency_defaults.py`、`tests/ui/test_ai_concurrency_settings.py`，修改 `tests/ui/tools/test_ai_mode_switching.py`：缺省 100、旧值保留、恢复快照及 128 界面往返。
- 新增 `tests/application/translation/test_ai_budget_100.py`：实际占满 100 个共享额度，跨来源/阶段、异常、暂停和取消后正确释放。
- 新增 `tests/infra/test_llm_connection_capacity.py`：无网络核验两个现有传输的 100 连接容量、取消后重建及旧池关闭。
- 新增 `scripts/benchmark_ai_stream_concurrency.py`：合成 provider、真实 `_run_batch`、1000 术语、双通道日志、候选持久化/恢复、Qt 心跳及资源计数，支持源码副本对照。
- 新增 `scripts/benchmark_ai_stream_processing.py`：隔离 JSON 和重复检测 CPU；累计至少 250ms CPU 避免 Windows 时钟粒度将短任务测成零。
- 修改 `docs/dev/ai_translator.md`、对应计划和直接索引，说明当前默认值、日志一致性及验证边界。

## 五轮交替测量

Windows 11、Python 3.12.12、32 逻辑处理器。同机依次运行旧副本/当前代码，各场景五对独立进程；每次以 barrier 确认实际峰值 100。合成请求使用真实提示构建和 1000 条词法术语，语义向量关闭。总耗时包含批次执行、检查点和日志收尾，不包含模型/SDK导入或术语管理器冷启动。

- **100 请求 × 每请求 1 条，16 字符/50ms**：中位 1.541 → 1.163 秒；旧范围 1.423～1.611，新范围 1.076～1.294。
- **100 × 20 条，16 字符/50ms**：中位 15.111 → 7.078 秒；旧范围 14.029～39.689，新范围 7.022～7.674。进程 CPU 中位 16.125 → 2.719 秒，峰值 RSS 中位 131.70 → 134.18MiB；日志打开由 21801～23120 次降至固定 200 次。
- **100 × 50 条，64 字符/10ms**：中位 15.152 → 3.377 秒；旧范围 14.620～21.275，新范围 3.282～3.438。
- **200 请求分多波、峰值 100、每请求 20 条，64 字符/10ms**：中位 9.888 → 3.737 秒；旧范围 9.587～26.459，新范围 3.527～7.175；新版本日志固定打开 400 次。

新版本二十次测量累计 **55,500 条**候选全部接纳并正确恢复，无身份重复或译文差异。旧版本多波第 4 次尝试遇到一次 Windows 错误 5，检查点原子替换失败并使测量中止；该失败保留在证据文件，重新运行该对照，不将失败伪装成成功样本。

50 条、4003 字符、16 字符分块的独立 CPU 测量五轮中位：部分 JSON 提取 7.576 → 0.595ms；重复检测 560.938 → 8.333ms。源码选择、循环次数与各轮数值保存在证据 JSON。

性能并非全维度改善：常规 20 条场景的新版本最大 Qt 心跳间隔为 33.49ms；快速长响应场景为 178.80～341.08ms，旧版本为 28.31～33.98ms。消除同步 IO 后的高速突发仍可带来短暂 UI 延迟，没有宣称全部场景无卡顿或固定耗时 SLA。CPU/磁盘负载波动在多波场景仍明显。

复现命令（旧副本应同时具有 `src/` 及同版本 `data/prompts/`）：

```powershell
uv run --no-sync --offline python scripts/benchmark_ai_stream_concurrency.py --source <源码目录> --samples 1 --output <结果.json>
# 每轮依次运行 before/after，共五轮；其他场景增加以下参数之一：
# --rows 1
# --rows 50 --chunk 64 --interval 0.01
# --requests 200 --chunk 64 --interval 0.01
uv run --no-sync --offline python scripts/benchmark_ai_stream_processing.py --source <源码目录> --samples 5 --output <CPU结果.json>
```

## 验证结果与限制

最终相关集合 **1,457 个不同用例通过**，包含 94 个新增用例：后端 1102、UI 分组 262 和 92、长路径受限用例改用专属短目录后 1 项通过。七项既有失败在起始源码副本复现后排除：

1. 已有术语提取测试的 `ConstructorOnlyPrompt` 缺少 `build_translation_prompt`。
2. 术语存储两项测试的旧假对象缺少 `_lock`。
3. 校对目标 stage 测试 `[False-1]` 得到 2 而预期 1。
4. 窗口布局的两个窄窗/字号几何断言。
5. 中断综合校对接续测试得到 2 次评估而预期 3；基线单独复验同样失败。

精确测试路径和排除 nodeid 保存在证据 JSON 的 `test_groups`、`known_baseline_failures`。运行方式：

```powershell
uv run --no-sync --offline pytest <test_groups 中的路径> -m "not llm and not integration" -q -p no:cacheprovider --basetemp <专属短目录> --deselect <对应既有失败 nodeid>
uv run --no-sync --offline ruff check src tests scripts/benchmark_ai_stream_concurrency.py scripts/benchmark_ai_stream_processing.py
uv run --no-sync --offline ruff format --check src tests scripts/benchmark_ai_stream_concurrency.py scripts/benchmark_ai_stream_processing.py
```

沙箱内硬链接被拒绝，长目录使 SQLite/旧路径测试失败；在获准的沙箱外运行和专属短目录中复验，不修改产品保存协议来迁就测试环境。一次过宽的 2865 项合集在完成前停止；旧 UI 大合集曾原生退出，均不计为整套通过。

本轮 29 个 Python 文件的 Ruff lint/format 均通过。收尾时全库检查仍遇到另一工作线 `src/transbridge/ui/operations/batch_download_session.py:343` 的 E501/格式问题；未改动该文件，未声称全库检查通过。

完整任务进度、实时日志查看、保存/恢复由独立功能测试覆盖；并发计时使用轻量文本窗口和真实 TaskFeedback，并非整个主窗口压测。真实 API 吞吐、服务端限流、模型质量和开启语义向量后的冷/热性能未运行。正常关闭排空日志；进程崩溃可能丢失有界的诊断尾部，不影响已确认持久化的候选。无格式迁移，也未提高既有 HTTP 池到 128。

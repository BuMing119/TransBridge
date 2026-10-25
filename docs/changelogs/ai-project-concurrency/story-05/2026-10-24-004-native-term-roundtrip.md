# 名称发布误报动态术语变化：原生记录往返与原因分类修复

- 日期：2026-10-24
- Epic / Story：ai-project-concurrency / S05，项目名称准备与共享术语发布
- 关联：[实施计划](../../../../plans/ai-project-concurrency/plan.md)

## 现场原因

用户报告名称预翻译术语不一致，并且两个来源共 20,162 条最终显示未完成。只读检查现场日志确认异常发生在 `prepare_names → _publish → SharedTermPool.publish → TaskConsistency.record_generated_terms`，命中持久化读回值与内存值的比较；不是模型未返回任何结果。

原生 serializer 保留原文、译文和上下文空白，读回却复用了外部导入适配器的 `strip()`。现场请求包含首空格原文 ` Frost Immunity`。该输入可稳定复现：程序自己的术语写入被误判为外部修改，任务一致性守卫拒绝继续，发布回滚，所有后续来源被中止。

名称术语冲突是另一条独立链路：历史动态 `auto_dialogue` 记录含 `Guards → 瓜尔`、`Whispers → 低语学院`；ParaTranz 参考词含多义释义。名称准备对完整有效词库做目标串包含硬检查，普通翻译的显式术语修复以外不做相同硬拒绝；发送时参考词存在数量裁剪，名称检查却不裁剪。已观察到模型返回“布鲁玛卫兵对战掠食者”，随后被错误历史译法拒绝。本轮保留现有语义合同，没有自动修改词库或删除强制检查；参考词与可强制决定的区分仍是单独的策略问题。

## 修改

- 新增 `src/transbridge/ai_translator/native_term_format.py`：识别原生完整字段记录并按原类型读取，保留文本空白、变体顺序/重复项、标识字符串类型和 metadata；显式 source 覆盖仍支持，空必填值跳过，损坏原生字段类型明确拒绝。
- 修改 `src/transbridge/ai_translator/term_formats.py::term_entries_from_data`：完整原生 JSON 使用专责读取器；外部简表、别名字段和显式 mapping 导入继续沿用既有规范化。原模块已接近 500 行，因此类型与保真责任独立提取。
- 修改 `src/transbridge/ui/tools/ai_translator/task_run_presentation.py::failure_reason`：将“术语库已变化”“名称术语不一致”与“术语修复未完成”分开，避免所有带“术语”的错误都被汇总成修复失败。
- 更新 `tests/ai_translator/test_term_formats.py`：原生完整字段、空白、标识类型、空必填值、坏字段拒绝与外部导入兼容回归。
- 新增 `tests/ui/tools/test_generated_term_roundtrip.py`：shared/legacy 写入确认、下一来源写入、重载、校对/翻译观察和最终守卫；外部译文或 metadata 修改仍拒绝且不覆盖外部字节。
- 新增 `tests/ui/tools/test_ai_name_publication_roundtrip.py`：真实两来源名称准备、去重复用、正文和最终术语检查通过，首空格原文保持不变。
- 更新 `tests/ui/tools/test_ai_task_presentation.py`：失败分类准确累计回归。
- 更新本计划与必要索引。既有增量记录不改写；一致性锁、文件版本检查、共享池原始字节核验和外部修改保护逻辑均保留。

## 验证

现有 uv 离线环境；Qt 使用 `QT_QPA_PLATFORM=offscreen`，测试 TEMP/TMP 定向本任务专用目录。测试只使用合成数据/模拟模型，无真实 API 请求或用户项目写入。

```powershell
uv --cache-dir .q31-canonical/uv-cache run --offline --no-sync pytest tests/ai_translator/test_term_formats.py tests/ui/tools/test_generated_term_roundtrip.py tests/ui/tools/test_shared_task_term_consistency.py tests/ui/tools/test_ai_task_term_transactions.py -q --basetemp .q31-canonical/green
uv --cache-dir .q31/uv-cache run --offline --no-sync pytest tests/ui/tools/test_ai_name_publication_roundtrip.py tests/ui/tools/test_ai_task_presentation.py -q --basetemp .q31/focused -o cache_dir=.q31/pytest-cache
uv --cache-dir .q31/uv-cache run --offline --no-sync pytest tests/ai_translator tests/application/terminology tests/contracts/terminology tests/ui/tools/test_generated_term_roundtrip.py tests/ui/tools/test_shared_task_term_consistency.py tests/ui/tools/test_ai_task_term_transactions.py tests/ui/tools/test_ai_task_term_writes.py tests/ui/tools/test_ai_task_term_closure.py tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_ai_project_name_preparation.py tests/ui/tools/test_ai_project_name_reuse.py tests/ui/tools/test_ai_project_name_scheduling.py tests/ui/tools/test_ai_project_name_barrier.py tests/ui/tools/test_ai_name_publication_roundtrip.py tests/ui/tools/test_ai_task_presentation.py -q -m 'not llm and not integration' --basetemp .q31/regression -o cache_dir=.q31/pytest-cache
uv --cache-dir .q31/uv-cache run --offline --no-sync ruff check src tests
uv --cache-dir .q31/uv-cache run --offline --no-sync ruff format --check src tests
```

- 修复前新增最小回归：14 失败、6 通过；失败集中于原生往返及 shared/legacy 自写确认。沙箱进程曾输出 FAISS Windows 诊断，但 pytest 完成并报告上述结果；绿色验证在批准的本地测试环境执行。
- 格式与生成术语专项：64 通过；两来源完整流程与摘要专项：9 通过。
- 扩展回归：**893 通过，5 个弃用警告，73.00 秒**，包含上述专项，不重复累计。
- 全库 Ruff lint 与 format check 通过，2085 个文件格式符合要求。
- 未跑全仓 pytest、真实模型或用户原任务重跑；未修改用户术语数据，未重启用户应用，未提交 Git。

## 兼容与遗留

原生 JSON 字段结构不变，无数据迁移；完整原生记录现在按字段原值读取，类型损坏明确报错。外部导入的规范化继续保留。应用重启后新任务使用修复。

整批误报缺陷已修复。历史错误映射、多义词参考被硬约束以及提示裁剪与事后校验的差异尚未改变；下一步应分别建模复用参考集合与已确认的强制决定，保留项目/插件作用域、显式修复和共享池闭环，不能通过简单关闭全部校验掩盖问题。

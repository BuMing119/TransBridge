# 已有名称译文在剩余模型请求前写入任务草稿

- 日期：2026-10-24
- Epic：`ai-project-concurrency`；Story：S10。
- 依据：用户明确要求“检测到可复用译文就直接填进草稿，不要等整批名称准备完成”，并补齐文档；[计划](../../../../plans/ai-project-concurrency/plan.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)、FR5.13.11。
- 归属：依据本会话前序修改、当前轮开始保存的文件基线、目标 Git 差异及暂存区检查，仅记录名称提前复用增量。已有 S01～S09、并发后台化、ParaTranz 等其他未提交工作不归入本记录。没有 commit/push。

## 行为变化

项目名称扫描及作用域检查完成后，所有通过校验的已有译法先持久化候选、受保护提交到对应任务草稿，再发出剩余名称的模型请求。任务窗口显示“写入已有译文”和实际“已写入草稿”计数；仅检测到匹配不再冒充写入成功。

提前填好的译文携带原始输入、提交后快照和提交 ID，来源翻译接续这些证据，不重复提交或送入翻译模型；仍执行配置的后处理和最终术语检查。全部复用时翻译请求为零，校对与必要修复仍可能调用模型。

提交按来源独立完成。模型失败保留已写入项；后一来源保存失败阻止模型启动、保留前一来源草稿与失败诊断。暂停/取消和输入修订保护仍生效。取消或名称准备异常时，尚未完成检查的草稿译文不能被账本当作可应用成功。最终项目应用与保存边界不变。

## 逐文件变更

以下路径相对仓库根目录；只记录所列符号/段落，不将同文件其他既有修改算入本 Story。

- 新增 `src/transbridge/application/translation/draft_reuse.py`：`commit_draft_reuse` 复用既有候选持久化和提交服务；`CommittedDraftTranslations` 验证提交证据、当前快照及范围，并将既有提交并入结果而不重写。
- 新增 `src/transbridge/ui/tools/ai_translator/task_name_reuse.py`：按来源提前提交名称；暂停/取消与一致性检查；失败或取消时保留已写入但未完成检查的逐条事实。
- 修改 `src/transbridge/ui/tools/ai_translator/task_name_preparation.py`：已知译文在名称模型调度前实写；后续验证不覆盖这些证据；进度统计采用实际提交组数。
- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`：保存来源级提交证据并传入普通翻译；隔离名称生成和显式最终术语修复不复用这份接续参数。
- 修改 `src/transbridge/ui/tools/ai_translator/task_worker.py`：首批屏障取消或异常后，完成信号仍携带已填草稿的文本及未完成状态。
- 修改 `src/transbridge/ai_translator/translator.py`：可选 `committed_draft` 依赖、范围校验、候选过滤、恢复指纹、成功统计、提交证据合并、报告和后处理接续。大型主控仅做接线，草稿提交责任在独立模块。
- 本会话前序新增 `src/transbridge/ai_translator/translation_reuse.py` 并接入 `translator.py`：普通翻译批次前接纳精确术语及准备结果，过滤已解决条目；保持原批次身份、失败不回退模型。该候选路径与本次新增的名称草稿实写路径职责不同。
- 本会话前序修改 `src/transbridge/application/translation/postprocess_progress.py`：提供普通来源的“复用已有译文”阶段标签。
- 修改 `src/transbridge/ui/tools/ai_translator/task_progress_presentation.py`：新增“写入已有译文”阶段；后续显示已提交独立名称数，避免将尚未提交项标为完成。
- 新增 `tests/application/translation/test_draft_reuse.py`：8 项持久化/提交顺序、失败、取消、范围、快照变化及伪造证据防护测试。
- 新增 `tests/ui/tools/test_ai_project_name_reuse.py`：9 项阻塞模型时真实草稿已写入、后处理接续、存储失败、暂停取消、单来源模型失败、跨来源部分提交、输入变化和重复执行测试。
- 本会话前序新增并在本轮扩展 `tests/ai_translator/test_translation_reuse.py`：共 12 项，覆盖普通批次前候选复用、失败不重复翻译、全量复用、已提交项校对及检查点接续。
- 修改 `tests/ai_translator/test_prepared_names.py`：测试构造器支持可选提前提交证据，便于真实翻译生命周期测试复用。
- 修改 `tests/ui/tools/test_ai_project_name_preparation.py`：名称准备后立即检查草稿实写；来源执行后无重复修订。
- 修改 `tests/ui/tools/test_ai_project_name_progress.py`：增加真实持久化所需测试输入，校验新增阶段及排除已提交项后的验证分母。
- 修改 `tests/ui/tools/test_ai_task_progress_presentation.py`：准确展示已写入草稿计数；重试测试等待后台启动真正替换 worker，适应工作区已有异步生命周期。
- 修改 `docs/requirements.md`、`docs/adr/003-three-round-translation-strategy.md`、`docs/adr/048-ai-project-concurrency-and-terms.md`：更新提前写入时机、证据、按来源提交、取消及接续边界。产品时机已确认，技术设计修订保留提议标记。
- 修改 `docs/dev/ai_translator.md`：替换过时的流式直接写回说明及流程图，说明任务草稿实写、普通候选、最终项目应用和恢复范围；更新相关索引 `docs/dev/INDEX.md`、`docs/INDEX.md`。
- 更新 `plans/ai-project-concurrency/plan.md` 的 S05 合同及 S10 进度，更新 `plans/INDEX.md`、`docs/changelogs/INDEX.md`；新增 [QA 报告](../../../test-reports/ai-name-immediate-reuse-qa-2026-10-24.md)。历史 S05 日志保留原始时点事实，不回写历史。

## 验证

完整命令、测试范围及诊断记录见 [QA 报告](../../../test-reports/ai-name-immediate-reuse-qa-2026-10-24.md)。

- 专项 pytest：107 passed。
- 来源并发、术语事务、最终闭环与统一执行：108 passed，与专项重叠 9 项。
- `pytest tests/ai_translator tests/application/translation tests/infra/test_limited_llm_client.py`：830 passed；使用正常权限及短临时路径，避免 Windows 硬链接和 SQLite 路径限制。
- 定向 Ruff 检查及全库 `ruff check src tests` 通过；全库格式检查曾通过，12:25 复验时另一工作线 `test_live_llm_logs.py` 有 1 处格式差异，本次不代改。
- 较大 UI 合集在历史结果窗口测试期间以 Windows 0xc0000409 退出，未获得整套通过结果；该文件独立 7 项通过，根因未确定。另有并发工作线新测试 `test_ai_launch_refresh_guard.py` 的类名导入错误，以及历史已知的两项布局断言被显式隔离。
- 未运行真实模型 API、实际项目吞吐基准、打包应用桌面验收或全仓库全部测试。

## 兼容与遗留

新增参数默认空，现有独立翻译调用兼容，不改文件格式或依赖。任务草稿接续需要有效证据；旧任务不能根据相同非空译文猜测恢复，建议重启应用后新建任务。本次未扩展跨进程翻译恢复入口，正在运行的旧进程不会自动采用新流程。

即时草稿复用实现与专项验证已完成。遗留为上述较大 UI 合集退出、其他工作线导入/格式问题和真实模型验证，不宣称它们已修复。任务临时基线、缓存与测试目录在收尾清理，不纳入仓库。

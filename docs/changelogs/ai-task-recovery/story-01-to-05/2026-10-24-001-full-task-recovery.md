# AI 翻译、校对与结果保存的完整恢复链

日期：2026-10-24  
Epic：ai-task-recovery  
Story：S1–S5  
计划：[AI 任务完整恢复](../../../../plans/ai-task-recovery/plan.md)

## 问题与结果

旧的 GUI 恢复入口只支持纯校对。翻译任务异常退出后，即使 runtime-v2 已保存候选，历史列表仍无法继续。此次为项目翻译、校对和混合任务接通执行清单、候选、后处理、完整结果和正式保存之间的恢复链。

新任务在模型调用前保存去凭据配置、输入范围和每次执行身份。再次打开项目可显式继续未完成任务；已保存的有效候选跳过对应翻译请求，已完成的校对批次/后处理阶段可复用，完整结果及待确认、采纳、拒绝决定可恢复。项目已经保存、任务状态尚未落盘时，按严格内容匹配避免重复应用。耗时恢复和持久化工作沿用后台 worker。

旧任务没有完整配置快照，只在原范围和动作可证明时提供接续。继续时先展示当前配置，再把旧候选作为私有草稿复用并完成后续检查；原历史和检查点保持原样。取消任务不自动复活，重新开始使用独立执行链。

## 本次归属

开始时工作区已有大量未提交修改。先保存本轮前的源码、测试、文档及计划基线，再结合 Git diff 核对本轮差异；以下只记录本次新增或本轮继续修改的内容，不把此前并发、术语策略、GUI 性能或 ParaTranz 修改归入本次。

### 应用层与翻译执行

- `src/transbridge/application/translation/candidate_checkpoint.py`：增加 `load_readonly` 入口，检查历史候选时不产生写锁文件。
- `src/transbridge/application/translation/candidate_checkpoint_store.py`：读取并校验 schema 1/2；读取中检查 head 变化，稳定损坏明确报错。
- `src/transbridge/application/translation/legacy_task_recovery.py`（新增）：按历史完整 before 报告、任务/来源身份发现可证明范围的旧翻译任务，生成虚拟恢复清单。
- `src/transbridge/application/translation/task_candidate_recovery.py`（新增）：按执行链、attempt 和阶段合并候选，主翻译优先；校验完整 EntryKey、动作和原始输入。逐执行输入只校验实际产生候选的词条，兼容已保存项与剩余工作同时存在后的再次中断。
- `src/transbridge/application/translation/task_recovery_lease.py`（新增）：为整个逻辑恢复链持有非阻塞 OS 锁；旧任务及后续恢复清单共享原始任务锁，进程退出自动释放。
- `src/transbridge/application/translation/task_recovery_results.py`（新增）：原子保存带校验和的成功结果与用户决定，保留报告证据；延迟写入不能覆盖较新的 attempt 或撤销已确认决定。
- `src/transbridge/application/translation/task_recovery.py`：接入执行锁与结果存储，校验 contract 2 的覆盖选项、执行链和完整输入；损坏输入身份转为带原因的校验错误，列表仍能发现健康任务。
- `src/transbridge/application/translation/postprocess_resume.py`（新增）：按逻辑来源、设置和完整内容定位阶段断点；跨进程只重建当前 revision，不借旧运行身份授权正式提交。
- `src/transbridge/application/translation/postprocess.py`：自动接续成功阶段前缀，校验设置及阶段顺序；失败、取消或部分批次不得标成完整阶段。部分批次仍允许有效结果继续后续处理，不能把只有警告的结果错误构造成失败。
- `src/transbridge/application/translation/postprocess_checkpoint.py`：阶段记录保存报告证据和设置指纹，恢复时校验候选完整性并保留接受状态；保留旧格式读取。
- `src/transbridge/application/translation/postprocess_progress.py`：补齐恢复阶段的进度名称。
- `src/transbridge/application/translation/proofread_checkpoint.py`：校对输入以完整内容定位，包含当前候选文本；跨进程 revision 重建不使有效断点失效，保留旧 hash 读取兼容。
- `src/transbridge/ai_translator/translator.py`：局部接入后处理身份、目录、设置和批次检查点，继续使用新运行的提交保护。

### GUI、任务生命周期与结果

- `src/transbridge/ui/tools/ai_translator/task_translation_resume.py`（新增）：worker 读取并复核旧候选，重建逐条复用证据；提供后处理及最终参考复核的稳定检查点。
- `src/transbridge/ui/tools/ai_translator/task_result_recovery.py`（新增）：恢复完整 ledger 和采纳决定，核对 before/after 内容，过滤已完成工作；正式内容已等于已采纳结果时避免重复写入。
- `src/transbridge/ui/tools/ai_translator/run_controller.py`：请求携带恢复记录和项目恢复目录。
- `src/transbridge/ui/tools/ai_translator/source_execution.py`：执行开始接入候选恢复，翻译入口传入阶段恢复参数。
- `src/transbridge/ui/tools/ai_translator/source_reference_review.py`：最终项目参考复核接入批次检查点。
- `src/transbridge/ui/tools/ai_translator/task_name_preparation.py`：恢复名称不重复调用模型；候选保存后、共享名称发布前退出的情况重新进入正常发布流程。
- `src/transbridge/ui/tools/ai_translator/task_recovery_binding.py`：扩大到所有非空项目 AI 任务，捕获原输入及参考快照；大恢复记录避免在 GUI 立即深拷贝；旧任务先进入配置页面。
- `src/transbridge/ui/tools/ai_translator/task_recovery_persistence.py`：后台保存 contract 2、覆盖配置、来源输入、检查点路径、执行链和参考快照；每次执行单独留存输入/参考证据，写状态前保留结果，启动时取得整个恢复链的锁。
- `src/transbridge/ui/tools/ai_translator/task_recovery_prompt.py`：后台合并新清单及旧任务发现；界面使用通用 AI 任务措辞，说明旧任务按当前配置接续；坏结果文件单独标记，保留其他可恢复任务。
- `src/transbridge/ui/tools/ai_translator/task_history_dialog.py`：历史列表支持翻译/混合恢复，展示旧任务说明及具体读取错误。
- `src/transbridge/ui/tools/ai_translator/task_launch.py`：传递恢复记录及目录，恢复原覆盖设置；启动准备期间预留任务，失败和关闭时释放。
- `src/transbridge/ui/tools/ai_translator/task_registry.py`：恢复请求、准备中的启动和已运行任务共同防重，识别原任务/新执行的别名。
- `src/transbridge/ui/tools/ai_translator/task_source_selection.py`：新恢复契约严格核验来源、词条及内容，已删除或修改项不能静默跳过；识别已采纳的正式 after 内容，同时保留老纯校对记录兼容。
- `src/transbridge/ui/tools/ai_translator/task_empty_recovery.py`：无剩余词条的完成路径仍获取逻辑任务锁并重查状态。
- `src/transbridge/ui/tools/ai_translator/task_result_flow.py`：后台恢复结果，保留异步初始化时的结果载荷；只在首次恢复时还原旧决定，重试不覆盖当前选择；预览及应用前持久化结果/决定。
- `src/transbridge/ui/tools/ai_translator/task_run.py`：所有后台工作结束后 dispose 释放恢复锁。
- `src/transbridge/ui/workbench/widget.py`：配置窗口创建失败或返回空时清除恢复请求预留，避免永久显示“正在启动”。

### 验证和文档文件

- `tests/application/translation/test_legacy_task_recovery.py`（新增）：旧报告发现、只读校验链、候选优先级、跨执行输入与再次中断。
- `tests/application/translation/test_task_recovery_results.py`（新增）：原子结果、损坏文件、决定顺序；真实子进程 `os._exit` 后的锁释放。
- `tests/application/translation/test_task_recovery.py`：增加损坏 contract 2 输入隔离，原文件不变且健康任务可读。
- `tests/application/translation/test_proofread_checkpoint.py`：revision 重建可复用，候选内容变化不可混用。
- `tests/contracts/translation/test_postprocess_automatic_resume.py`（新增）：完整阶段自动复用、设置/输入/证据校验；部分批次以及后续阶段异常不会错误推进检查点。
- `tests/ui/tools/test_ai_result_recovery.py`（新增）：恢复预览、采纳/拒绝、已保存内容、异步初始化和不重复模型请求。
- `tests/ui/tools/test_full_task_recovery_ui.py`（新增）：完整翻译/混合任务清单、配置、锁、旧任务配置入口、健康/损坏记录隔离和后台恢复。
- `tests/ui/tools/test_translation_task_resume.py`（新增）：仅处理剩余翻译、恢复名称再发布、暂停取消、阶段和最终复核批次复用。
- `tests/ui/tools/test_recovery_retry_results.py`（新增）：重试不能覆盖用户当前采纳/拒绝决定。
- `tests/ui/tools/test_ai_source_selection_preparation.py`、`tests/ui/tools/test_task_background_records.py`：模拟新进程恢复前显式释放旧执行锁。
- `tests/ui/tools/test_task_recovery_binding.py`：区分旧兼容契约与完整恢复契约，适配锁生命周期及严格输入。
- `tests/ui/tools/test_task_recovery_discovery.py`：更新通用 AI 恢复文案断言并验证恢复发现。
- `tests/ui/tools/test_unified_task_runtime.py`：保留旧纯校对空范围路径的显式兼容验证。
- `scripts/benchmark_ai_task_recovery.py`（新增）：两来源 20,000 条结果的离线保存、全新 store/ledger 恢复和 Qt 心跳测量，包含拒绝决定与待复核证据；自行清理生成目录。
- `docs/dev/ai_translator.md`：替换只支持纯校对的说明，统一任务恢复与旧 ProgressCheckpoint 的职责和生命周期。
- `plans/ai-task-recovery/plan.md`（新增）、`plans/INDEX.md`：记录本轮契约、Story、实现结果与验证。
- `docs/changelogs/INDEX.md`：增加本条记录索引。

## 验证证据

主要最终回归（Windows，Python 3.12.12，现有 uv 环境，无真实 LLM）：

```powershell
uv run --no-sync pytest tests/application/translation tests/ai_translator tests/contracts/translation tests/ui/tools --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology -q -m 'not llm and not integration' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -o faulthandler_timeout=60 --basetemp=.qr26z
```

- **2069 passed，2 deselected，219.95 秒**。两项既有字号/窗口布局用例沿用此前基线排除，不宣称已修复。未运行 LLM/外部集成；未纳入无关 smart_assistant、terminology 子目录。
- 最后完整性矩阵：`uv run --no-sync pytest tests/application/translation/test_task_recovery.py tests/contracts/translation/test_postprocess_automatic_resume.py tests/ui/tools/test_full_task_recovery_ui.py tests/ui/tools/test_ai_result_recovery.py tests/ui/tools/test_task_recovery_binding.py -q --basetemp=.qr26q`，**95 passed，4.11 秒**。覆盖扩大回归收集后补充的 4 个损坏输入用例；与主回归有重叠，不累加。
- 此前恢复矩阵 **223 passed**；发现部分批次问题后，`test_proofread_stage.py`、`test_postprocess_automatic_resume.py`、`test_postprocess_checkpoint.py`、`test_translation_task_resume.py` 联合复验 **52 passed**。
- `uv run --no-sync ruff check src tests scripts/benchmark_ai_task_recovery.py` 通过。
- `uv run --no-sync ruff format --check src tests scripts/benchmark_ai_task_recovery.py` 通过，2118 个文件已格式化。
- 首次更大的 UI 集合停滞后主动停止；历史结果窗口单测 **7 passed**。随后直接相关集合完整结束。较长的临时目录曾导致 Windows SQLite 无法打开测试库，改用根目录短路径后 223 项矩阵通过；失败/停滞运行不计为通过。
- 中途扩大回归曾出现 **1 failed、2066 passed**，定位为部分批次只有警告却被构造为 partial 失败；修正阶段检查点推进与返回结果的边界后，最终 2069 项通过。

大任务：`uv run --no-sync python scripts/benchmark_ai_task_recovery.py --entries 20000`。

- 20,000 条，两个来源：结果保存 **0.522 秒**，全新 store/ledger 恢复 **2.732 秒**，总计 **3.271 秒**。
- 10ms Qt 心跳采样 112 次，最大间隔 **157.49ms**。本机单次离线测量，证明这一保存/恢复路径未造成秒级 GUI 停顿，不代表所有硬件、真实模型耗时或所有 UI 路径。
- 模型调用 **0**；完整恢复后剩余执行项为空，2,000 条拒绝决定及复核理由均保留。

旧数据只读核验：目标项目发现 2 个可接续旧任务；19:43 任务的 BSHeartland / BSAssets 分别得到 7,656 / 8,091 个唯一候选，共 **15,747** 条。候选不等于最终校对完成结果。读取前后历史、恢复和 runtime 文件大小/修改时间保持一致；没有替用户启动真实模型任务或应用译文。

## 责任边界和兼容

新增独立模块均小于 500 行。`translator.py` 已超过 700 行，本次只加构造参数及后处理恢复接线，存储适配和候选恢复放在独立模块；避免为修复恢复功能同时重构大翻译器引入额外风险。若继续向翻译器增加阶段职责，应先提取后处理编排。`postprocess.py` 587 行，主工作负载 8 个方法，本次仅扩展既有阶段检查点职责；`run_controller.py` 571 行只增加请求字段。

原任务、历史报告和候选不做批量迁移。新的 contract 2 清单及带设置指纹的阶段记录由新版本使用；降级版本可能无法读取新阶段格式，因此不能依赖降级来继续新任务。无法证明范围/动作的旧记录仍只读，用户独立修改过的词条会明确阻止旧结果覆盖。

## 遗留项

- Windows 原生 Python 崩溃的根本原因仍需独立定位；本次实现退出后的恢复，不声称修复该原生崩溃。
- 尚未执行真实付费 LLM 或用户项目的正式恢复/写入验收。功能证据来自离线模型桩、进程锁测试、Qt 回归、大任务基准和真实旧检查点的只读核验。

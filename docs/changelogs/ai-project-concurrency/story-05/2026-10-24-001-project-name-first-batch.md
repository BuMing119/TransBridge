# 项目名称首批统计、去重与统一准备

- 日期：2026-10-24
- Epic：`ai-project-concurrency`；Story：S05
- 依据：[实现计划](../../../../plans/ai-project-concurrency/plan.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)、FR5.13.11。
- 归属：对照本轮开始时保存的 `src/tests/plans/docs` 文件基线及 Git 差异，只记录用户补充“最开始统计后统一处理名称”引起的增量。S01～S04 和其他既有未提交修改不计入本记录；没有 commit/push。

## 行为

启用项目共享术语的任务，在任何来源进入后续翻译、后处理或校对前，先扫描本次选中插件的真实名称。相同原文、类别及适用术语约束的名称只准备一次，首批仍按 Token 预算分批并共用任务请求额度。描述、对白、激活提示不属于名称。

已发布译法、插件特例和本来源已有有效译名优先复用。不同类别、不同权威约束及已有多种本地译法的名称分开处理。代表名称在隔离集合中翻译，成功提交证据、保护标记及强制术语校验通过后，才发布无歧义新词；原插件草稿在准备阶段不变。后续各插件按词条身份复用结果，分别走候选持久化、提交和已配置后处理；名称失败传播到对应条目，不再次向模型发送同一失败准备项，其余条目继续。

名称准备有界排队，扫描支持暂停/取消；取消后不放行来源工作流。扫描只用文本术语匹配，不额外初始化向量索引。界面增加“项目名称首批”，显示出现次数、独立名称数、来源进度、失败和实际新增共享术语数。

## 逐文件变更

以下路径相对仓库根目录。

- 新增 `src/transbridge/ai_translator/name_preparation_plan.py`：名称出现位置、约束和分组模型；选中范围、阶段策略、逐条取消检查与保守去重。
- 新增 `src/transbridge/ai_translator/name_preparation_authority.py`：冻结插件有效术语、强制子词及本地已有名称证据；已知异译不跨出现位置合并。
- 新增 `src/transbridge/ai_translator/exact_term_lookup.py`：按调用方原文拼写读取有效名称，包括显式别名与规范化文本；不改变旧 `exact_match` API。
- 新增 `src/transbridge/ai_translator/prepared_names.py`：不可变准备结果、完整 EntryKey/原文/上下文/保护标记校验和候选恢复指纹。
- 修改 `src/transbridge/ai_translator/translator.py`：接入可选准备结果及延后生成术语开关；准备失败不回退模型；成功仍走正常候选提交与后处理。对应准备项不参与旧译播种或自动断点术语修复，显式最终术语修复保留。
- 修改 `src/transbridge/converter/context_categories.py`：提供窄于第一轮的名称分类；修复 `BOOK:FULL` 与 `SCRL:FULL` 被误连成一个字符串的书名分类。
- 新增 `src/transbridge/ui/tools/ai_translator/task_name_preparation.py`：集中扫描、受限并行预翻译、隔离集合校验、统一共享池发布及逐来源结果分发。已知局部译法不在准备阶段提升为全局术语，持久化异常拒绝放行。
- 修改 `src/transbridge/ui/tools/ai_translator/source_execution.py`：准备入口、隔离运行身份、关闭准备阶段后处理、正式阶段结果复用及实际新增术语数归属。
- 修改 `src/transbridge/ui/tools/ai_translator/task_worker.py`：在共享批次执行器创建后、来源调度前增加名称屏障，包含只校对来源的等待。
- 修改 `src/transbridge/application/translation/postprocess_progress.py`：新增首批名称阶段中文标签。
- 新增 `tests/ai_translator/test_name_preparation_plan.py`：20 项分类、去重、阶段、别名、作用域、本地译名、歧义及取消测试。
- 新增 `tests/ai_translator/test_prepared_names.py`：21 项准备结果消费、失败隔离、提交证据、后处理、恢复指纹及旧译播种排除测试。
- 新增 `tests/ui/tools/test_ai_project_name_barrier.py`：6 项 Worker 屏障、暂停取消、共享额度和来源错误隔离测试。
- 新增 `tests/ui/tools/test_ai_project_name_preparation.py`：9 项真实翻译器/共享池/一致性保护集成测试；仅模型 Provider 使用测试替身。
- 新增 `tests/ui/tools/test_ai_project_name_scheduling.py`：3 项有界等待、扫描取消和禁止额外向量初始化测试。
- 修改 `docs/requirements.md`、`docs/adr/003-three-round-translation-strategy.md`、`docs/adr/048-ai-project-concurrency-and-terms.md`：同步项目首批屏障与其余来源阶段并行的契约。
- 修改 `plans/ai-project-concurrency/plan.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`：S05 状态、验证和本增量索引。

## 验证

使用现有 `uv.lock` 与环境，离线运行，不安装依赖，不调用真实模型 API。

```powershell
uv --cache-dir .tmp-ai-name-bootstrap-20261024/uv-cache run --offline --no-sync pytest tests/ai_translator/test_name_preparation_plan.py tests/ui/tools/test_ai_project_name_scheduling.py tests/ui/tools/test_ai_project_name_preparation.py tests/ui/tools/test_ai_project_name_barrier.py tests/ai_translator/test_prepared_names.py -q --disable-warnings --basetemp=.q25/focused
```

结果：59 passed。初次运行缺少 `.q25` 父目录导致测试夹具创建失败，创建任务目录后正常；随后发现测试辅助函数重复传入 `target_entry_ids`，修复辅助函数后全部通过。

```powershell
uv --cache-dir .tmp-ai-name-bootstrap-20261024/uv-cache run --offline --no-sync pytest tests/ai_translator tests/application/translation tests/infra/test_limited_llm_client.py tests/ui/tools --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -q --disable-warnings --basetemp=.q25/full
```

结果：1598 passed、2 deselected，155.10 秒。两项排除项是 S01～S04 已在基线复现的窗口布局失败，详见[上一轮记录](../story-01-to-04/2026-10-24-001-project-concurrency-and-shared-terms.md)。恢复测试需要临时目录硬链接，复用已验证的沙箱外离线测试方式；未触碰用户数据。未运行与本次变更无关的完整仓库测试集。

```powershell
uv --cache-dir .tmp-ai-name-bootstrap-20261024/uv-cache run --offline --no-sync ruff check src tests
uv --cache-dir .tmp-ai-name-bootstrap-20261024/uv-cache run --offline --no-sync ruff format --check src tests
git diff --check
```

全部通过，2034 个文件已符合格式。最终差异按本轮文件基线审查；本任务临时基线、缓存和测试目录在收尾删除。

## 兼容与限制

无项目、关闭检索或纯校对且没有待译名称时沿用既有流程；共享池身份及持久化格式不变，不修改已发布术语。新准备参数默认为关闭，旧候选指纹保持兼容；启用准备时指纹绑定准备结果，避免旧成功记录绕过当前失败。书名进入正确的首轮类别是本次同时修复的行为差异。

名称按字面、类别和已知术语证据去重，不承诺自动识别未提供上下文的所有同名异义。真实模型语义质量、线上速率和节省的实际费用未测。无其他未完成项。

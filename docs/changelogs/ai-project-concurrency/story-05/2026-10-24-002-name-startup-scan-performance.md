# 名称首批启动卡顿修复

- 日期：2026-10-24
- Epic：`ai-project-concurrency`；Story：S05
- 依据：[计划](../../../../plans/ai-project-concurrency/plan.md)、[ADR-048](../../../adr/048-ai-project-concurrency-and-terms.md)、FR5.13.11，以及用户要求“先在词条中去重，再走原术语和翻译流程”。
- 归属：与本轮开始保存的 `src/tests/docs/plans` 文件基线逐文件比较，仅记录本轮启动修复；保留其他未提交修改，不提交、不推送。

## 原因与行为

现场日志显示两个来源的术语加载结束后没有模型调用记录。原名称规划在首次进度之前，对每个独立名称扫描所有术语正则，并经冻结快照路径重复计算整份术语摘要。术语超过 Python 正则缓存容量时持续重编译；实际耗时来自这些前置工作，单纯字面去重不需要如此处理。未取得现场线程栈，因此日志用于定位阶段，离线重现用于验证代码热路径。

新流程先按原文和名称类别线性聚合，保留所有来源身份，再加载术语。插件作用域分别复用精确查找、已验证快照索引和编译后的匹配器，不再逐名称重算摘要。独有且没有旧译复用的名称先进入原翻译路径；完整子术语检查推迟到成功结果缓存及发布之前。重复名称、精确译法和本地旧译仍检查复用约束，插件特例与同名异类不能强行合并。

统计开始即显示日志及来源进度，扫描每 256 条刷新，随后显示加载术语、复用约束检查和结果校验。无名称时不加载首批术语库。暂停、取消、失败传播、正常候选提交及最终术语一致性保护保持有效。

## 逐文件变更

路径相对仓库根目录。

- 修改 `src/transbridge/ai_translator/name_preparation_plan.py`：新增 `NameCandidates`、`collect_project_names` 与 `resolve_project_names`，分离字面统计和术语约束解析；保留首次出现顺序，原 `plan_project_names` 接口兼容。
- 修改 `src/transbridge/ai_translator/name_preparation_authority.py`：按作用域缓存有效术语及发布快照索引，新增可选 `check_required_terms`；轻量和完整结果分别缓存，完整匹配器按需构建。
- 修改 `src/transbridge/ai_translator/required_term_matching.py`：提取可复用 `MandatoryTermMatcher`，一次验证及编译；保留 `match_required_terms` API、词边界、大小写、别名优先级和最长非重叠规则。
- 修改 `src/transbridge/ui/tools/ai_translator/task_name_preparation.py`：统计完成后才加载术语，独有名称延后完整校验，成功结果发布前仍必须遵守冻结术语约束；补充各启动阶段进度。
- 新增 `tests/ai_translator/test_name_preparation_authority.py`：3 项作用域快照、延迟匹配及关闭发布门控回归。
- 修改 `tests/ai_translator/test_required_term_matching.py`：新增 7 项大词库重复匹配、Unicode 大小写和别名优先级测试。
- 修改 `tests/ai_translator/test_name_preparation_plan.py`：新增纯统计先完成、节流进度及分组顺序测试。
- 修改 `tests/ui/tools/test_ai_project_name_scheduling.py`：新增统计早于加载、独有名称延后匹配及无名称不读库测试。
- 修改 `tests/ui/tools/test_ai_project_name_preparation.py`：新增唯一名称错误子术语禁止发布、重复名称插件子术语不同须分别翻译的真实链路回归。
- 新增 `scripts/benchmark_ai_name_startup.py`：离线合成术语及名称，显式隔离源码导入，输出前置检查、后置校验和合计时间，便于复测。本仓库没有 skill 所引用的 `scripts/benchmark_command.py`，使用此专用脚本替代。
- 修改 `docs/requirements.md`、`docs/adr/048-ai-project-concurrency-and-terms.md`：明确先轻量聚合再接既有翻译流程及启动进度。
- 修改 `plans/ai-project-concurrency/plan.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`：同步修复状态、验证及本记录链接；既有增量文件不变。

## 验证

现有 uv 环境及锁文件，Python 3.12.12，Windows 11；无安装、无真实模型 API、无用户翻译数据写入。

```powershell
uv --cache-dir .tmp-ai-name-startup-20261024/uv-cache run --offline --no-sync pytest tests/ai_translator/test_name_preparation_plan.py tests/ai_translator/test_name_preparation_authority.py tests/ai_translator/test_required_term_matching.py tests/ai_translator/test_prepared_names.py tests/ui/tools/test_ai_project_name_barrier.py tests/ui/tools/test_ai_project_name_preparation.py tests/ui/tools/test_ai_project_name_scheduling.py -q --disable-warnings --basetemp=.q26/focused
```

78 passed，2.36 秒。

```powershell
uv --cache-dir .tmp-ai-name-startup-20261024/uv-cache run --offline --no-sync pytest tests/ai_translator tests/application/translation tests/infra/test_limited_llm_client.py tests/ui/tools --ignore=tests/ui/tools/smart_assistant --ignore=tests/ui/tools/terminology --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size1-14]' --deselect='tests/ui/tools/test_ai_mode_switching.py::test_mode_switches_preserve_shell_and_common_field_anchors[size2-10]' -q --disable-warnings --basetemp=.q26/full
```

1613 passed、2 deselected，124.88 秒。排除项为前次已在基线复现的窗口布局失败，参见[首轮记录](../story-01-to-04/2026-10-24-001-project-concurrency-and-shared-terms.md)。恢复测试需要临时目录硬链接，以已允许的沙箱外方式运行；未运行无关完整仓库测试集。

```powershell
uv --cache-dir .tmp-ai-name-startup-20261024/uv-cache run --offline --no-sync ruff check src tests
uv --cache-dir .tmp-ai-name-startup-20261024/uv-cache run --offline --no-sync ruff format --check src tests
git diff --check
```

均通过，2035 个源代码及测试文件符合格式；包含新增基准脚本的 Ruff 检查和格式检查同样通过（2036 个文件）。启动修复另经独立只读审查，未发现明确缺陷。

## 离线性能证据

输入为 1000 条合成已发布项目术语、同一插件作用域 100 个不命中术语的独立名称。每次重新创建 authority，清空正则及术语文本缓存。先执行所有名称前置检查，再执行完整校验；旧版后置阶段命中缓存，新版后置阶段完成延期的匹配，合计对比没有省略校验工作。不包含模型请求、文件读取及完整应用启动。

测试结束后，使用交付脚本交替启动基线与修改后进程，各 5 次。源码路径被置于导入列表首位并断言实际模块路径。实际命令为：

```powershell
1..5 | ForEach-Object {
    .venv/Scripts/python.exe scripts/benchmark_ai_name_startup.py --source .tmp-ai-name-startup-20261024/src --samples 1 --terms 1000 --names 100
    .venv/Scripts/python.exe scripts/benchmark_ai_name_startup.py --source src --samples 1 --terms 1000 --names 100
}
```

- 基线合计秒数：4.218070、4.037450、4.013403、4.061235、4.428098；中位 **4.061235 秒**，范围 **4.013403～4.428098 秒**。
- 修改后合计秒数：0.086496、0.081737、0.082435、0.098726、0.087146；中位 **0.086496 秒**，范围 **0.081737～0.098726 秒**。
- 修改后前置检查中位 **0.021528 秒**（0.020442～0.024730），后置完整校验中位 **0.061993 秒**（0.060360～0.074365）。
- 初次并行 QA 期间及临时 runner 的测量不纳入上述正式对比。

另将预构建的 20000 条名称平均分到两个来源：原文 `Name {i % 10000}`，上下文 `WEAP:FULL`，每个名称重复两次；仅计时 `collect_project_names(tasks, overwrite=False)`，不含对象创建、术语和 UI 回调。5 次秒数为 0.073761、0.063217、0.079119、0.078302、0.064531，中位 **0.073761 秒**，范围 **0.063217～0.079119 秒**。每轮校验 10000 组且每组出现两次，说明字面统计本身可快速完成。

## 兼容与限制

无数据格式迁移，不改变共享池、检查点或公开翻译接口。已启动的程序不会自动加载代码修复，重启后生效。真实模型吞吐、语义质量、完整用户项目耗时未测；上述数字是固定合成输入的本地热路径结果。缓存与测试目录及基线在收尾删除，不删除用户项目数据；复测旧版本须自行保存相应源码基线。无其他未完成项。

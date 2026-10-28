# 固定输入、独立成果与局部合入 QA

- 日期：2026-10-26。
- 范围：[实施计划](../../plans/task-result-lifecycle/plan.md)、[ADR-049](../adr/049-task-result-lifecycle.md)、FR19.15。
- 环境：Windows、Python 3.12.12、仓库现有 uv 环境。Qt 使用 offscreen；云端与模型调用使用测试替身或本地合成 HTTP 服务。
- 基线：`2058215e309f35b377737687616a3569d222ce24`。用户已有 `pyproject.toml`、`uv.lock` 的版本修改及 `.transbridge/` 数据不属于本次工作。
- 状态：本地实现与相关验证完成；扩大测试的既有失败、环境问题和未执行边界见下文，不宣称全仓测试全绿。

## 行为与故障覆盖

公共三方合入测试覆盖相同值、仅当前改变、仅成果改变、双方不同修改、译文与状态联动、原文/上下文变化、删除、重复应用和过期手工决定。项目层使用最新目标状态和原子 CAS；授权回调不执行时不能谎报成功，回调重复调用不能重复提交，失败回执保留原错误及计数。

AI 真实 V2 路径覆盖启动、成果整理、先保存候选、后台合入、结果窗口重开和覆盖前恢复；旧工程通过独立适配执行同一比较规则。存储失败保留内存结果；结果保存、当前工程保存和实际合入回执是不同状态。审阅后发生的新编辑重新比较，不被旧选择无条件覆盖。

下载覆盖跨来源部分成功、单来源多个文件的后续失败/403/取消、内存成果另存、源文件与云端不可访问时重开已有成果、重复恢复方向、显式选择失效、保存失败重试不重读成功来源、实际磁盘与工作台投影一致。未完整读取的来源明确标为不完整，不自动当作完整快照合入。

退出覆盖旧轮询、旧保存回调，以及确认草稿、确认记录丢弃、选择备份路径的嵌套事件循环。取消后重开退出不能继承旧决定；关闭其他窗口期间发生同样交错也不会误关新会话。

## 独立审查发现与修复

1. 提交前备份不能证明已经覆盖。AI 和下载均拆分尝试证据与实际回执；注入真实 V2 提交失败后继续编辑，再打开恢复界面，不会用失败尝试覆盖新编辑。
2. 退出入口的回调检查不足以保护嵌套对话框。每次退出携带独立 attempt，跨嵌套确认后重新核验；第二次保存未完成时，第一次保存成功不能关闭窗口。
3. 公共合入失败分支重建 OperationResult 时丢失失败计数，导致真实磁盘错误变成通用输入错误。改为透传原失败回执，保留原诊断和计数。
4. AI 后置全局检查曾将已经完成的写入误报为未应用。成功回执按实际提交接收；身份切换后不向新工程发旧结果通知。
5. 报告载荷清理与更新元数据存在并发窗口。报告读写及元数据更新使用同一锁，先发布新引用，再删除本任务被取代的载荷。

## 已执行检查

各组有重叠，不将数字简单相加当作独立用例总数。命令中的缓存、basetemp 仅为本任务测试目录，结束时清理。

### 后端、持久化及设置

```powershell
uv run --offline --no-sync --cache-dir .tmp-task-results-root/uv-cache pytest tests/application/translation tests/persistence/v2 tests/config tests/bootstrap -q -p no:cacheprovider --basetemp=.tmp-tr-backend-final --tb=short --show-capture=no
```

最终扩大运行 **706 passed、1 failed**。失败为未修改的 `test_candidate_checkpoint_journal.py::test_compaction_and_batch_serialization_scale_with_new_candidates` 在 Windows `os.replace` 报 `WinError 5`；之前 76 项子集运行中该项通过，随后单项复验仍出现权限错误，不能称整组全绿。第一次沙箱内运行的硬链接权限失败在沙箱外复验消失；原并发 create 测试的锁内 Barrier 已迁为入口同时启动，保留一成功一 FileExistsError 的实际合同。

归档 SQLite 测试最初使用沙箱默认 TEMP 无法打开数据库，改为本任务可写 TEMP 后通过。未为测试环境错误降低生产原子写入保护。

### 项目命令与同步协议

```powershell
uv run --offline --no-sync --cache-dir .tmp-task-results-root/uv-cache pytest tests/application/projects tests/contracts/paratranz tests/paratranz/test_batch_download_snapshot.py -q -p no:cacheprovider --basetemp=.tmp-tr-project --tb=short --show-capture=no
uv run --offline --no-sync --cache-dir .tmp-task-results-root/uv-cache pytest tests/contracts/paratranz/test_terms_api_contract.py -m 'not integration' -q -p no:cacheprovider --basetemp=.tmp-tr-http --tb=short --show-capture=no
```

扩大运行 **521 passed、10 failed、1 skipped**。9 个失败来自沙箱禁止 127.0.0.1 HTTP，沙箱外仅本地合成协议文件 **12 passed、1 deselected**，排除真实账号 integration。另 1 项为既有来源排序断言，预期 `context_order_changed`，实际 `upload_association_confirmed`；本任务未改其规划逻辑。

### AI 结果合同与退出

AI 五文件收口 **98 passed**：`test_ai_task_session.py`、`test_ai_task_async_apply.py`、`test_ai_session_preparation.py`、`test_ai_source_selection_preparation.py`、`test_translation_workflow_closure_audit.py`。通过真实 evidence store 和 TaskRuntime 验证成功只确认一次实际回执、失败不确认、提交前取消不发布。

退出与恢复四文件 **41 passed**：`test_cancel_application_close.py`、`test_ai_task_shutdown.py`、`test_runtime_task_shutdown.py`、`test_save_recovery.py`。设置/config/bootstrap 的前期相关组合 **62 passed**，包含键盘导航与设置取消不写盘。另有 **52 passed** 的设置和退出组合，两组有重叠。

### 扩大 UI 回归及后续收口

AI 实现 Agent 执行：

```powershell
uv run --no-sync pytest tests/ui/tools tests/ui/test_ai_task_shutdown.py tests/application/translation/test_task_history.py tests/application/translation/test_task_recovery_results.py -q --tb=short --disable-warnings --ignore=tests/ui/tools/test_ai_task_session.py --ignore=tests/ui/tools/test_ai_task_async_apply.py --ignore=tests/ui/tools/test_ai_session_preparation.py
```

结果 **1708 passed、20 failed、12 skipped**，679.48 秒。该结果是收口前证据，未将整组写成通过。后续 13 个本次合同变化引起的失败已更新为固定输入、成果可重开和稀疏合入的实际断言；没有删除身份、原文变化、取消及磁盘失败保护。`full_task_recovery_ui`、`task_recovery_discovery`、`ai_result_lifecycle` 联合 **51 passed**；mixed 文件 **5 passed**；旧 closure mock 已迁到真实 authoritative port 并包含在 98 项合同验证中。

其余 7 个失败的处置：两个窗口字号锚点断言已有仓库历史基线记录；一个收尾进度时序断言单独复验通过；一个 proofread SQLite 和一个 translation resume 用例使用短工作区临时路径后通过；两个 workflow_progress 的 mock 不接受 `max_concurrent`，在独立 HEAD 归档同样复现，未修改无关代码。translation resume 的长路径失败也在 HEAD 归档复现，SQLite 文件路径 253 字符，加 journal 后缀超过 Windows 路径限制；当前与 HEAD 的短路径分别通过。

主会话的 operations、设置、退出组合 **640 passed、17 failed**，46.18 秒；旧下载合同断言及错误传播问题随后修复，61 项针对性回归通过。完整 `tests/ui/operations` 的 **550 个不同用例均取得通过证据**：50 个文件分别启动进程，初次 529 通过、21 个临时目录权限失败；将 `tempfile.tempdir` 指向工作区临时目录后，涉及的三个文件共 40 项原样复验通过。一次单进程扩大检查在未改动的 `theme_service.close` 出现 Qt 原生崩溃，对应 layout_stability 文件独立 7 项通过；不宣称整组单进程稳定性已修复。

第一次覆盖整个 tools 的主会话尝试在不相关智能助手测试期间长时间停留，主动停止；第二次与 Agent 扩大检查重复的 UI 尝试也主动停止。这两次没有最终结果，不计入通过数。

### 最终核心联合验证

```powershell
uv run --offline --no-sync --cache-dir .tmp-task-results-root/uv-cache pytest tests/application/translation/test_result_merge.py tests/application/projects/test_project_result_merge.py tests/config/test_task_result_preferences.py tests/ui/test_task_result_settings.py tests/ui/test_cancel_application_close.py tests/ui/test_ai_task_shutdown.py tests/ui/test_runtime_task_shutdown.py tests/ui/test_save_recovery.py tests/ui/tools/test_ai_task_session.py tests/ui/tools/test_ai_task_async_apply.py tests/ui/tools/test_ai_session_preparation.py tests/ui/tools/test_full_task_recovery_ui.py tests/ui/tools/test_task_recovery_discovery.py tests/ui/tools/test_ai_result_lifecycle.py tests/ui/tools/test_mixed_completion_integrity.py tests/ui/operations/test_batch_download_results.py -q -p no:cacheprovider --basetemp=.tmp-tf --tb=short --show-capture=no
uv run --offline --no-sync --cache-dir .tmp-task-results-root/uv-cache ruff check src tests scripts/benchmark_variant_snapshot.py
uv run --offline --no-sync --cache-dir .tmp-task-results-root/uv-cache ruff format --check src tests scripts/benchmark_variant_snapshot.py
git diff --check
```

核心联合 **227 passed**，11.00 秒；Ruff、格式（2236 个文件）及 diff 检查通过。警告主要来自既有兼容入口的直接 TranslationEntry 修改弃用提示。设置页面另用真实 Qt 离屏渲染检查，无文字遮挡或控件重叠。

最后补齐显式接续的同进程 lease 转交：仅旧运行已经停止、持久化无错误且没有活跃 Review 时退役旧 owner；新恢复绑定可取得同一任务的 lease。真实 lifecycle 文件 **15 passed**，覆盖 completed/cancelled 和四类未耐久错误保护。后续 316 项联合 **315 passed、1 failed**，失败是 `test_ai_task_action_flow.py::test_closing_during_application_or_save_does_not_interrupt_completion[save]` 末尾结果按钮状态不符，保存及提交断言已经通过。该节点单独复验通过，完整 action_flow 文件 **12 passed**；只增加失败诊断信息，未弱化断言或加 sleep。原因尚未证实，不能归为已修复的环境问题，也不宣称 316 项整组全绿。

独立 HEAD 归档复验确认来源排序断言和 checkpoint 原子替换 WinError 5 同样存在；checkpoint 在沙箱内、沙箱外均可复现。归档仅包含跟踪的源码、测试、配置及测试 data，未复制用户数据，验证后清理。

## 性能证据

```powershell
uv run --offline --no-sync --cache-dir .tmp-task-results-root/uv-cache python scripts/benchmark_variant_snapshot.py --baseline 2058215e309f35b377737687616a3569d222ce24 --entries 10000 --reads 100 --repetitions 5
```

脚本用 `git show` 在内存载入基线模块，不切换工作区、不读取用户工程。同一份 10,000 条合成数据连续读取 100 次，5 轮：

- 基线每轮新建 100 个 snapshot；耗时中位 **0.4437518 秒**，范围 0.4245198～0.4575326 秒。
- 新实现每轮新建 **0 个** snapshot；耗时中位 **0.00000380003 秒**，范围 0.00000370003～0.00000609993 秒。

这只衡量未变化聚合的重复 snapshot 读取，不表示整个软件或真实 AI/下载的提速倍数。实际内容提交和正式文件保存仍可能遍历完整版本；本轮移除的是重复构造、反复源文件校验和不必要的整项目历史。

## 职责、兼容与限制

- 新公共比较、项目合入、源读取、成果存储、恢复审阅、设置页和退出提示各自独立；AI TaskSession 的新增职责拆出，下载 Session 保持在 500 行以内。
- 现有较大 `lifecycle.py` 只修原保存/发布责任内的身份与 no-op 边界；`context.py` 只注入设置 provider；`gui_facade.py` 新命令委派独立服务；`variant.py` 只改变已有 snapshot 的复用。未把新成果职责塞进这些大模块。后续新增生命周期责任前应提取保存/发布事务边界，本次避免与整个工程生命周期同时重构带来额外风险。
- 不改变现有工程文件格式，不破坏性迁移、不删除手动历史。新增成果独立保留；回退代码前应保留这些文件。旧结果缺少可靠 base 时只读，不猜测覆盖。
- 未调用付费模型或真实云端，未执行用户工程数据验证、打包、提交或推送。用户桌面实际体验仍需后续使用验证。

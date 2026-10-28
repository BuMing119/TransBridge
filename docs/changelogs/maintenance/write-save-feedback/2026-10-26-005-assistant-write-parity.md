# AI 写回补齐可选 BSA、统一目录与明确来源批量

- 日期：2026-10-26
- Epic / Story：maintenance / write-save-feedback（非计划维护）
- 入口：用户接受本会话提出的三项 AI 工具补齐建议，并调用 bm-pilot 授权完成实现、验证与记录。

## 对外行为

保留一个 `writer.write_back` 工具及其 admin、确认、授权目录要求，新增可选参数：

- `pack_bsa` 默认 `false`，输出散装 STRINGS；显式 `true` 打包本地化资源，混合批量中的 XML 保持原格式。没有可打包资源时明确拒绝。
- `output_dir` 适用于全部既有格式，按来源原名集中另存，支持创建授权范围内的嵌套目录。单文件 `path` 保留优先级；兼容 `target=strings` 必须提供目录且优先目录的旧规则。
- `source_keys` 从 `list_collections` 的 key 明确选择一个或多个来源，不切换活动集合，省略仍处理当前来源。空、重复、未知 key 和无效快照拒绝执行；多个来源不能共用单一 `path`。
- `target=auto` 仅用于显式选择来源，按各来源自己的格式处理插件与 XML 混合批量。

所有来源和预期输出先固定并授权，批量复用同一份冻结译名方案。独立来源的覆盖/输出冲突等预检失败保留回执，其他可写来源继续；全部阻断则不建任务。单文件启动结果保持 `{task_id,path}`，批量新增 `{task_id,paths,source_keys}`，终态含带来源 key 的 `outcomes`、实际 artifacts 和诊断。取消或部分失败保留已提交输出，不自动重放任务。

整批输出一次捕获文件撤销凭证，涵盖实际可执行的插件、BSA、STRINGS、XML 目标。真实 Session/请求/任务链验证可恢复旧插件及 BSA，并删除本轮新建 XML。无持久化 schema 迁移，无依赖升级，无 Git 提交。

## 逐文件变化

- 新增 `src/transbridge/application/io/operation_write_batch.py`：将既有逐来源执行、输出冲突、部分完成/取消计数和回执聚合从 UI 提取到纯应用层，供界面与 AI 共用；未复制第二套发布协议。
- 修改 `src/transbridge/ui/operations/write_batch.py`：保留界面请求、另存目录编辑和计划呈现，引用应用层批量实现；兼容既有导入名。`_HydratedWriteBatchWorkload` 别名仍服务旧 UI/测试调用，全部调用迁移至应用层公开名后可移除。
- 新增 `src/transbridge/smart_assistant/tools/write_back_preparation.py`：负责明确来源选择、参数约束、来源及译文冻结、统一输出路径、完整目标授权、冻结投影预检，以及本次撤销目标集合。
- 修改 `src/transbridge/smart_assistant/tools/tool_writer.py`：保留工具名，注册新参数及完整描述，选择单文件或共享批量 workload；保留旧单文件结果形状，批量补充逐来源结果；实际文件集合交给原有撤销捕获。
- 修改 `src/transbridge/smart_assistant/guardrails/input_validator.py`：只对 write_back 复用相同输出授权函数，令 guard 与 writer 对新建嵌套目录、相对路径和授权根保持一致；其他工具保留原规则。
- 修改 `src/transbridge/smart_assistant/tools/task_manager.py` 的 `notify_finished`：取消中的终态转换尚不能完成时，保留 worker 已提供的文件结果，待 worker 结束后通知，避免先取消导致丢失已写出的文件及逐来源回执。仅修复既有通知职责，不向这个已有大模块新增职责或方法。
- 新增 `tests/smart_assistant/tools/test_write_back_options.py`：26 项真实写回回归，覆盖三格式统一目录、明确来源与当前集合不变、无活动集合的显式单来源、非法选择/授权/参数、部分失败、全失败、取消前/部分提交后回执、BSA 与散装实际文件逆操作、工具契约、嵌套目录、重名阻断及旧单文件 path 优先级。
- 新增 `tests/smart_assistant/tools/test_write_back_request_undo.py`：真实请求接纳、Session 凭证和 TaskRuntime 共同验证一批 BSA/插件/XML 的单次捕获及撤销。
- 修改 `docs/dev/ui.md`：在写回功能下说明 AI 参数、默认值、输入限制、结果形状和撤销行为。
- 修改 `docs/dev/task-runtime-compat.md`：更新 writer 的调用状态，指向共享应用层能力。
- 修改 `docs/changelogs/INDEX.md`：只增加本记录入口，历史增量不变。

工作区原有的工程、下载、AI 活动界面、依赖及其他文档变化不归入本记录。对启动时已修改的 UI 批量模块和文档保留副本核验归属；临时目录在收尾删除。

## 验证

沿用 uv 环境和锁文件；TEMP、TMP、UV_CACHE_DIR、pytest basetemp 指向独有 `.tmp-aiwrite-92bd/`，避免沙箱系统缓存权限问题。

修改前旧 AI 写回及发布边界基线：**23 passed**。共享执行提取后的兼容聚焦回归：**106 passed**。新增功能及真实请求撤销验证：

```powershell
uv run --no-sync pytest tests/smart_assistant/tools/test_write_back_options.py tests/smart_assistant/tools/test_write_back_request_undo.py -q --basetemp .tmp-aiwrite-92bd/e
```

结果：**27 passed**。

扩大回归：

```powershell
uv run --no-sync pytest tests/smart_assistant/tools/test_safe_write_back.py tests/smart_assistant/tools/test_write_back_publication_boundary.py tests/smart_assistant/tools/test_write_back_options.py tests/smart_assistant/tools/test_write_back_request_undo.py tests/smart_assistant/test_input_validation_guard.py tests/smart_assistant/test_tool_consolidation.py tests/smart_assistant/postprocess/test_task_manager.py tests/smart_assistant/test_runtime_worker_controls.py tests/smart_assistant/test_request_execution.py tests/smart_assistant/test_undo_admission.py tests/application/assistant_requests/test_file_undo_capture.py tests/application/assistant_requests/test_round_undo_domains.py tests/ui/operations/test_strict_write_routes.py tests/ui/operations/test_batch_output_conflicts.py tests/ui/operations/test_write_save_as.py tests/ui/operations/test_bsa_output_options.py tests/ui/operations/test_plugin_transfer_ui.py tests/ui/operations/test_production_facade.py tests/ui/operations/test_write_execution_feedback.py tests/integration/publish -q --basetemp .tmp-aiwrite-92bd/r
```

结果：**446 passed、1 skipped**，33.73 秒，退出码 0。包括原有界面路径、BSA 回读与发布恢复、任务控制、请求接纳、路径 guard 和撤销回归；聚焦轮次与之重叠，不累计为独立总数。

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
```

最终全库静态检查及 2200 文件格式检查通过；本次文件的 `git diff --check` 通过。中途全库检查曾遇到其他工作线 `test_ai_task_activity.py` 的 F811，未修改该文件；最终工作区复验已通过。

## 遗留与未验证项

本次功能无未完成项。未运行全仓库 pytest、真实 LLM 生成调用、真实用户数据写回或安装包构建。批量与单文件仍使用应用既有安全提交和撤销边界，未承诺整批跨文件操作具备 OS 级全局原子性。

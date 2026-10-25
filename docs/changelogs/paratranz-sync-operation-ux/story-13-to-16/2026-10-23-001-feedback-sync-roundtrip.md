# ParaTranz 反馈修复：主动对照、数据确认及准确往返

- 日期：2026-10-23
- Epic / Story：`paratranz-sync-operation-ux` / S13～S16
- 计划：[plan.md](../../../../plans/paratranz-sync-operation-ux/plan.md)
- 需求：FR22.3、FR22.3.1～FR22.3.3、FR22.4

## 问题与交付

用户提供的检查日志包含 189 条待处理项，其中 177 条为重复候选、12 条为原文不符；同一任务日志存在完全相同原文跨序号的证据。执行前完整云端读取耗时约 118 秒，超过原有默认 60 秒确认时限。执行准备阶段失败只在任务中心留下通用失败码，缺少可关联的具体执行原因。

本轮改为用户选好项目与同步方式后点击“开始对照”；操作或数据变化才要求重新检查，单纯等待不会使 ParaTranz 确认超时。确认仍绑定所有者、方向、目标、选项和快照且只能消费一次；编辑、取消和开始新一轮对照都会撤销旧确认。其他操作继续使用原有确认时限。

下载时，相同原文、译文、补充说明及翻译状态的重复候选可采用共同内容；不同内容按组显示并展示状态差异，例如 53 条已翻译与 1 条已审核显示为两种选择。共同内容不证明唯一目标，因此不保存任意云端 ID。`QUST:CNAM` 仅在同一任务记录内按完全相同原文查找跨序号对应项；唯一匹配保留真实 ID，后续上传复用。失效关联、重复 ID、目标占用仍需明确处理。上传已有记录只写译文与状态，保持云端原文、上下文、key 和顺序不变。

GUI 更新已有条目保留工程原文、上下文及来源元数据；只更新译文、状态和已确认关联，不写回 ESM/ESP。旧集合的新增/删除仍按其原有独立计划列出，V2 工程保持来源条目集合不变。所有实际写入仍通过已有 TaskRuntime、恢复点和本地事务。

现有日志新增 `PARATRANZ_SYNC_EXECUTION`，按 `run_id`、`plan_id` 关联准备、恢复点、提交权限、执行、清理、结果及失败阶段。准备阶段失败也产生可显示的结果；已提交结果不因清理异常丢失。异常类型、脱敏原因、堆栈和结果数量可追踪。限定身份中的原文仅记录指纹和长度，包括异常链中的原文也作剔除。检查日志保留分组前全部候选及 ID，增加组数、组大小和译文指纹；没有报告或导出功能。

## 文件增量

本轮启动时工作区干净；后续同时出现的 AI 翻译及其他 UI 工作不属于本记录，未修改其代码或清理其临时目录。

- `application/security/hitl.py`：可选无时间到期的确认及显式撤销；默认 60 秒保持兼容。
- `application/sync/use_case.py`、`ui/operations/plan_presenter.py`：同步采用无倒计时确认；重检立即撤销旧授权，保留请求和快照校验。
- 新增 `application/sync/execution_diagnostics.py`；修改 `task_adapter.py`、`executor.py`：执行阶段日志、异常分类、准备失败结果和已提交事实保护。
- 新增 `application/sync/candidate_matching.py`；修改 `download_planner.py`、`download_review.py`、`upload_planner.py`、`planner.py`：精确任务原文索引、共同内容分组、引用保护及 GUI 来源保留选项。
- `application/sync/local_candidate.py`：按来源范围及内容摘要索引分组结果，保留本地来源和顺序，不生成任意关联；避免同任务大量分组重复扫描。
- `paratranz/sync_diagnostics.py`：分组与原始候选数量、全部 ID、译文指纹及相等证据，保持正文不进入日志。
- `ui/operations/paratranz_dialog.py`、`sync_preflight_presentation.py`、`mappers.py`：主动开始对照、选项失效、工程更新文案与可关闭的只读检查。
- `ui/operations/paratranz_sync.py`、`sync_preparation.py`、`production_support.py`：生产组合接入来源保留、更新范围及正式提交。
- `ui/operations/sync_review_detail.py`、`sync_review_dialog.py`、`sync_review_direction.py`、`sync_review_list.py`、`sync_review_projection.py`、`sync_review_view.py`：分组选项、状态可见、补充说明来源、准确后果提示和随字体变化的列表行高。
- 新增 `tests/contracts/paratranz/test_sync_candidate_matching.py`、`test_sync_execution_logging.py`：匹配/分组/身份/复杂度和日志异常边界。
- 更新同目录 `test_download_resolution.py`、`test_upload_resolution.py`、`test_sync_plan_confirmation.py`、`test_sync_diagnostics_logging.py`：兼容、往返、确认及全部候选日志。
- 新增 `tests/ui/operations/test_paratranz_feedback_roundtrip.py`、`test_sync_manual_comparison.py`：真实 V2 下载→编辑→上传→保存重开，以及手动对照/关闭/迟到结果。
- 更新同目录 `test_operation_plan_presenter.py`、`test_paratranz_download_authority.py`、`test_paratranz_download_review_flow.py`、`test_paratranz_sync_dialog.py`、`test_paratranz_upload_roundtrip.py`、`test_sync_review_dialog.py`、`test_sync_review_layout.py`：来源保护、确认撤销、状态分组及窗口布局回归。
- 更新需求、ADR-045、计划及对应索引。上述相对代码路径位于 `src/transbridge/`。

新增匹配和执行日志职责独立；主同步窗口 481 行、21 个方法，执行器 399 行。所有本轮生产模块均未超过 500 行或 30 个方法，无新增超限责任。持久化和 EntryKey 格式不变，不需要迁移。

## 验证证据

使用现有 Python 3.12 / uv 环境，不安装或更新依赖。先执行聚焦测试并修复旧文案断言，再运行最终联合回归：

```text
uv run --no-sync --no-cache pytest tests/contracts/paratranz tests/ui/operations tests/paratranz tests/contracts/security/test_tool_schema_security.py tests/security/test_data_boundaries.py tests/ui/test_logging_setup.py tests/smart_assistant/tools/test_paratranz_sync_plan.py tests/smart_assistant/tools/test_paratranz_tools.py -q --tb=short -rs
.venv/Scripts/ruff.exe check src/transbridge/application/security/hitl.py src/transbridge/application/sync src/transbridge/paratranz/sync_diagnostics.py src/transbridge/ui/operations tests/contracts/paratranz tests/ui/operations
.venv/Scripts/ruff.exe format --check src/transbridge/application/security/hitl.py src/transbridge/application/sync src/transbridge/paratranz/sync_diagnostics.py src/transbridge/ui/operations tests/contracts/paratranz tests/ui/operations
.venv/Scripts/ruff.exe check src tests
.venv/Scripts/ruff.exe format --check src tests
git -c core.safecrlf=false diff --check
```

- 最终联合回归：**625 passed, 2 skipped**，35.65 秒。跳过项为未配置真实 ParaTranz 测试账号和 Windows 无符号链接权限；11 项既有弃用警告。
- 覆盖模拟每次 118.147 秒云端读取、用户等待一小时后确认，两方向均能执行；本地/远端变化、目标变化和旧确认仍被拒绝。
- 覆盖 54 条等价候选直接下载、53+1 状态分组、无任意关联、明确指定上传目标、任务日志错位、保存重开及云端原文/上下文不变。
- 执行日志 14 项回归覆盖准备、恢复点、权限、执行、清理、快照错误、取消、原文限定身份和异常文本转义；检查日志保留全部分组成员。
- 200 条本地任务日志/400 条远端的复杂度回归验证构建及重复查询仅产生 1,200 次文本哈希；执行查询使用索引。
- 已检查真实 Qt 主窗口与复核窗口在 640～1120 像素及 9pt/12pt 字体下的截图，修复截断和行高问题；本轮专属截图目录已清理。
- 相关 **92 个文件** Ruff 检查、格式检查通过。全库检查最后仅有其他工作中 `tests/ui/test_project_creation_reuse.py` 和 `tests/ui/test_safe_drop_binding.py` 的 3 条长行/2 个文件格式问题；保持其内容不变。uv 静态检查遇沙箱临时缓存权限问题，改用同一 `.venv` 的 Ruff；文件/本地回环夹具使用提升权限的本地测试执行。
- 标准 Git 差异空白检查通过。未运行全库 pytest、真实云端联机写入、安装包构建、发布或 Git 提交。

## 边界

这次修复不按相同译文猜测上传目标，也不以模糊原文匹配任务日志。无法唯一确定上传目标时仍需明确选择；不同内容继续暂缓并保持目标端不变。实际项目尚需用更新后的程序重新对照，旧日志未包含完整译文，不能据此声称全部 189 条均已自动解决。新日志可直接核验剩余原因及具体执行失败。

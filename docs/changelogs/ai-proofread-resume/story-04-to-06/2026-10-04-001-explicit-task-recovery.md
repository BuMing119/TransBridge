# 明确校对任务恢复与独立重跑

- 日期：2026-10-04
- Epic：ai-proofread-resume；Story：S04～S06
- [计划](../../../../plans/ai-proofread-resume/plan.md)

## 问题与结果

原先以项目、版本和输入匹配共享校对候选，新建任务也默认复用，用户无法判断继续的是哪一次。本次将候选按 task_id 隔离，新任务默认不复用；只有选择明确的历史任务才允许继续。项目重新打开时，后台查找中断的校对任务，显示时间、来源和已保存候选数，提供“继续任务／重新开始／稍后处理”。选择继续或重新开始先打开配置窗口，由用户启动模型请求。

继续任务保留原任务的来源和 EntryKey 范围，恢复非敏感临时参数并保留当前凭据，不写全局设置；候选仍需核验输入、配置和术语。重新开始使用当前配置和独立 task_id。完成、主动取消与程序关闭中断分开记录，前两者不自动提示，历史中仍可重新开始。

工作区已有大量未提交变更，本记录仅归属以下符号级增量；此前自动应用、保存提示、实时日志与性能修复不重复计入本次。

## 变更文件

- 新增 `application/translation/task_recovery.py`：原子任务清单，身份、状态与完整范围校验，拒绝凭据字段，创建不覆盖既有记录；路径以任务 ID 的哈希生成。
- 修改 `application/translation/proofread_checkpoint.py`：可选任务命名空间和历史候选计数；未带任务 ID 的旧库保留兼容读取接口，桌面新任务不再使用旧共享命名空间。
- 新增 `ui/tools/ai_translator/task_recovery_binding.py`：配置恢复、原范围绑定、恢复身份校验、已保存应用结果的精确跳过，以及中断/取消/完成状态写入。
- 新增 `ui/tools/ai_translator/task_config_snapshot.py`，修改 `config_presenter.py::restore_task_config`：去除凭据的执行快照，含临时参数和所有流程配置；只还原任务草稿。
- 修改 `run_controller.py::TranslationRunRequest`、`task_runtime.py::start_task`、`source_execution.py::_polish`：新任务默认关闭复用，显式继续携带原 task_id，重新开始获得独立命名空间。
- 修改 `task_run.py`：创建任务恢复绑定、记录恢复状态、区分主动取消和程序退出；写入异常明确显示。
- 修改 `single_task_view.py`：移除默认续跑勾选框，说明新任务不复用；修改 `ai_translator_window.py` 与 `_window_actions.py`：恢复范围参与估算，延迟刷新保留“继续任务／重新开始”按钮文案。
- 新增 `task_recovery_prompt.py`，修改 `task_registry.py`、`task_history_dialog.py`、`ui/workbench/widget.py`：后台发现、非阻塞提示、历史操作、版本隔离、重复任务防护、退出等待扫描和通过现有 Intent 注入运行时。
- 新增 `tests/application/translation/test_task_recovery.py`、`tests/ui/tools/test_task_recovery_binding.py`、`test_task_recovery_discovery.py`；扩展 checkpoint、resume wiring、runtime、layout 测试。真实窗口测试覆盖配置还原、延迟刷新和两个启动按钮。
- 修改 `tests/ui/test_workbench_slices.py`：原进度窗口测试替身补齐现有 registry 合同，并验证先注册再延迟激活。
- 更新本计划及两个对应索引；以上相对生产路径均以 `src/transbridge/` 为根。

## 验证

- `.venv/Scripts/python.exe -m pytest tests/ui/tools tests/ui/test_ai_task_shutdown.py tests/application/translation/test_task_recovery.py tests/application/translation/test_proofread_checkpoint.py -q --tb=short --basetemp=.tmp-explicit-recovery-suite`：703 项通过。
- `.venv/Scripts/python.exe -m pytest tests/ui/test_workbench_slices.py tests/ui/test_workbench_story07.py tests/ui/test_workbench_theme_migration.py tests/ui/test_modern_workbench_visual_shell.py tests/ui/characterization/test_workbench_contract.py tests/ui/tools/test_unified_task_runtime.py tests/ui/tools/test_task_recovery_binding.py tests/ui/tools/test_unified_task_config.py tests/ui/tools/test_task_recovery_discovery.py tests/ui/tools/test_ai_task_history_ui.py -q --tb=short --basetemp=.tmp-explicit-recovery-final`：105 项通过。此前工作台扩展检查发现旧测试替身缺少 registry，补齐后通过。
- 独立 QA 发现临时任务参数未恢复的问题，已补配置快照及真实窗口回归。后续最终验证见计划交付记录。
- `.venv/Scripts/python.exe -m ruff check src tests`、`.venv/Scripts/python.exe -m ruff format --check src tests`、`git -c core.safecrlf=false diff --check`：通过。
- 使用现有 uv 管理的 `.venv`，未更改依赖；测试有既有 SWIG 和兼容 API 弃用警告。

## 兼容与限制

- 明确恢复入口只为启用默认校对且范围不含翻译条目的任务创建；含翻译步骤的任务不能据此声称完整续跑。
- 旧共享候选和旧报告保留，不自动推断归属或迁移为新任务。新任务清单不保存 API 密钥。
- 未运行真实 LLM API、整仓 pytest、安装包构建或替换已安装程序；交付为本地源码修改，未 commit/push。
- 职责复核：`run_controller.py` 561 行，本次仅修改请求数据字段；恢复存储、绑定、配置快照、发现提示均独立。其余本次扩展的生产模块低于 500 行。

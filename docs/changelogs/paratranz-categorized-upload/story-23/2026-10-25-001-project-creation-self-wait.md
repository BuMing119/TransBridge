# S23：新建工程等待自身切换结束的死锁

日期：2026-10-25。
关联：[当前计划](../../../../plans/paratranz-categorized-upload/plan.md)、[S22](../story-22/2026-10-25-001-saved-workbench-projection.md)。

## 故障与修复边界

用户最新日志 20261025_220523.log 显示，80 个插件在 22:06:28 已全部解析完成，没有 ERROR；
随后创建页面保持禁用。主线程心跳仍在，GUI watchdog 没有生成阻塞栈。
只读隔离内存验证进一步确认，开启 project-open 后后台一致性检查停在 Condition.wait_for，
只有人为结束事务才返回。

S22 最后增加的后台保存等待要求 open token 为空，创建流程却先在 before_start 开启 token，
再在 worker 中检查旧工作台；结束 token 的 GUI 回调必须等 worker 返回，形成自身等待。
这是 S22 的回归。S22 扩大回归早于最后一次等待逻辑改动，最后专项未包含真实创建入口，
因此未检出。既有历史记录保持原文，本轮明确修复并补齐覆盖。

本轮生产修改仅位于 src/transbridge/ui/project_view_binding.py，
未改创建流程的提交、安全检查或公共接口：

- begin：事务开启会改变检查的等待条件，设置 token 后通知已有等待者。
- install：安装标志在 Condition 锁内协调，完成或异常退出时清除标志并通知；
  后台检查不读取半安装的槽位/状态。
- diverged：新建/打开事务期间，比较当前已安装视图对应的权威快照，
  不等待自身事务结束，不把待安装新项目与旧槽位比较。
  普通后台保存继续等待已收到投影正常交付，GUI 线程不等待。
  比较结束再次核对事务、序列、安装标志、交付计数及已安装 snapshot 对象；
  任一变化都重新读取，同 token 的安装也不会漏检。
- 关闭仍唤醒检查并保留 PROJECT_VIEW_UNAVAILABLE；真正未提交译文、stage 和关联仍会拒绝。
  旧工程权威未保存工作继续由现有 provisioning 的 dirty / generation / signature 守卫保护。

## 新增回归

- tests/ui/test_project_creation_deadlock.py：6 项真实 StartCenter 按钮、
  MainWindow.start_foreground_task、ApiWorker、AppContext 和磁盘回归。
  首次创建 0/1/80 个小型真实插件、连续新建、旧 UI 草稿保护后重试、
  损坏插件修复后同按钮重试。80 个插件是隔离生成的最小插件，不是用户实际插件全集。
  旧实现首项以有界超时失败，状态为 committing / opening=True / worker=True；
  fixture 清理先结束隔离事务，避免失败测试本身卡住退出。修复后 6 项通过。
- tests/ui/test_projection_check_during_open.py：9 项真实线程事务边界回归。
  覆盖旧视图检查不等自身结束、已等待普通回执的检查在 begin 后被唤醒、
  真译文/stage/关联草稿保持、pending 新项目隔离、同 token 安装重读、
  半安装等待以及取消打开后正常交付旧项目更新。
  同 token 安装用例先发布 pending 再进入检查，安装过程中 token/sequence 不变，
  单独验证 snapshot identity 检查；唤醒用例明确等检查进入 Condition 后再开启事务。

没有修改已有测试或弱化断言来放行。
docs/dev/paratranz-categorized-upload.md 更新现行等待边界；
当前 plan、直接索引及本条新增记录同步，不修改 S22 等历史增量。

## 最终代码验证

生产修改冻结后执行扩大回归：3025 passed、1 skipped、3 deselected，
另有 2 failed / 21 errors，218.72 秒。
这 23 项均发生于隔离测试的原子写入目标路径，示例长度 262 字符，
超过传统 Win32 260 字符限制；原 basetemp 名 final-integrated 使测试路径过长。
仅把本轮 basetemp 改为单字母 q 后，对全部受影响文件/用例及事务检查复验，
33 项全部通过，18.39 秒，示例目标长度降到 247 字符。
没有改生产文件、持久化代码或这些旧测试以处理环境限制。
因此扩大范围合计 3048 个不同用例通过，1 跳过、3 项既有失败排除；
33 项中的已通过用例不重复相加。

随后对最终代码和完善后的事务测试执行新建、安装、普通下载/保存、恢复、打开以及 AI 边界专项：
**181 passed，48.23 秒**。该组含重叠，不与扩大范围相加。

实际命令（TEMP/TMP、uv 缓存、pytest 目录均在本轮独立目录，使用现有环境和 --no-sync）：

~~~powershell
uv --cache-dir .tmp-project-create-deadlock-20261025/uv-cache run --no-sync pytest tests/ui --ignore=tests/ui/tools tests/application/projects tests/persistence/v2 tests/contracts/paratranz tests/paratranz -q --disable-warnings --ignore=tests/contracts/paratranz/test_terms_api_contract.py --ignore=tests/paratranz/test_typed_client.py --deselect=tests/ui/characterization/test_main_window_contract.py::test_autosave_stop_is_idempotent '--deselect=tests/ui/test_assistant_project_navigation.py::test_dirty_save_or_discard_reuses_project_lifecycle_decision[True]' -k 'not test_reordered_source_projects_into_context_only_remote_plan' --basetemp .tmp-project-create-deadlock-20261025/final-integrated

uv --cache-dir .tmp-project-create-deadlock-20261025/uv-cache run --no-sync pytest tests/ui/test_plugin_drop_workflow.py tests/ui/test_project_creation_reuse.py tests/ui/test_v2_variant_hydration.py tests/persistence/v2/test_project_archive.py::test_plugin_strings_and_sqlite_wal_assets_survive_portable_roundtrip tests/persistence/v2/test_project_archive.py::test_same_name_conflict_can_be_renamed_without_changing_archive_identity tests/ui/test_projection_check_during_open.py -q --disable-warnings --basetemp .tmp-project-create-deadlock-20261025/q

uv --cache-dir .tmp-project-create-deadlock-20261025/uv-cache run --no-sync pytest tests/ui/test_project_creation_deadlock.py tests/ui/test_project_creation_transition.py tests/ui/test_projection_check_during_open.py tests/ui/test_project_view_installation.py tests/ui/operations/test_download_workbench_projection.py tests/ui/test_save_completion_state.py tests/ui/test_save_recovery.py tests/ui/test_background_gui_operations.py tests/ui/test_project_open_operation.py tests/ui/test_prepared_entry_projection.py tests/ui/test_content_projection_merge.py tests/ui/test_reference_projection.py tests/ui/test_authoritative_content_projection.py tests/ui/tools/test_ai_frozen_save_boundary.py tests/ui/tools/test_ai_launch_refresh_guard.py tests/ui/tools/test_ai_name_publication_roundtrip.py tests/ui/tools/test_generated_term_roundtrip.py tests/ui/tools/test_ai_result_background.py tests/ui/tools/test_ai_result_recovery.py tests/ui/tools/test_project_reference_large_task.py -q --disable-warnings --basetemp .tmp-project-create-deadlock-20261025/s

uv --cache-dir .tmp-project-create-deadlock-20261025/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-project-create-deadlock-20261025/uv-cache run --no-sync ruff format --check src tests
~~~

Ruff 通过，格式检查 2199 files already formatted。
保留既有 SWIG / legacy 弃用警告。
沿用 S22 已确认的 3 项基线排除：旧 autosave host fixture、
旧导航 host 缺 project_commands，以及旧上传排序断言。
两个本机 HTTP server 测试文件继续因前轮沙箱套接字边界排除；
完整依据见 S22，未将这些排除伪报通过。

## 收尾与限制

保留下载同步、自动保存及真实未提交编辑保护；无需迁移本地格式或版本。
未使用用户实际插件全集重建工程，未执行全库 pytest、真实云端操作或安装包构建。
代码已经修复，运行中旧进程仍需重新启动以加载新逻辑；本轮未结束或重启用户进程。
未修改用户工程数据、依赖、lockfile 或打包文件，未提交 Git。

所有测试和 Agent 完成后，校验绝对路径、OWNER 和无重解析点，
已删除本轮 .tmp-project-create-deadlock-20261025 下的基线、日志、缓存和测试文件。
保留既有临时目录及用户数据。本轮修复范围无遗留阻塞，未验范围如上。

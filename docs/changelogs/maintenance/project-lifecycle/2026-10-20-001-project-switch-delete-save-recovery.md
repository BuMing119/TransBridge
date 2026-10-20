# 修复新建工程残留、删除文件锁与保存失败恢复

- 日期：2026-10-20
- 归属：maintenance / project-lifecycle
- 关联：[guided-ui-workflows S02](../../../../plans/guided-ui-workflows/stories/story-02-authoritative-project-provisioning.md)、[S04](../../../../plans/guided-ui-workflows/stories/story-04-start-center-guided-project.md)
- 授权：用户通过 bm-pilot 要求修复已经复现的单实例工程生命周期故障。

## 问题与结果

同一窗口打开旧工程后再新建工程，原回调只追加插件，正式工程已经切换而工作台残留旧来源，保存因此报 `PROJECTION_AUTHORITY_DIVERGED`。自动保存失败后仍反复重试，退出又要求保存成功，产生重复提示和无法正常退出。另有术语 SQLite 连接未在删除前释放，导致 Windows `WinError 32`。

新建流程现在在后台准备完整工作台，复用工程视图安装事务，一次替换工程信息与全部来源；空工程也清除旧来源。旧工程未保存时保留内容并拒绝切换。删除只释放目标工程资源，失败后恢复已有服务引用。保存失败暂停自动重试，人工保存成功或切换工程/版本后恢复。退出默认返回工作台；用户明确选择后，可备份全部来源并在校验成功、内容未变化时退出。

## 本次文件增量

仅记录本任务实际编辑部分。共享工作区已有的多插件创建、ParaTranz 同步、助手会话及其他 UI 改动不归入本记录。

- 新增 `src/transbridge/ui/coordinators/project_creation_operation.py`：提交前冻结工程视图；在 worker 中核验旧内容、提交并准备全部来源，检查词条覆盖后在 Qt 线程整体安装；失败保留正式记录并提示重开。
- 修改 `src/transbridge/ui/shell/start_center_controller.py`：提交交给上述操作，创建成功回调只导航和提示，移除向旧工作台追加来源的路径。原有多选插件改动属于前一工作线。
- 修改 `src/transbridge/persistence/project_provisioning.py`：迁移来源也保留已经解析的快照，避免重复解析。
- 修改 `src/transbridge/application/projects/provisioning_sources.py`：交付全部非折叠基线来源的 hydration，覆盖独立已有译文；已配对合并的副本不重复展示。
- 修改 `src/transbridge/application/projects/provisioning_models.py`：更正来源快照的职责说明，不改持久化格式。
- 修改 `src/transbridge/application/projects/management.py`：通过组合层注入的资源作用域执行删除。
- 修改 `src/transbridge/bootstrap/composition.py`：将术语资源作用域接入工程删除命令。
- 修改 `src/transbridge/bootstrap/terminology_storage.py`：管理删除期间的访问，阻止旧任务重建已删数据库；正式归档重新导入同一 ID 后允许取得新仓库。
- 修改 `src/transbridge/bootstrap/terminology.py`：仅在删除成功后清除目标工程的方案/同步服务缓存。
- 新增 `src/transbridge/persistence/terminology/connection_lifecycle.py`：等待目标仓库操作锁，关闭 cursor 和 SQLite；删除回滚后恢复稳定连接入口。回滚缺库时保留错误，不创建空库。
- 修改 `src/transbridge/persistence/terminology/repository.py`、`profile_reads.py`：使用稳定连接和连接代次，使回滚后的读缓存失效。原 repository 超过 500 行，本次仅保留局部接线，新增连接生命周期职责独立成模块。
- 修改 `src/transbridge/ui/main_window.py`：前台操作增加受忙碌检查保护的 `before_start`；报告保存结果以恢复自动保存；补齐前台/保存 worker 构造和启动失败的清理。恢复导出职责独立于主窗口。
- 修改 `src/transbridge/ui/shell/window_lifecycle.py`：失败暂停自动保存；接入明确的备份退出，保留任务、草稿和输入保护；恢复 worker 失败仍留窗。
- 新增 `src/transbridge/ui/shell/save_recovery.py`：逐来源导出内部 JSON、标签和正式工程快照，临时写入、fsync、ZIP/SHA 校验后原子替换。退出凭据绑定工程/版本、编辑代次、collection 身份及 revision；来源描述固定原 collection 引用。
- 新增 `tests/ui/test_project_creation_transition.py`：9 项真实后台创建回归，覆盖空/单源/多源、创建失败、未保存译文、视图准备失败及独立迁移来源。
- 修改 `tests/ui/test_start_center_guided_project.py`、`test_start_center_multi_plugin.py`：适配整体视图安装，保留多来源和字符串 ID 验证。
- 修改 `tests/persistence/v2/test_project_provisioning.py`：迁移来源须保留与基线身份一致的快照。
- 新增 `tests/integration/bootstrap/test_project_terminology_deletion.py`：6 项回归，覆盖真实文件锁、回滚、跨线程事务、待提交事务拒删、缺库和同 ID 归档重导入。
- 新增 `tests/ui/test_save_recovery.py`：20 项回归，覆盖完整内容读回、默认留窗、原子写失败、新编辑、等长修改、slot 替换、晚到 worker、自动保存恢复及启动异常。
- 修改 `tests/ui/test_ai_task_shutdown.py`、`test_runtime_task_shutdown.py`、`test_worker_shutdown_registry.py`、`test_project_open_guards.py`：旧关闭测试显式选择返回工作台，保留原任务与退出屏障断言。
- 修改 `plans/guided-ui-workflows/plan.md`、`stories/story-02-authoritative-project-provisioning.md`、`stories/story-04-start-center-guided-project.md`：将逐来源加载描述更正为完整基线来源和整体替换，补充验证；不改变 Story 草稿状态或历史统计。
- 新增本记录，修改 `docs/changelogs/INDEX.md`，保留其他工作线条目。

## 最终验证

最终联合运行退出码 0，**441 passed，7 warnings，19.19 秒**。3 条为既有 SWIG 弃用警告，4 条来自特意覆盖旧 `add(overwrite=True)` 路径的回归。

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$repairTests = @(
  'tests/ui/test_project_creation_transition.py',
  'tests/ui/test_start_center_guided_project.py',
  'tests/ui/test_start_center_multi_plugin.py',
  'tests/ui/test_project_view_installation.py',
  'tests/ui/test_project_source_hydration.py',
  'tests/ui/test_project_open_operation.py',
  'tests/ui/test_project_open_guards.py',
  'tests/application/projects/test_provisioning.py',
  'tests/persistence/v2/test_project_provisioning.py',
  'tests/persistence/v2/test_project_provisioning_multiple_sources.py',
  'tests/ui/test_save_recovery.py',
  'tests/ui/test_save_completion_state.py',
  'tests/ui/test_worker_shutdown_registry.py',
  'tests/ui/test_ai_task_shutdown.py',
  'tests/ui/test_runtime_task_shutdown.py',
  'tests/ui/test_background_gui_operations.py',
  'tests/integration/bootstrap/test_project_terminology_deletion.py',
  'tests/persistence/terminology',
  'tests/integration/bootstrap/test_terminology_task_wiring.py',
  'tests/integration/bootstrap/test_terminology_editing.py',
  'tests/integration/bootstrap/test_terminology_browsing.py',
  'tests/integration/bootstrap/test_terminology_save_snapshots.py',
  'tests/integration/bootstrap/test_terminology_build_review.py',
  'tests/integration/bootstrap/test_terminology_conflict_review.py',
  'tests/integration/bootstrap/test_terminology_publication_review.py',
  'tests/integration/bootstrap/test_terminology_presenter_build.py',
  'tests/persistence/v2/test_project_management.py',
  'tests/persistence/v2/test_project_archive.py'
)
uv run --no-sync --no-cache pytest @repairTests -q -p no:cacheprovider --basetemp .tmp-pilot-project-repair-01a1591e/all
uv run --no-sync --no-cache ruff check src tests
uv run --no-sync --no-cache ruff format --check src tests
git -c core.safecrlf=false diff --check
```

Ruff lint 通过；格式检查通过，1941 个文件无需格式化；差异空白检查通过。分组测试不与最终 441 项重复累计。普通沙箱下 pytest 临时目录遇到 Windows ACL 错误，最终经自动审批后在专属目录运行；全部使用合成数据。

独立审查发现的迁移来源漏载、GUI 全量扫描、worker 启动失败收尾和备份收据漏检原地修改均已修复并复验。

## 兼容性与未运行项

- 不改工程/版本 schema、依赖、版本或安装包，无数据迁移步骤。
- 备份中的来源 JSON 可通过现有译文导入恢复完整词条；标签和正式工程状态保存在 manifest，普通译文导入不会自动恢复这些工程设置。原始插件需保留，备份内附恢复说明。
- 未修改真实用户工程、AppData 或云端译文；未运行远端 ParaTranz、全仓 pytest、发行包构建或安装包手工验证。
- 本次及子任务专属临时目录已核验归属后清理，其他任务临时目录保持原样。
- 未执行 Git commit、push 或发布。

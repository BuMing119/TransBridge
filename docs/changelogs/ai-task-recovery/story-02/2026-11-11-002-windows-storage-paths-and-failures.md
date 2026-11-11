# S2 Windows 长路径存储与后处理保存失败报告

- 日期：2026-11-11
- Epic / Story：`ai-task-recovery / S2 执行恢复`
- 关联：[计划](../../../../plans/ai-task-recovery/plan.md)、[此前的短临时文件修复](2026-11-11-001-short-checkpoint-temporaries.md)
- 范围：本会话的校对数据库修复，以及随后审查确认的候选日志、阶段断点、错误报告和任务/项目数据库四类问题。记录当前真实 Git 差异，不包含用户既有的 `.agents/skills/bm-release/SKILL.md` 和 `.transbridge/`。

## 原因与结果

Windows 上仅缩短临时文件名不足以保证其他正式文件能够保存。审查使用隔离数据复现：常规目录结构下，后处理提交日志可达 285 字符并触发 `FileNotFoundError`；较深目录中的阶段 JSON 保存失败，已有文件还会被普通 `exists()` 判断为不存在。任务/项目数据库同样可能无法打开，直接将扩展路径传给 `Path.as_uri()` 又会产生 `invalid uri authority: %3F`。

现在在实际文件访问入口使用已有的 Windows 扩展路径策略，SQLite URI 编码完整本机文件名。正式文件名、任务身份、数据库 schema、日志校验链、只读/读写模式、事务与原子替换语义保持不变，既有断点不需要迁移。

后处理阶段异常与随后发生的断点保存异常同时保留在终态报告中。保存失败会停止后续阶段并阻止正式提交，仍返回已经生成的候选供报告展示；取消任务保持取消状态。再次恢复时复用已保存的完整阶段，只重做尚未保存的阶段。

## 逐文件变更

- 新增 `src/transbridge/filesystem_paths.py`：`native_path` 抽取既有长路径规范化策略；`sqlite_uri` 统一文件名编码及 `ro/rw/rwc` 模式，避免扩展路径前缀被解析为 URI authority。没有新增哈希。
- 修改 `src/transbridge/application/io/publish/filesystem.py`：`OsPublishFilesystem.canonicalize` 委托共享路径函数，保留原行为。
- 修改 `src/transbridge/application/translation/candidate_checkpoint.py`：`FilesystemTranslationCheckpointPort._path` 规范化最终提交头路径。
- 修改 `src/transbridge/application/translation/candidate_checkpoint_journal.py`：`journal_path` 规范化实际日志路径，兼容提交头较短而日志较长的情况。
- 修改 `src/transbridge/application/translation/candidate_checkpoint_store.py`：`_replace` 在生成完整日志名称后规范化路径，覆盖写入和失败清理。
- 修改 `src/transbridge/application/translation/postprocess_checkpoint.py`：阶段存储根目录及最终 JSON 路径使用同一规范化策略，覆盖目录创建、存在检查、读写和替换。
- 修改 `src/transbridge/application/translation/proofread_checkpoint.py`：数据库及损坏记录备份复用共享路径/URI，读写均兼容原有文件；移除本会话早期的局部 URI 实现。
- 修改 `src/transbridge/application/translation/postprocess.py`：阶段异常记录安全的具体原因和阶段；各保存分支统一把异常转成 `POSTPROCESS_CHECKPOINT_SAVE_FAILED` 终态报告，保留原始诊断、候选与异常日志。
- 修改 `src/transbridge/ai_translator/translation_entry_outcomes.py`：优先呈现已有的具体诊断，缺少完成阶段时再补“后处理未完成”，保持词条作用域隔离。
- 修改 `src/transbridge/persistence/task_store/connection.py`：在存在检查、目录创建和连接前规范化数据库路径，并使用统一 URI。
- 修改 `src/transbridge/persistence/project_store/connection.py`：项目数据库和写租约入口规范化路径，并使用统一 URI，保留 schema 校验与事务配置。
- 修改 `src/transbridge/persistence/project_store/repository.py`：`publish_project` 在存在检查、暂存和原子发布前规范化目标路径。
- 修改 `src/transbridge/persistence/v2/session_write_lease.py`：先经过原路径授权边界，再规范化实际锁文件访问路径，避免数据库可打开而写租约失败。
- 新增 `tests/path_fixtures.py`：构造带中文、空格及 URI 保留字符的隔离深目录。
- 新增 `tests/contracts/translation/test_long_checkpoint_storage.py`：覆盖不同深度的日志写入/重开、既有快照读取与更新、替换失败清理，以及完整后处理提交记录落盘。
- 新增 `tests/contracts/translation/test_postprocess_storage_failures.py`：覆盖成功、阶段异常、诊断失败、可重试缺口、两种取消、空流水线、提交阻止及恢复后仅重跑未保存阶段。
- 新增 `tests/persistence/test_long_database_paths.py`：真实 SQLite 长路径创建、重开、只读保护、回滚、项目发布与写租约，覆盖普通路径和已带扩展前缀的路径。
- 新增 `tests/application/translation/test_proofread_checkpoint_paths.py`：校对长路径保存、恢复、不重复请求、旧文件名兼容、损坏备份及只读保护；最终使用共享 URI 函数。
- 修改 `tests/contracts/translation/test_postprocess_candidate_report.py`：核验阶段、原始原因、异常链和报告敏感字段脱敏。
- 修改 `tests/ai_translator/test_translation_entry_outcomes.py`：未完成阶段时仍保留有作用域的具体错误。
- 修改 `tests/ai_translator/test_translator_term_conflicts.py`：阶段异常进入最终词条失败原因，保持失败译文不发布的约束。
- 修改 `docs/changelogs/INDEX.md` 和 `plans/ai-task-recovery/plan.md`：仅添加本增量入口。

责任审查：`postprocess.py` 当前 629 物理行。本次仅在既有保存调用点增加失败投影，未增加新的业务职责；路径处理独立放入无 UI/应用层依赖的模块。没有扩展为通用工作流重构。

## 验证

使用仓库现有 Python 3.12.12 / uv 环境及任务专用临时目录；没有执行付费模型请求或修改用户译文。

修复前新增的初始 17 项专项用例全部失败，分别复现长路径文件错误、SQLite URI 错误和保存异常逃逸：

```powershell
uv run --no-sync pytest tests/contracts/translation/test_long_checkpoint_storage.py tests/contracts/translation/test_postprocess_storage_failures.py tests/persistence/test_long_database_paths.py -q -p no:cacheprovider --basetemp .tmp-storage-repair-20261111/before
```

首次修复后，以上用例加已有校对长路径用例共 **24 项通过**。随后补充取消前保存、恢复只重跑未保存阶段、完整提交链路三个用例，纳入最终扩大回归：

```powershell
uv run --no-sync pytest tests/application/translation tests/contracts/translation tests/persistence tests/ai_translator/test_translation_entry_outcomes.py tests/ai_translator/test_translator_term_conflicts.py tests/ui/tools/test_task_recovery_discovery.py tests/integration/publish/test_atomic_publish.py -q -p no:cacheprovider --basetemp .tmp-storage-repair-20261111/regression
```

结果：**1239 passed, 1 skipped**，44.86 秒，退出码 0。跳过项是当前 Windows 环境无法创建符号链接的发布测试；另有三条既有 SWIG 弃用警告。

```powershell
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
git diff --check
```

均通过；2537 个源码/测试文件符合格式要求。格式化过程中 Windows 沙箱 ACL 拒绝写入两个新增测试文件，放行该限定操作后完成，未扩大文件改动范围。

## 兼容与遗留

- 本次四项审查问题已修复；原有断点格式、名称、身份及文件位置不变。
- 实测覆盖本机 Windows 文件系统和现有离线回归。未实测 UNC 网络共享、真实模型服务或发行安装包；未打包、提交或发布。
- 本记录不宣称已统一改造仓库所有其他持久化模块。

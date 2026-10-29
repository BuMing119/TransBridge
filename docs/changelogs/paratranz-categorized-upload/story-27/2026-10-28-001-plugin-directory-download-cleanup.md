# S27：按插件目录下载并清理本轮临时数据

日期：2026-10-28（Asia/Shanghai）。Epic：paratranz-categorized-upload；Story：S27。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md)、[需求](../../../requirements.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[ADR-049](../../../adr/049-task-result-lifecycle.md)、[开发说明](../../../dev/paratranz-categorized-upload.md)。

## 原因与用户确认

用户日志显示 Dragonborn 下载请求均成功，但云端 231 个分类文件只读取了 229 个。
用户提供的插件解析出 10966 条；本机原来源有 10990 条。对比实际记录确认：用户插件包含相关对话，
没有 0002D512、000CA210 两条任务的 QUST 记录；本机来源包含它们，因此分类路径分别采用任务 ID 和任务标题。
截图三句的 key、原文和任务归属一致，但命名不同的整个文件在请求前被排除。

用户明确确认保留任务标题命名；下载仅判断插件层级，把该层级内的分类文件下载后交给本地匹配。
用户另要求不建立目录索引、不增加校验，下载留下的中间内容仅供本轮使用，完成或取消后清理，后续不复用。
先前尝试的目录索引及新增云端路径校验已撤销，最终差异不包含这些实现。

## 逐文件变化

- 修改 `src/transbridge/paratranz/download_paths.py`：`matches_current_path` 按插件目录边界接收全部 JSON，
  继续支持普通根文件及确定性数字分片。新旧布局、大小写和旧名消歧沿用原合同，不增加索引或网络查询。
- 修改 `src/transbridge/ui/operations/batch_download_session.py`：`_scope` 仅生成插件根范围，
  删除下载前分类重算；删除持久下载成果和覆盖前记录的读写。`discard` 释放本轮来源、快照、计划、选择、
  匹配及权限状态；比较/合入失败和无待确认项的成功合入后清理。本轮尚未完成的分歧只留内存。
- 修改 `src/transbridge/ui/operations/batch_download_results.py`：不再写合入尝试或成功回执，
  直接使用原有本地合入及正式版本原子保存。已按偏好或显式选择处理的条目不继续冒充待确认项。
- 修改 `src/transbridge/ui/operations/batch_download_dialog.py`：移除成果重开、另存及恢复按钮，
  通过 discard 回调在成功、失败、取消、关闭及改变选择结束本轮时释放数据；后台仍使用输入时不提前释放。
  本轮确认分歧后继续合入；已决定保留当前的零更新轮也清理，不遗留复核候选。
- 修改 `src/transbridge/ui/operations/download_run.py`：更新本轮输入及提交职责说明。
- 删除仅服务旧下载闭环的 `application/sync/download_results.py`、`paratranz/path_snapshot.py`、
  `ui/operations/batch_download_access.py`、`ui/operations/batch_download_recovery.py`。
- 新增 `tests/paratranz/test_plugin_directory_download.py`：没有本地任务标题记录时读取命名任务文件，
  匹配三句既有词条，不跨读 Skyrim、不把云端额外条目加入本地。
- 修改 `tests/paratranz/test_download_paths.py`、`test_batch_download_snapshot.py`、`test_file_transport_keys.py`：
  验证插件目录内任意分类/历史拆分名均可读取，插件边界、新旧布局和原有 key 解码继续有效。
- 重写 `tests/ui/operations/test_batch_download_results.py`：成功后清空输入，下一轮读取新译文，
  取消/失败丢弃前缀，不创建成果目录；保留本轮分歧与审阅后再编辑的回归。
- 修改 `tests/ui/operations/test_batch_download_dialog.py`：删除旧恢复入口测试，新增成功、错误、取消、
  关闭待确认窗口及选择保留当前后的清理验证。
- 修改 `tests/ui/operations/test_unified_download_service.py`：真实 HTTP 组合覆盖按任务标题命名的子目录，
  不调用本地分类器，正确合入、保存并清空本轮状态；失败后的新轮重新请求，不保存恢复点。
- 修改 `tests/ui/operations/test_batch_download_session.py`、`test_batch_download_autosave.py`、
  `test_download_automatic_recovery.py`、`test_download_autosave_boundaries.py`、
  `test_download_workbench_projection.py`：更新已取消的持久恢复断言，保留正式保存、并发编辑、取消仲裁、
  保存失败不发布、实际工作台投影和保存重开的一致性验证。
- 删除仅针对已移除存储的 `tests/contracts/paratranz/test_download_result_store.py`。
  修改 `tests/persistence/v2/test_legacy_project_task_assets.py`，用历史 wire 数据验证旧下载附件原字节保留，
  不依赖已删除的运行时 writer。
- 同步 `docs/requirements.md`、ADR-047、ADR-049、`docs/dev/paratranz.md`、
  `docs/dev/paratranz-categorized-upload.md`、当前计划及直接相关索引，替换精确分类白名单和下载持久恢复的旧描述。

## 实际验证

### 扩大回归

```powershell
uv run pytest tests/paratranz tests/contracts/paratranz tests/ui/operations tests/persistence/v2/test_legacy_project_task_assets.py --basetemp=D:/MyCode/TransBridge/.q27-8e3c --deselect=tests/paratranz/test_entry_upload_order.py::test_assistant_selected_upload_retains_full_collection_order_and_local_context --deselect=tests/paratranz/test_entry_upload_order.py::test_assistant_upload_preserves_sparse_plugin_order_and_free_text --deselect=tests/paratranz/test_entry_upsert.py::test_assistant_upload_reuses_only_the_target_projects_known_identity -q --tb=short
uv run ruff check src tests
uv run ruff format --check src tests
git diff --check
```

结果：739 passed、1 skipped、4 deselected，33.24 秒；Ruff 检查通过，2248 个 Python 文件格式通过，
diff 空白检查通过。最终测试使用新建的短路径目录，避免 Windows 下 SQLite 测试路径过长；该目录收尾删除。

被排除的 4 个助手上传用例已独立做基线复现：在单独 Python 进程中将本次涉及的下载模块替换为
`git show HEAD:<path>` 的内存模块，再执行上述两个助手上传文件中的同名 4 项。
结果仍为 4 failed：两个缺少对应工程版本，两个 SimpleNamespace 没有 config；不属于本次下载改动。
未为通过测试改动助手上传业务。

初轮扩大测试中，旧成果落盘/旧冲突文件排除断言按新契约修改；默认长临时路径的一项 SQLite 失败，
在短路径完整回归中通过。未把这类环境失败或已取消合同的断言计作当前交付失败。

### 用户真实插件与云端只读验证

通过 `uv run python -` 执行一次隔离验证：复制用户提供的 Dragonborn.esm 与本机配套英文 Strings，
建立全空译文的当前工程，使用实际 BatchDownloadSession、文件读取器、本地匹配、TaskRuntime 和正式保存服务。
用户没有提供 BSA，因此使用本机英文表；三类表条数与用户日志相同，截图三句的原文也一致。

- 初始 10966 条均为空；只读访问实际 ParaTranz 项目 17696。
- 使用正常请求预算完整读取 Dragonborn 目录的 231 个文件，包含原先漏读的 3527821、3527828。
- 自动合入并保存 10669 条，297 条未更新，0 条待确认；未把未更新条目全部推断为下载失败。
- 截图三句全部取得非空译文；磁盘读回与内存一致，dirty 为 false。
- 关闭并重新打开隔离工程，所有译文逐项一致。
- 未生成 download-results 或 merge 记录；完成后本轮读取、计划、来源和复核状态已清空。
- 请求限定 GET，未写云端；整轮约 251.03 秒，不作为新旧性能对比。
- 验证工程及输入副本在 finally 中按所有权和绝对路径确认后删除，用户原插件、正式工程和云端内容未改变。

前几次验证脚本未真正读取云端：把 C 盘插件与 D 盘外部 Strings 直接拼接，触发来源成果恢复的跨盘路径限制。
将输入及配套表复制到同一隔离目录后通过；未因此修改生产来源恢复规则或增加校验。

## 兼容与边界

保留任务标题命名、上传补传、现有本地词条匹配及正式版本保存。没有 schema、依赖或云端文件迁移。
当前 GUI 不再重开旧下载成果；旧用户文件和历史工程包附件没有自动删除或改写。
原有未跟踪的 `.transbridge/` 不属于本任务，未修改。未提交 Git、未构建或打包新版、未运行全库 pytest。
没有未完成的本次实现项；上述 4 项基线失败和安装包构建不在本次交付范围。

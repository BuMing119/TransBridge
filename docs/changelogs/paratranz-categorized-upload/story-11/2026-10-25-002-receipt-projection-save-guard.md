# 上传后关联投影与保存检查修复

- 日期：2026-10-25
- Epic / Story：`paratranz-categorized-upload` / S11 后续修复
- 计划：[plan](../../../../plans/paratranz-categorized-upload/plan.md)
- 使用说明：[分类上传](../../../dev/paratranz-categorized-upload.md)
- 前序：[直接上传与固定路径](2026-10-25-001-direct-upload-and-fixed-paths.md)

## 证据与原因

用户上传完成后，工作台显示 `PROJECTION_AUTHORITY_DIVERGED`，退出时出现“项目尚未保存”。本地 `data/log/20261025_025050.log` 在 03:02:34 的 `execution_summary` 为 `completed`、`stale=false`、`succeeded=20162`，所选文件 280 个。27 次 `checkpoint_saved` 合计保存 20,162 条关联和 280 个文件绑定；没有把该次实际上传判为失败的证据。

原因在本地投影边界：分类上传的工程事务保存了新 `external_refs` 和词条修订，但 `ProjectViewBinding` 只更新工程元数据，没有同步已打开来源集合。工作台继续持有旧 ID；保存检查比较译文、状态和外部引用，因引用不同而拒绝保存。此前回归验证了工程落盘和 UI 修订更新，没有验证全部工作台集合及表格持有的引用一致，遗漏了该缺陷。

修复后，同工程/版本的已确认关联通过 Qt 发布队列同步到所有已打开来源。只有当前引用仍等于上一份工程投影时才推进；真实本地引用差异保留。译文、状态不回填，修订只推进不回退，真正未提交的编辑仍会触发保护。各来源先准备完成，再一次安装；失败事务没有发布，因此不会把尚未保存的回执塞进工作台。

当前来源未变时不刷新表格；纯 `external_refs` / `revision` 更新只替换数据对象，保留筛选、排序、选择和滚动，外部 ID 索引同步更新。启用译名方案时沿用原刷新生命周期，不改变该功能的预览协议。

旧进程不会自动加载代码修复。发生该提示时可使用“备份当前内容并退出”，保留屏幕内容后重启并打开原工程。日志所证实的上传结果不需要为此重新上传；不删除云端文件、不清除工程关联、不关闭真实未保存编辑检查。本轮没有操作用户工程文件或云端项目。

## 逐文件增量

工作区有大量既有修改；以下归属通过 `.tmp-upload-projection-fix/baseline` 中的修改前副本核对，不包含前序未提交工作。未提交或推送 Git。

- **修改 `src/transbridge/ui/entry_projection.py`**：增加 `sync_projected_references`，按完整 EntryKey 对照前后关联；仅替换与旧投影一致的引用并推进修订，复用未变词条，各受影响集合重建一次索引。正常顺序的快照不为每条未变词条创建身份对象，顺序变化时才建立查找索引。
- **修改 `src/transbridge/ui/project_view_binding.py`**：同工程、版本、stream 的正常发布接入引用同步；安装新工程和跨版本发布不套用旧关联。所有来源就绪后，仅当前集合改变才发送一次 `collection_changed`，保留原排队顺序与过时投递保护。
- **修改 `src/transbridge/ui/workbench/entry_refresh.py`**：纯引用/修订批次不受原 64 条可见编辑阈值限制，使用 `replace_session` 更新表格对象，不重跑过滤、排序或逐行重绘；混合译文/状态变化继续执行原检查。
- **新增 `tests/ui/test_reference_projection.py`**：9 项覆盖多处相同词条、未选来源复用、非活动来源无刷新、乱序快照、真实本地引用差异、正常 UI 修订滞后、禁止修订回退、工程/版本切换及排队投递。
- **新增 `tests/ui/operations/test_categorized_upload_projection.py`**：4 项真实工程服务、后台上传和 Qt 工作台回归，覆盖全部成功、取消后部分成功、未提交本地编辑、关联保存失败；检查普通保存入口、所有来源索引和真实表格对象一致。
- **修改 `tests/ui/test_entry_refresh.py`**：新增 4 项，600 条纯修订/引用批次无 modelReset、无 dataChanged、不增加渲染 generation，保持持久 QModelIndex、选择、过滤、排序、滚动及 summary；混合译文/状态变更仍重新检查筛选成员。
- **修改 `scripts/benchmark_categorized_upload_ui.py`**：除 UI 修订外，断言 `authoritative_projection_diverged()` 为假，并在报告写入 `workbench_save_guard_consistent`。
- **修改 `docs/dev/paratranz-categorized-upload.md`**：补齐工程落盘到工作台的同步规则、旧进程恢复指引和本次真实日志证据；分清受控测试与用户上传事实。
- **修改 `plans/paratranz-categorized-upload/plan.md`、`docs/INDEX.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`**：更新 S11 后续修复和直接相关索引；新增本记录，前序增量保持不变。

三个生产模块分别为 112、323、128 行，主类均未超过 30 方法；没有增加持久化、网络或 UI 主线程磁盘职责。工程 schema、固定上传路径及批次保存协议不变。

## 验证

使用仓库 uv 环境和锁文件，设置 `UV_CACHE_DIR=.tmp-upload-projection-fix/uv-cache`、`TMP/TEMP=.tmp-upload-projection-fix`（执行时均为仓库内绝对路径）、`QT_QPA_PLATFORM=offscreen`、`PYTHONIOENCODING=utf-8`。

先补真实上传/工作台回归，修改前 **2 failed、1 passed**：全部成功与部分取消都被错误判为 divergence，未提交编辑保护正常。修复后通过；随后加入保存失败、真实表格和边界回归。600 条表格新测试先复现 2 项失败后通过。扩大测试发现只读快照数组不能直接下标访问、非活动来源不应刷新当前表格，均修正后复验。

最终相关集合去重 **440 passed、27 warnings，22.74 秒，退出码 0**：

```powershell
$projectionTests = @(rg -l 'ProjectViewBinding|authoritative_projection_diverged|project_view_failed|prepare_project_view' tests -g '*.py')
$categoryTests = @(Get-ChildItem tests/ui/operations -File -Filter 'test_categorized_*.py' | ForEach-Object { $_.FullName })
$focusedTests = @($projectionTests) + @($categoryTests) + @('tests/ui/test_save_completion_state.py','tests/ui/test_save_recovery.py','tests/ui/test_dialogue_projection_sync.py','tests/ui/test_entry_refresh.py','tests/ui/test_step2_incremental_rendering.py','tests/application/projects/test_categorized_upload.py','tests/ui/test_inline_edit_recovery.py','tests/ui/test_inline_edit_shutdown.py','tests/ui/test_translation_table_model.py','tests/ui/test_translation_table_reading.py','tests/ui/test_translation_table_sorting.py')
$focusedTests = @($focusedTests | ForEach-Object { (Resolve-Path -LiteralPath $_).Path } | Sort-Object -Unique)
uv run pytest @focusedTests -q --tb=short --show-capture=no
```

警告为既有 SWIG 类型及旧直接赋值接口的弃用提示。独立 UI 切片的 67 项、前一轮 390 项和聚焦 13 项与最终集合重叠，不相加。前序 S11 的旧下载恢复基线失败不在本轮集合内，未声称修复该无关问题。

全库静态/格式检查通过（2157 文件）：

```powershell
uv run ruff check src tests scripts/benchmark_categorized_upload_ui.py
uv run ruff format --check src tests scripts/benchmark_categorized_upload_ui.py
```

真实 Qt/工程原子保存冒烟通过：

```powershell
uv run python scripts/benchmark_categorized_upload_ui.py --entries 1000 --receipt-count 100 --scratch .tmp-upload-projection-fix/ui-smoke --output .tmp-upload-projection-fix/ui-smoke/report.json
```

1,000 行工作台、100 条关联：1 次原子保存、1 次投影发布；落盘关联、工作台修订和保存检查一致。worker 提交 587.213 ms；10 ms Qt 心跳最大间隔 33.909 ms，无超过 100 ms 的间隔。这是单轮冒烟，未重测两万条性能，不作为所有工程的延迟上限。

本轮 diff、职责大小和新增文档链接已核对；结束时清理专用基线、缓存及 QA 输出。未运行全库 pytest 或构建，未发起真实云端请求，未重启或接管用户正在运行的进程。

## 遗留边界

本次复现的上传后保存错误已修复。代码需应用重启后生效；已有真实未提交内容应通过原备份退出流程保护。译名方案启用时仍采用原刷新路径；服务端读取/写入之间的并发窗口、前序记录的旧下载恢复失败均不在本次修复范围。

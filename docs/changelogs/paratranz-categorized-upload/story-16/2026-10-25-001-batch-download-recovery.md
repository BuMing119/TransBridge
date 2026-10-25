# S16：批量下载与原窗口恢复

日期：2026-10-25。Epic / Story：paratranz-categorized-upload / S16。
关联：[计划](../../../../plans/paratranz-categorized-upload/plan.md)、[FR3.3 / FR3.4.4](../../../requirements.md)、[下载说明](../../../dev/paratranz-categorized-upload.md)。

## 问题与交付

旧批量直接合并删除后，共享下载计划仅支持单来源，但菜单仍能触发批量入口，导致固定显示“批量下载暂不可用”。用户要求参考当前上传流程恢复批量下载，并明确要求不要把程序内部误校验、失效状态与可恢复错误直接交给用户处理，授权 bm-pilot 与多子 agent 实现。

生产批量入口现列出当前工程插件，后台共享文件列表读取，按来源分别对照/复核；确定项形成一个候选集合，创建真实历史还原点并一次提交当前版本已有译文和状态。无关本地编辑自动保留，同条新编辑进入逐条复核，其他确定项可先更新。失败保留选择，可原地重试。单插件入口继续沿用原计划，上传执行语义不变。

工作区启动时已有大量 AI、工程、上传、下载和文档未提交变更。本轮用任务专属副本比对启动内容，新增文件按独占分工核验；以下只归属本次新增符号和修改，不把 HEAD 的全部差异或原有未跟踪模块算入本轮。没有 commit、push、依赖/schema 变更或真实云端写入。

## 逐文件增量

- **新增 `application/sync/batch_download.py`**：BatchDownloadSource/Plan/Candidates、完整本地快照转换、逐来源规划、失效复核选择局部清理与本地并发编辑重基；保持原文、上下文和词条顺序，不附加远端关联。
- **修改 `application/sync/planner.py`、`download_planner.py`**：增加可选 download_review_keys/review_keys，仅对实际存在新编辑的目标条目强制复核；支持保留本地或明确采用远端，其他确定条目继续。默认未传参数时保留原合同。
- **新增 `paratranz/batch_download_snapshot.py`**：正常一次 inventory、每个当前文件一次完整读取，来源失败不采用半份数据；支持当前精确路径与数字分片。404 有界刷新并重建该来源完整文件集合，未变化文件内容复用；跨来源重复 ID/文件归属失败明确隔离，权限/限流/取消不伪装缺文件。
- **修改 `application/projects/categorized_upload.py`**：复用只读来源水合，增加可选来源子集、逐来源失败和完整启用来源命名元数据；下载读取时保留其他系统/项目的外部引用。原上传默认完整读取、遇错失败的行为不变。
- **修改 `application/projects/gui_facade.py`**：增加 file_sources_snapshot 只读端口；replace_entry_records 透传现有 lifecycle 的 commit_mutation，允许最终提交与取消在同一临界区仲裁。
- **修改 `application/projects/lifecycle.py`**：仅优化真实还原点 OSError 的诊断，保留原异常原因并给出中文动作/本地未修改/原地重试提示，不改变其他 lifecycle 行为。
- **新增 `ui/operations/batch_download_session.py`**：组合工程来源、权限验证、来源范围缓存、分来源计划和当前路径重算。正常 unverified→available 不当作目标切换；缺文件/失败不永久缓存，跨缓存归属冲突清理后可重读；无关修订自动重基，路径变化与同条新编辑自动重新对照。全局身份变化和持续本地竞争保留选择并停止有界重试。
- **新增 `ui/operations/batch_download_commit.py`**：一个 TaskRuntime 任务，一次最终 Variant 状态提交；正常一个还原点，遇提交前修订竞争时刷新保护后有界重试。最终目标检查在实际 mutation 内执行。提交成功在同一仲裁锁内冻结 completed，迟到取消不反转成功；排队 Future 取消且执行体未进入时也能结束等待。
- **新增 `ui/operations/batch_download_view.py`、`batch_download_dialog.py`**：不可变视图值、插件选择、更新策略、后台对照、现有逐条复核窗口、一次确认更新、有界进度及详情；失败保留选择，具体重新对照原因保留，不再把零更新显示为可更新。
- **修改 `ui/operations/facade.py`、`production.py`**：生产批量下载组合并保活新窗口，单插件仍使用既有 coordinator；上传和自定义测试工厂保留原行为。
- **修改 `ui/operations/sync_review_projection.py`**：对照后本地新编辑的中文复核原因。
- **新增七个测试文件**：`tests/contracts/paratranz/test_batch_download.py`、`tests/paratranz/test_batch_download_snapshot.py`、`tests/application/projects/test_batch_download_sources.py`、`tests/ui/operations/test_batch_download_dialog.py`、`test_batch_download_production.py`、`test_batch_download_session.py`、`test_batch_download_task.py`。覆盖实际工程/投影/未保存状态与保存故障、生产卡片/菜单、真实 Qt worker、真实线程和线程池取消边界。
- **修改 `docs/requirements.md`、`docs/dev/paratranz.md`、`docs/dev/paratranz-categorized-upload.md`、`plans/paratranz-categorized-upload/plan.md`、`plans/INDEX.md`、`docs/changelogs/INDEX.md`**：原位更新批量范围、恢复/复核语义、使用入口、验收及索引，不修改既有增量历史。

职责复核：新增生产模块为 33～367 行，对话框 22 方法、会话 13 方法，来源服务 215 行、GUI facade 450 行/24 方法。既有超过阈值的 lifecycle 仅局部改善还原点错误文案；没有向其扩展批量业务职责，后续新增生命周期行为仍需先按责任拆分。

## 最终验证

仓库 uv 环境，--no-sync；UV cache、TEMP/TMP、pytest basetemp 均放在任务专属 `.tmp-batch-download-a0eb07b2`。最终整合命令：

```powershell
$env:TEMP = (Resolve-Path .tmp-batch-download-a0eb07b2/temp).Path
$env:TMP = $env:TEMP
$env:FAISS_OPT_LEVEL = 'generic'
uv --cache-dir .tmp-batch-download-a0eb07b2/uv-cache run --no-sync pytest tests/ui/operations tests/ui/test_download_plan_routing.py tests/paratranz/test_batch_download_snapshot.py tests/paratranz/test_current_path_snapshot.py tests/paratranz/test_file_publication.py tests/paratranz/test_file_transport_keys.py tests/application/projects tests/contracts/paratranz --ignore=tests/contracts/paratranz/test_terms_api_contract.py -k 'not test_reordered_source_projects_into_context_only_remote_plan' -q --basetemp .tmp-batch-download-a0eb07b2/final-regression-generic
```

结果：**919 passed、1 deselected、3 条既有弃用 warning，27.89 秒**。本轮七个新增文件的 **109 个用例**全部包含在该集合中，不把聚焦重跑重复相加。最后补充空插件明确跳过提示，并改共享来源的失效提示为上传/下载通用措辞。

最终全库 `uv run --no-sync ruff check src tests` 和 `ruff format --check src tests` **全部通过，2177 files already formatted**。中途曾检测到无关 AI 文件的导入顺序、行长和格式问题，未替其他工作线修改文件；最终复查已通过。对本轮 14 个生产文件与 7 个测试文件显式执行相同检查也通过，**21 files already formatted**；相关 tracked diff 的 `git -c core.whitespace=cr-at-eol diff --check -- <本轮文件>` 通过。

使用真实 BatchDownloadDialog 离屏渲染，并显式载入系统 Microsoft YaHei 字体检查中文长项目名、插件列表、状态、详情与按钮布局；未在用户正在运行的窗口执行下载。1 / 1,000 / 10,000 词条测试验证请求数随文件数增长，不是线上性能测量。

## 中间发现与修复证据

- 首轮两个失败来自新增测试错误使用 ErrorCategory.IO/OperationResult.failed 的参数类型，修正测试后真实工程的快照/提交故障原地重试通过。
- 独立审查发现提交成功后再次捕获可能把已成功操作报失败，已删除该捕获；提交后切账号回归仍返回真实成功。
- 真实 ThreadBackend 复现提交后迟到取消导致任务 cancelled，改为最终 mutation 同锁冻结 completed。后续又复现 completed 已出现、receipt 尚未返回时等待器误报失败，增加 started Event 区分正常收尾与排队未执行取消，真实线程/线程池 5 项回归通过。
- 缺文件缓存、跨缓存重复 ID、源仅改名后继续采用旧路径、验证状态升级误判均补回归并修复；冲突缓存可清理后原地重读。
- 404 刷新最初未重新计算新增数字分片，已改为来源级有界重建；新分片损坏时整来源不采用成功前缀。
- 初次扩大回归 891 passed / 22 failed：其中 21 项写回相关失败来自沙箱默认 TEMP 无写权限；设为任务目录后 40 项写回/迁移联合回归全通过，最终整合也包含并通过这些用例。
- 另一项 `test_source_update.py::test_reordered_source_projects_into_context_only_remote_plan` 预期两个 context_order_changed，实际为 upload_association_confirmed。通过 importlib 加载启动副本的 upload_planner.py 与 planner.py 后，独立执行同一用例仍得到相同失败（1 failed，1.06 秒），确认不由本轮可选下载复核参数引入；最终只排除该用例，未修改既有上传行为或断言。
- 末轮复验曾在 Faiss AVX2 原生模块加载时出现 Windows 0xc0000008 诊断，进程随后退出。根据已安装 faiss/loader.py 使用现有 FAISS_OPT_LEVEL=generic 选项，仅作用于测试进程；最终 919 项通过，没有修改依赖、产品启动配置或持久环境。

## 迁移与边界

无工程格式迁移、无新依赖，不恢复旧直接合并入口，不迁移/删除云端文件。批量下载仅面向当前权威工程；普通/分类文件继续采用当前布局。获取已经成功缓存文件的新云端修改，仍需开启新一轮下载窗口；失败/缺文件及路径恢复可以原地重试。

真实权限或磁盘错误不会被绕过；本地保持原状，保留选择并给出具体原因。仍有一项已独立复现的既有上传排序断言失败；全库静态检查最终通过。未运行全库 pytest、terms 接口合同、构建/安装包或真实账号下载；受控网关与本地工程回归不替代线上验收。任务专属临时文件收尾时清理。

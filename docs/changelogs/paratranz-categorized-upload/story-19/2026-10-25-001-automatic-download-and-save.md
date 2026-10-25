# 一次点击自动下载并保存

- 日期：2026-10-25
- Epic：paratranz-categorized-upload；Story：S19
- 需求：FR3.3、FR22.3、FR22.3.2
- [计划](../../../../plans/paratranz-categorized-upload/plan.md)

## 用户要求与结果

S18 虽已统一窗口和主按钮，仍要求用户先“开始对照”，再点“更新并保存”。
用户明确指出无需手动执行这些中间步骤，本轮改为选择来源后只点一次“下载并保存”。
后台自动完成读取、计划、更新和磁盘保存；只有真正无法确定的译文保留本地并进入复核。
可靠项先自动保存，接受复核选择后自动继续保存，不再点击主按钮确认第二遍。

打开窗口或改变来源/策略仍只改变选择，不自动请求云端。
下载前还原点、保存失败原窗口重试、成功云端内容复用、提交取消仲裁和当前工程/版本保护保留。
零远端变化但当前版本仍有未保存编辑时也保存；全部来源缺失/读取失败时不借机提交或显示假成功。

## 本次真实变更

- 新增 `src/transbridge/ui/operations/download_run.py`：独立、可测试的后台运行编排，
  比较后直接调用已有受保护持久化提交。源路径/修订变化后的可靠新计划最多自动接续两次，
  持续变化则保留选择原地重试。整个流程使用同一个 worker 和取消令牌；既成保存回执不被迟到取消反转。
  部分更新成功后投影剩余待确认行，明确区分“已保存多少”与“仍有多少待确认”。
- `src/transbridge/ui/operations/batch_download_dialog.py`：主按钮始终为“下载并保存”；
  删除手动对照/重新对照和两阶段主按钮状态，只保留 load/run 两类后台任务。
  接受冲突选择后直接启动自动流程；失败保留来源、策略和复核选择供原地重试。
- `src/transbridge/ui/operations/sync_review_dialog.py`：新增可选 `submit_on_accept=False`，
  统一下载传 True，按钮为“确认选择并保存”，说明确认后自动保存；其他现有调用默认仍暂存选择。
  模块与类说明同步当前两种明确行为，没有改变独立上传/双向同步的确认合同。
- `src/transbridge/ui/operations/batch_download_view.py`：末尾追加默认 False 的 `can_save`，
  区分可以保存当前已有编辑的有效快照与缺文件、读取失败和无可提交内容。
- `src/transbridge/application/projects/categorized_upload.py`：内存来源快照追加默认 False 的 `dirty`，
  从当前权威 ActiveProject 捕获，不依赖可能尚未刷新到 Qt 的界面脏状态；不涉及持久化 schema。
- `src/transbridge/ui/operations/batch_download_session.py`：完整来源成功且权威版本未保存时声明 `can_save`，
  复用原有空 patch 持久化保存，零差异不遗漏已有编辑。
  部分保存后只清除实际更新条目的复核保护，未处理及保留本地的保护在本轮窗口内继续有效。
- 新增 `tests/ui/operations/test_download_run.py`：11 项后台编排回归，覆盖自动发布、无可采用内容、
  部分完成、有界重算、阶段间取消、迟到取消、真实错误传递及零差异保存。
- 新增 `tests/ui/operations/test_download_automatic_recovery.py`：5 项真实 Session/Planner/TaskRuntime/
  生命周期回归，覆盖下载期间的新编辑、其他确定项自动保存、后续保留本地/选择远端及再次重试、
  比较后取消不建还原点、界面脏状态滞后时仍由权威状态触发零差异保存。
- `tests/ui/operations/test_batch_download_dialog.py`、`test_batch_download_production.py`：
  原两步断言改为一次点击完整完成，覆盖单/多来源、菜单/卡片/旧别名、无当前集合、复核、取消、
  失败重试、主线程响应、共享复核窗口默认/自动保存文案和零更新保存；不只检查按钮文字。
- `tests/ui/operations/test_unified_download_service.py`：实际默认 facade → Qt 窗口 → ApiWorker →
  真实 Service/API/文件网关 → 磁盘读回，仅替换 HTTP 响应，17 项通过。
  一次点击保存；首次磁盘错误后同按钮重试不增加 HTTP，复用相同还原点文件和字节；
  确定项先保存、真实复核模型接受候选后自动保存冲突；云端已一致但本地未保存、全缺文件及全冲突均有独立断言。
- `docs/requirements.md`：在 FR3.3、FR22.3、FR22.3.2 修正手动对照/计划后二次确认的旧要求，
  保留独立同步原确认合同。`docs/dev/paratranz.md`、`paratranz-categorized-upload.md`、当前计划与直接索引同步。
  S18 及更早增量不修改，工作区其他 AI/上传等已有变更不计入本轮。

## 先复现再修复的边界

1. 将真实生产端到端用例改为一次点击后，修前稳定失败：HTTP 已读取，但界面停在“对照完成，可以更新”，
   当前版本未保存。改为同 worker 自动运行后通过。
2. 独立审查发现自动部分保存后 `review_keys` 被整体清空，后续显式“保留本地”被当作过期选择丢弃，
   导致新编辑被远端覆盖。真实回归修前 1 失败/3 通过；改为仅解除实际更新条目的保护后全部通过，
   同时覆盖选择远端和再次重试，不以缓存或新校验掩盖问题。
3. 零差异保存最初读取 `context.dirty`，Qt 通知尚未交付时会误判已保存。
   新回归先复现返回预览而未落盘，再改为来源快照的权威 dirty，磁盘读回及清除 dirty 通过。

## 验证

环境为 Windows、仓库现有 Python 3.12.12/uv 环境，未设置 FAISS_OPT_LEVEL；
TEMP/TMP 与 uv cache 均在本轮有 owner 标记的独占临时目录。

```powershell
uv --cache-dir .tmp-auto-download-20261025/uv-cache run --no-sync pytest tests/ui/operations tests/ui/test_download_plan_routing.py tests/application/projects tests/contracts/paratranz tests/persistence/v2 tests/paratranz tests/ui/test_main_window_shell.py tests/ui/test_main_window_coordinators.py tests/ui/test_intent_router.py tests/ui/test_workbench_theme_migration.py -q --ignore=tests/contracts/paratranz/test_terms_api_contract.py --ignore=tests/paratranz/test_typed_client.py -k 'not test_reordered_source_projects_into_context_only_remote_plan' --basetemp .tmp-auto-download-20261025/final-authoritative
uv --cache-dir .tmp-auto-download-20261025/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-auto-download-20261025/uv-cache run --no-sync ruff format --check src tests
```

最终整合 **1,605 passed、1 deselected**，145.08 秒，退出码 0；4 条既有弃用 warning。
专项通过数与整合重叠，不重复累加。
此前修复复核保护后的整合为 1,604 passed、1 deselected、4 条既有弃用 warning，46.79 秒；
最后权威 dirty 修正的来源/真实界面专项 51 项通过，随后运行上述最终整合。
全库 Ruff check 通过，格式检查 2,189 文件通过，退出码均为 0。
真实 Qt 截图检查了单个主按钮、部分保存计数、待确认入口，以及复核的“确认选择并保存”按钮与说明，无文字截断。

继续明确排除 S16 已复现的旧上传排序断言，不改变无关上传行为；
`test_typed_client.py` 本地 HTTP server 在当前沙箱内连接受限，沿用 S18 的环境边界未重跑；
不相关 `test_terms_api_contract.py` 未运行。未运行全库 pytest、真实账号云端下载/写入、安装包构建或新性能基准。
本轮没有修改来源解析/缓存算法，不重复声称获得 S18 的性能收益。

## 兼容与清理

新增内存字段和共享复核参数均有默认值，不修改依赖、持久化 schema、游戏文件或云端布局；无数据迁移。
正常下载只有一次明确请求，独立同步与自定义通用计划仍遵守既有合同。
临时测试输入、截图及缓存确认任务所有权后删除，保留本记录和正式回归测试；无已知未解决的本轮业务缺陷。

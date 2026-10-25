# S12：普通与分类上传切换文件 API

日期：2026-10-25。Epic：paratranz-categorized-upload / Story：S12。
用户明确要求先为 API 代码补充使用备注，再按官网文件接口改造上传。

## 修改原因与行为

此前已有分类文件按词条 GET/PUT 并回读，上传后保存全部远端关联，造成请求数和本地序列化成本随词条数增长。当前正常 GUI 流程为本地生成文件、一次云端文件列表、按完整路径解析当前 ID、文件创建/增量原文更新/批量译文状态导入、文件结果。

官网 OpenAPI 0.5.7 于本次对话中从 https://paratranz.cn/api-docs?format=json 只读获取：原文更新 incremental=false 会删除缺失项，应用显式 true；译文按 key 匹配且不创建词条，支持 stage、safe/force、skip/hashMatched，需管理员以上权限；423 为文件忙，GET 文件词条为完整不分页响应。未将第三方旧版文档作为当前参数依据。

## 可归属本次的文件增量

- 修改 `src/transbridge/paratranz/api/paratranz_files_api.py`：模块及方法使用备注；保留既有文件路径调用接口，增加内存文件创建/更新、JSON 译文导入、incremental/skip/cancellation 参数。创建读取 {file, revision} 响应。
- 新增 `src/transbridge/application/file_upload.py`：复用既有分类/命名规则，生成普通或分类文件，不传历史远端 ID；四种上传内容模式、文件回执、明确 404 一次重定位、413 原文文件拆分及同文件译文分批、部分成功与取消。正常路径不读词条、不调用逐条写接口、不提交本地关联。
- 新增 `src/transbridge/paratranz/file_publication.py`：文件 API 网关、每服务/用户共享串行请求锁及每秒一次预算、可取消等待、仅明确 429 按 Retry-After 有界重试；超时不重放，识别 hashMatched/unchanged/empty，保留有界 HTTP 诊断。
- 修改 `src/transbridge/ui/operations/categorized_upload_bridge.py`：普通/分类共用来源快照和文件执行；删除生产会话的权限列表预读及关联检查点提交。保留工程/版本/账号/修订轻量检查、来源范围和会话日志。
- 修改 `src/transbridge/ui/operations/categorized_upload_dialog.py`：四种内容策略、管理员权限提示、普通/分类标签、文件计数与部分结果；沿用后台 worker、100ms 最新进度、取消和详情上限。
- 修改 `src/transbridge/ui/operations/facade.py`、`production.py`：默认生产普通上传入口接入共享文件窗口，保留窗口生命周期；自定义计划窗口及显式独立同步调用仍兼容其原合同，不作为常规 GUI 上传回退。
- 修改 `src/transbridge/application/projects/categorized_upload.py`、`gui_facade.py`：来源快照可显式忽略历史文件绑定及外部词条引用；默认兼容旧调用。只改快照参数/投射，不改变既有工程事务或其他命令行为。
- 新增 `src/transbridge/paratranz/path_snapshot.py`；修改 `ui/operations/categorized_download_scope.py`、`ordinary_upload_scope.py`：有登记来源的权威工程按当前插件根文件、分片及分类目录定位，逐文件一次读取完整词条，无需上传关联；兼容无登记来源的旧适配器，不扩展读取到其他插件。
- 新增 `tests/paratranz/test_file_publication.py`、`test_current_path_snapshot.py`、`tests/ui/operations/test_file_upload_production.py`：真实 prepared request 的文件/JSON 合同、请求规模不随条目数增长、当前路径下载和默认生产路由。
- 更新 `tests/ui/operations/test_categorized_upload_bridge.py`、`test_categorized_upload_checkpoints.py`、`test_categorized_upload_recovery.py`：将被替代的逐条核验/关联落盘断言换为文件请求、无本地提交、未保存编辑保护和当前路径恢复；保留真实本地工程 fixture。
- 更新 `tests/ui/operations/test_categorized_upload_projection.py`、`test_categorized_upload_dialog.py`、`test_upload_mode_routing.py`：上传不依赖关联存储，保留工作台编辑/保存保护，更新文件计数和策略选择合同。
- 更新 `docs/requirements.md` FR3.4～FR3.5、`docs/adr/045-original-qualified-plugin-entries.md` 的上传段落、`docs/adr/047-opt-in-categorized-project-upload.md`、`docs/dev/paratranz-categorized-upload.md`、`docs/dev/paratranz.md`：在原位置替换旧常规上传合同；ADR-045 的本地原文限定身份及下载规则保留。
- 更新 `plans/paratranz-categorized-upload/plan.md`：当前目标改为 S12，原 S01～S11 明确归为历史；最小更新 `plans/INDEX.md`、`docs/INDEX.md`、`docs/dev/INDEX.md`、`docs/changelogs/INDEX.md`。

启动时工作区已有大量 AI、同步、工程和分类上传的未提交/未跟踪改动。上述记录仅描述本次调用链/符号增量，不将 Git 对 HEAD 的全部差异归入本轮；未改 lockfile、包配置或用户工程数据，未提交或推送。

## 验证命令与证据

使用仓库 uv 环境，`--no-sync` 避免改动依赖。沙箱默认 uv/pytest 临时目录不可写，因此为命令设置 `UV_CACHE_DIR=.tmp-file-upload-qa/uv-cache`、`TEMP`/`TMP=.tmp-file-upload-qa/temp`，并使用任务专用 `--basetemp`；任务结束清理该目录。

最终扩大回归（退出码 0）：

```powershell
uv run --no-sync pytest tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_file_publication.py tests/paratranz/test_current_path_snapshot.py tests/paratranz/test_categorized_snapshot.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_categorized_file_import.py tests/paratranz/test_ordinary_upload_adapter.py tests/paratranz/test_ordinary_upload_paths.py tests/paratranz/test_single_upload_order.py tests/ui/operations tests/contracts/paratranz --ignore=tests/contracts/paratranz/test_terms_api_contract.py --deselect='tests/ui/operations/test_paratranz_download_recovery.py::test_snapshot_failure_keeps_unsaved_local_content[legacy-io-error]' -q --basetemp=.tmp-file-upload-qa/pytest-handoff
```

结果：**860 passed，1 deselected**，35.28 秒。覆盖新文件 1/1000/10000 条仍只有一次创建、四种内容模式、中文/嵌套目录、JSON stage、服务端无变更状态、404 同路径重建、413 拆分、423 文件忙、429 等待、跨窗口共享串行与排队取消、超时未知、403 部分成功后停止、无本地提交和下载隔离。

本机受控 HTTP 回归（沙箱首次因 WinError 10013 阻止 127.0.0.1 连接；获得执行环境允许后重跑，退出码 0）：

```powershell
uv run --no-sync pytest tests/paratranz/test_typed_client.py tests/contracts/paratranz/test_terms_api_contract.py -k 'not live' -q --basetemp=.tmp-file-upload-qa/pytest-http
```

结果：**28 passed，1 deselected**，15.12 秒；排除的为显式真实云端 smoke。此组与上面的 860 项不重叠，合计 888 项通过，不能与中间聚焦回归相加。

全库静态验证（退出码均 0）：

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
```

2162 个 Python 文件格式通过。目标差异 `git -c core.whitespace=cr-at-eol diff --check -- <本次相关路径>` 通过；仓库 Windows 行尾转换提示不代表空白缺陷。

## 已知失败与边界

- 扩大验证最初得到 858 passed、1 个 legacy 下载失败；另 9 个本机 HTTP 失败由沙箱套接字限制导致，独立重跑已全部通过。修订后最终矩阵为上面的 860+28，不重复累计。
- `test_snapshot_failure_keeps_unsaved_local_content[legacy-io-error]`：预期历史还原点 IO 失败令任务 FAILED，实际 COMPLETED。既有 S11 已记录相同失败；本轮用任务临时脚本强制两个下载调用点恢复旧 `ParaTranzRemoteSnapshotAdapter`，仍在原断言复现（1 failed，退出码 1），说明新当前路径适配器不是原因。最终单独排除，不在上传改造中修复。
- 中间聚焦运行曾因默认临时目录/本机网络限制以及尚未更新的旧 UI 文案断言失败；已调整运行环境、更新被替代的合同并复跑。没有把初次失败记作最终通过。
- 未运行真实云端写入、全库 pytest、发布构建或线上速度测量。文件导入的最终服务器表现仍需实际使用验证；不承诺按固定倍数加速。
- 旧独立同步计划和无登记来源调用保留公共兼容；既有云端文件/分片与本地旧关联不自动删除，工程 schema 无迁移。

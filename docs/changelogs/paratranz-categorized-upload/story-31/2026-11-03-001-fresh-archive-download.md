# S31：达到 50 个云端文件时生成新压缩包下载

日期：2026-11-03。Epic：paratranz-categorized-upload；Story：S31。对应 FR3.4.4、ADR-047。

## 行为

用户确认先计算需要下载的文件数，以 50 为切换门槛，再执行生成、下载、解压、匹配和清理。实际去重所选数不足 50 时保持逐文件读取；达到 50 时触发一次新导出，轮询最近导出，只有新 ID 且生成时间晚于本轮基线才下载。下载后再次检查导出身份，变化则拒绝采用。无历史导出时等待首次结果。默认轮询间隔 3 秒、最多 100 次，HTTP 沿用现有超时；不是严格 300 秒墙钟上限。

按 `raw/<完整云端路径>.json` 读取完整词条；路径约定复用仓库现有恢复工具及样例（原文件 `mod.json` 对应 `raw/mod.json.json`）。不读取 utf8 副本、无关插件或仅同 basename 的其他文件。所选内容按现有来源、key、原文及状态规则匹配并保存，不改变匹配算法或本地格式。原始 ZIP 和解压文件在快照读取结束时清理，之后的合并只保留本轮内存数据，由现有会话结束逻辑释放。

大批量触发新导出需要 ParaTranz 管理员权限。权限不足、超时、缺成员、包损坏或导出变化明确失败，不静默降级或采用旧包。原有小批量权限不变。

## 可归属修改

- 新增 `src/transbridge/paratranz/artifact_download.py`：`FreshArtifactDownload` 负责本轮新导出和下载、前后身份复核、协作取消及客户端 finally 关闭。复用现有 `ParatranzExportAPI`，不修改其用户已有变更；不使用面向持久 ZIP 发布的旧 `ArtifactWorkflow`。
- 新增 `src/transbridge/paratranz/artifact_file_reads.py`：`ArtifactFileReads` 拥有任务临时目录、精确 raw 成员定位和流式解压。仅使用本地生成的 file-ID 文件名落盘，拒绝不安全/重复/符号链接成员；单文件 64 MiB、所选解压总量 512 MiB 上限。关闭时删除本轮 ZIP 和提取文件。
- 修改 `src/transbridge/paratranz/batch_download_snapshot.py`：复用现有选择与归属规则计算去重文件数；达到门槛注入压缩包读取器，否则沿用受限并发；统一复用 `_file_entries` 和完整来源隔离，finally 负责临时资源清理。
- 修改 `src/transbridge/paratranz/download_progress.py`：首个进度包含实际需下载文件数，不增加重复首帧回调。
- 修改 `src/transbridge/smart_assistant/tools/tool_paratranz.py`：仅调整下载工具说明，明确 50 文件、新导出权限和清理语义。该既有大文件没有新增执行职责。
- 新增 `tests/paratranz/test_fresh_artifact_download.py`（12 项）、`test_artifact_file_downloads.py`（27 项）：新旧结果、失败/权限/取消、0/49/50/51 门槛、全项目与所选数、普通/分类/旧分片路径、逐文件与 ZIP 快照相等、内容/大小/路径安全及所有退出清理。
- 修改 `tests/contracts/paratranz/test_project_download_use_case.py`：新增 50 文件真实临时工程合入/保存/清理回归，验证译文及阶段、落盘快照和无逐文件请求。
- 更新 `docs/requirements.md` FR3.4.4、`docs/adr/047-opt-in-categorized-project-upload.md` 下载合同、S31 plan 及直接索引。

本轮不修改依赖、lockfile、schema、上传业务、用户数据或既有未提交 API/UI 改动；不提交、不发布。两个新增生产模块分别 79/103 行，职责独立，无尺寸豁免。

## 验证

Windows、Python 3.12.12、仓库现有 uv 环境；合成远程 API/HTTP、本机回环测试及临时本地工程，无真实 ParaTranz 生成/下载调用。首次默认沙箱临时目录有访问权限错误，改为本轮独占仓库临时目录后通过；未为测试改变生产临时目录策略。单独应用层测试曾输出 FAISS 原生异常诊断但最终 8 项通过，沙箱外复验同样 8 项通过。

```powershell
$env:TMP = 'D:\MyCode\TransBridge\.tmp-para-s31-qa'
$env:TEMP = $env:TMP
$env:QT_QPA_PLATFORM = 'offscreen'
uv run --offline --no-sync --cache-dir .tmp-para-s31-qa/uv-cache pytest tests/paratranz/test_fresh_artifact_download.py tests/paratranz/test_artifact_file_downloads.py tests/paratranz/test_batch_download_snapshot.py tests/paratranz/test_parallel_file_downloads.py tests/paratranz/test_download_progress.py tests/paratranz/test_plugin_directory_download.py -q --tb=short
uv run --offline --no-sync --cache-dir .tmp-para-s31-qa/uv-cache pytest tests/contracts/paratranz/test_project_download_use_case.py -q --tb=short
```

分别 81、8 项通过；它们均包含在下述扩大集合中，不重复计数。新增 40 项测试全部通过。

```powershell
uv run --offline --no-sync --cache-dir .tmp-para-s31-qa/uv-cache pytest tests/paratranz tests/contracts/paratranz tests/ui/operations/test_batch_download_session.py tests/ui/operations/test_batch_download_production.py tests/ui/operations/test_batch_download_results.py tests/ui/operations/test_batch_download_autosave.py tests/ui/operations/test_download_autosave_boundaries.py tests/ui/operations/test_download_run.py tests/ui/operations/test_unified_download_service.py tests/smart_assistant/tools/test_project_download.py -q --tb=short --junitxml .tmp-para-s31-qa/regression.xml
uv run --offline --no-sync --cache-dir .tmp-para-s31-qa/uv-cache ruff check src tests
uv run --offline --no-sync --cache-dir .tmp-para-s31-qa/uv-cache ruff format --check src tests
git diff --check
```

扩大回归 **661 通过、1 真实账号集成跳过、1 既有失败**（37.63 秒）；Ruff 通过、2366 文件格式通过，diff 空白检查通过。扩大测试经授权在沙箱外运行本机回环 HTTP 与临时工程原子写入。

既有失败：`test_batch_window_is_retained_until_destroyed_and_count_includes_other_uploads`，开始上传时夹具 `slots[key]` 为裸 `object`，缺 `collection`，在 `ui/source_input_state.py:24` 抛 AttributeError，尚未执行下载。使用 `git show HEAD:src/transbridge/paratranz/{download_progress,batch_download_snapshot}.py` 取原模块文本，在独立 Python 进程内 `exec(compile(...), module.__dict__)` 替换后，对该 nodeid 执行 `pytest.main([...,'-q','--tb=short'])` 仍以同一堆栈失败。未修改源码进行基线复验，也未修复范围外夹具。

## 限制与清理

未运行全仓 pytest、真实账号导出、线上时延测试、LLM 或构建发布。公开 OpenAPI 文档页面的本轮读取不可用，沿用已在仓库核验的接口及现有 raw 包样例；不把合成回归描述为真实服务验收。平台没有在此适配器中提供按本轮 Job ID 下载指定 ZIP 的能力，因此采用新结果身份/时间及下载前后身份一致性检查，不承诺服务端并发导出的事务隔离。

本轮独占 `.tmp-para-s31-qa` 在核验绝对路径与归属后删除，原始临时 XML 和 uv 缓存不提交；验证摘要保存于本记录。预先存在的 `.transbridge/` 及用户其他改动保持原样。

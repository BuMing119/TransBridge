# S31：压缩包原始词条文件后缀兼容

- 日期：2026-11-11
- Epic/Story：paratranz-categorized-upload / S31，大批量下载使用新生成的压缩包。
- 关联：[计划 S31](../../../../plans/paratranz-categorized-upload/plan.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[首次实现记录](2026-11-03-001-fresh-archive-download.md)。

## 原因与行为

用户反馈批量下载提示“新压缩包缺少所选文件的完整词条”。原实现已支持在完整云端文件名后追加 `.json`，因此 `.json.json` 本身不是已证实的故障原因；本次未取得截图对应的真实压缩包，不能确认该截图的具体路径差异。

本次补充有限兼容：优先读取 `raw/<完整云端路径>.json`；该成员不存在且云端路径以 `.json` 结尾时，尝试 `raw/<完整云端路径>`。若这个兼容成员同时是完整云端清单中另一个文件追加后缀后的路径，则拒绝采用，包含另一个文件未被选中的情况。两种形式同时存在时保持追加后缀形式优先。缺成员提示列出尝试的包内路径，不使用 utf8 副本、其他插件同名文件或仅 basename 匹配。

## 可归属变更

- 修改 [artifact_file_reads.py](../../../../src/transbridge/paratranz/artifact_file_reads.py)：`ArtifactFileReads.prepare` 在解压前调用新增 `_selected_members`，统一完成两种命名形式的解析、归属检查和缺成员诊断。既有大小上限、成员安全检查、完整来源处理和临时目录清理继续生效。
- 修改 [test_artifact_file_downloads.py](../../../../tests/paratranz/test_artifact_file_downloads.py)：新增 10 项回归用例，覆盖中文嵌套目录、原文件自身已有重复后缀、追加形式优先、已选/未选文件归属冲突，以及 utf8/其他目录/仅 basename 的拒绝；兼容分支与逐文件下载快照比较相等。
- 修改 [ADR-047](../../../adr/047-opt-in-categorized-project-upload.md) 的下载契约段落：同步命名优先级、兼容候选归属限制和路径诊断。
- 新增本记录；[changelog 索引](../../INDEX.md) 和 [计划](../../../../plans/paratranz-categorized-upload/plan.md) 仅增加直接关联链接。既有增量文件保持原样。

无文件删除或移动，不改依赖、lockfile、工程 schema 或上传流程；已有数据无需迁移。工作区原有发布 skill、发布记录及 `.transbridge/` 不归入本增量。

## 实际验证

使用 Windows、Python 3.12.12 和现有 uv 环境，`--no-sync` 避免修改依赖。首次普通沙箱运行受 uv 缓存及 Python 临时目录权限影响，未作为功能结论；随后获准在沙箱外运行测试，使用本次专用临时目录。

修复前，新增用例所在文件运行 **30 通过、7 失败**：

```powershell
$env:TEMP = 'D:\MyCode\TransBridge\.tmp-artifact-json-compat-20261111'
$env:TMP = $env:TEMP
uv run --no-sync pytest tests/paratranz/test_artifact_file_downloads.py -q -p no:cacheprovider --tb=line
```

修复后相关回归 **84 通过、3 条 SWIG 弃用告警**，包括 37 项压缩包读取测试：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
uv run --no-sync pytest tests/paratranz/test_artifact_file_downloads.py tests/paratranz/test_fresh_artifact_download.py tests/paratranz/test_batch_download_snapshot.py tests/contracts/paratranz/test_project_download_use_case.py -q -p no:cacheprovider --basetemp .tmp-artifact-json-compat-20261111/pytest
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.tmp-artifact-json-compat-20261111\uv-cache'
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
git diff --check -- src/transbridge/paratranz/artifact_file_reads.py tests/paratranz/test_artifact_file_downloads.py docs/adr/047-opt-in-categorized-project-upload.md
```

Ruff 检查通过，2527 个文件格式检查通过，目标差异空白检查通过。以上是实现阶段已执行的验证，本次归档不重复运行运行时测试。任务专用临时目录已核对路径及归属后删除。

## 遗留与验证边界

- 未使用截图中的真实压缩包或真实 ParaTranz 账号复验，尚不能认定原截图已被本修复解决。
- 未运行全仓 pytest、LLM、安装包构建或发布；当前为源码变更。
- 真正缺失或归属不明确的文件仍报错，不能用兼容规则替代完整性检查。

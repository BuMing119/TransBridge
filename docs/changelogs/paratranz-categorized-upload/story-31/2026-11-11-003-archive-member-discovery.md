# S31：压缩包全包成员检索

- 日期：2026-11-11
- Epic/Story：paratranz-categorized-upload / S31，大批量下载使用新生成的压缩包。
- 关联：[计划 S31](../../../../plans/paratranz-categorized-upload/plan.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、[前次后缀兼容](2026-11-11-002-raw-json-suffix-compatibility.md)。

## 原因与验收

用户补充真实 ZIP 后确认：包内 189 个成员全部位于 utf8/，没有 raw/。两个插件分别包含 8,451 和 11,711 条词条，合计 20,162 条，与截图总数一致；全部词条具有 key/original/translation/context/stage，文件名 UTF-8 标记正常。前次仅修复 raw 文件后缀，无法处理本次实际布局。

用户明确要求全面检索，不能继续按目录逐项添加兼容规则。本次建立全包成员索引，以完整云端路径及追加一次 `.json` 的变体按路径组件边界匹配，允许包根目录、utf8 或任意外层包装目录。保留已有规范 raw 优先级与后缀归属保护；不存在规范 raw 时采用通用发现路径，不按 basename 猜测或跨插件匹配。候选须对完整清单检查归属，包括未选文件。

通用发现候选必须是词条列表，显式包含 key/original/translation/stage，并复用 ParaTranzEntry 类型校验；context 保持可选。原文、状态缺失时不使用默认值掩盖不完整输出。格式化副本和无效 JSON 记录诊断；有多份有效候选时直接比较解析内容，一致才采用，冲突报错。规范 raw 保持原有格式兼容行为，不受通用候选副本影响。候选解压累计计入原有大小上限，原有路径安全及 finally 清理继续生效。

## 可归属变更

- 新增 [artifact_member_index.py](../../../../src/transbridge/paratranz/artifact_member_index.py)：`selected_members` 与 `_index_members` 统一负责全包路径索引、候选选择、归属检查和缺失诊断；从临时文件读取模块移出成员选择职责。
- 修改 [artifact_file_reads.py](../../../../src/transbridge/paratranz/artifact_file_reads.py)：物化所选文件的候选列表；`get_file_translation` 校验发现候选并核对多副本，保留失败原因；新增 `_validate_entries`，不改快照与合入算法。
- 新增 [test_artifact_member_discovery.py](../../../../tests/paratranz/test_artifact_member_discovery.py)：45 项回归，覆盖任意根目录、包装目录、追加后缀、中文路径、完整字段、字段类型、空词条列表、BOM、格式化副本跳过、内容一致/冲突与未选文件归属歧义；成功快照与逐文件路径比较。
- 修改 [test_artifact_file_downloads.py](../../../../tests/paratranz/test_artifact_file_downloads.py)：移除“一律拒绝 utf8 中完整词条”的过时断言；其他目录和 basename 误匹配仍拒绝，简化 utf8 的拒绝由新测试覆盖。
- 修改 [ADR-047](../../../adr/047-opt-in-categorized-project-upload.md) 和 [计划](../../../../plans/paratranz-categorized-upload/plan.md) 的当前下载合同，替换禁止 utf8 的过时说明；新增本记录并更新 [索引](../../INDEX.md)。历史增量保持原样。

没有修改依赖、锁文件、安装器、工程 schema 或上传流程，已有工程无需迁移。启动时已有 `.agents/skills/bm-release/SKILL.md` 与 `.transbridge/` 改动不归入本任务；用户 ZIP 不复制到测试夹具或版本库。

## 实际验证

环境为 Windows / Python 3.12.12 / 现有 uv 环境。使用 `--no-sync`，测试临时目录隔离为 `.tmp-artifact-utf8-20261111`；因本机沙箱临时文件权限约束，测试获准在沙箱外执行。

- 初始仅 utf8 的回归先在旧实现复现：19 失败、2 通过。随后按用户追加要求扩展为通用目录发现回归，初始测试文件改名为 `test_artifact_member_discovery.py`。
- 实现后聚焦测试：81 通过。
- 扩大 ParaTranz 模块及项目下载用例：450 通过，3 条既有 SWIG 弃用告警。

```powershell
$env:TEMP = 'D:\MyCode\TransBridge\.tmp-artifact-utf8-20261111'
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = 'utf-8'
uv run --no-sync pytest tests/paratranz/test_artifact_member_discovery.py tests/paratranz/test_artifact_file_downloads.py -q -p no:cacheprovider --basetemp .tmp-artifact-utf8-20261111/focused --tb=short
uv run --no-sync pytest tests/paratranz tests/contracts/paratranz/test_project_download_use_case.py -q -p no:cacheprovider --basetemp .tmp-artifact-utf8-20261111/regression --tb=short
$env:UV_CACHE_DIR = 'D:\MyCode\TransBridge\.tmp-artifact-utf8-20261111\uv-cache'
uv run --no-sync ruff check src tests --no-cache
uv run --no-sync ruff format --check src tests --no-cache
```

Ruff 检查通过，2539 个文件格式检查通过。

另外用 `uv run --no-sync python -` 执行一次真实 ZIP 离线冒烟：从包内 utf8 相对路径重建模拟云端文件清单，替换下载器为本地只读复制，经生产 `BatchDownloadSnapshotReader.fetch` 和 `ArtifactFileReads` 读取两个插件；与相同内容走逐文件入口生成的快照逐项相等。结果为 189 文件、20,162 条词条，来源分布 8,451 / 11,711；确认没有逐文件网络回退，所有临时解压目录已清理。这验证实际附件的解析与匹配，不冒充真实账号文件清单或线上下载复验。

## 遗留与验证边界

- 未运行全仓 pytest、真实 ParaTranz 网络导出/下载、GUI 交互或安装包构建发布；当前交付为源码修复。
- 全包检索仍要求完整云端路径可对应，真正缺失、字段不完整、归属歧义及内容冲突继续明确报错；不使用模糊同名替代。
- 文件字段校验不能证明服务端是否过滤过某些词条，本次真实附件数量与用户截图一致；未引入未经官方接口证实的导出参数。

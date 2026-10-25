# S13：原文限定词条使用可还原的 ParaTranz 传输 key

日期：2026-10-25。Epic：paratranz-categorized-upload；Story：S13。用户先要求只读分析，随后以 bm-pilot 明确授权落实对话中的哈希 key 方案。对应 FR3.4.6/FR3.5、ADR-045/047。

## 行为与边界

普通与分类文件上传现在均在传输时编码原文限定词条，同 key 不同原文留在同一个正常文件，不再为它们逐条生成冲突文件。在线下载及下载 JSON 导入恢复原始 key 和精确原文身份；本地、插件 key、来源顺序与工程 schema 不变。不增加网络校验或本地映射表。

格式为 `__transbridge_key__:v1:o:<无填充base64url原key>:<完整SHA256精确UTF-8原文>`。普通 key 保留；命中保留前缀时使用 v1:k 转义。编码不依赖当前选择、文件类别、条目排序、译文/状态或 ID。非法格式、未知版本与原文哈希不符明确诊断，不猜测截断或静默映射。同身份重复仍失败，不以位置编号制造身份。

旧裸 key 与旧冲突文件保持下载兼容；新旧记录内容不同进入既有复核，相同内容可使用共同内容。首次切换先上传原文或使用原文加译文；仅译文不建立新 key。本轮不自动搬迁、合并或删除云端旧文件/余项，不迁移评论/历史，不操作真实云端。

## 可归属本次的文件增量

- 新增 `src/transbridge/application/io/paratranz_keys.py`：唯一编解码规则、版本、转义、原文证明与结构错误。
- 修改 `application/io/paratranz_mapping.py`：ParaTranz JSON 序列化使用编码 key；读取先还原完整 EntryKey，再做原有重复身份/远端 ID 检查；无效行返回可定位诊断，不影响有效行。
- 修改 `application/file_upload.py`：以编码后的 key 检查唯一性；普通/分类共用规则，分类调用关闭冲突文件拆分。413 原文拆文件/同文件译文分批保留已经生成的 key。
- 修改 `application/categorized_upload/classification.py`：增加 split_conflicts 参数；生产文件上传显式 false，旧独立词条 planner/live_targets 的默认拆分合同保留。该兼容层在旧词条同步接口退役时移除，不是新 GUI 的备用路径。
- 修改 `paratranz/sync_snapshot.py`：decode_keys 为显式只读适配选项，默认保留远端 key；解码不改变原始载荷的修订哈希。
- 修改 `paratranz/path_snapshot.py`、`paratranz/categorized_snapshot.py`：在线下载启用解码。
- 修改 `ui/operations/categorized_download_scope.py`：各下载兜底分支启用解码，非下载不启用。
- 修改 `smart_assistant/tools/tool_paratranz.py`：将操作方向传给快照装配，助手/MCP 下载计划启用解码，旧上传/双向计划保留原始远端 key。此大模块仅作既有装配的局部参数联动，未增加编解码职责。
- 新增 `tests/contracts/io/test_paratranz_transport_keys.py`：精确空白、换行、Unicode、版本、转义、篡改诊断、离线 JSON 导入导出完整身份及 ID 往返。
- 新增 `tests/paratranz/test_file_transport_keys.py`：普通/分类单文件请求、不同子集/顺序稳定、同身份重复失败、413、真实下载计划及本地应用、新旧候选复核、旧冲突路径兼容、原文变化拒绝、1000 条限定身份仍一次文件写入。
- 修改 `tests/smart_assistant/tools/test_paratranz_sync_plan.py`：助手下载计划端到端匹配编码词条，不写云端。
- 修改 `docs/requirements.md` FR3.4.6/FR3.5、ADR-045/047、`docs/dev/paratranz-categorized-upload.md`：在原位替换旧文件拆分合同，明确迁移与回退边界。
- 修改 `docs/dev/paratranz.md`：修正过期的生产上传入口/核验描述，指向当前文件流程。
- 修改 `plans/paratranz-categorized-upload/plan.md`：S13 契约、落点、验收、验证及兼容层退出条件；最小更新 `plans/INDEX.md`、`docs/INDEX.md`、`docs/dev/INDEX.md` 与 `docs/changelogs/INDEX.md`。

工作区在本轮开始前已有大量未提交/未跟踪文件，含 S12 文件 API、AI、工程及同步改动。本记录只描述上述符号与段落增量，不将对 HEAD 的全部差异归入本轮。未改 lockfile、包配置或用户工程数据，未 commit/push。

## 实际验证

使用仓库 uv 环境和 `--no-sync`。命令设置 `UV_CACHE_DIR=.tmp-transport-key-qa/uv-cache`，`TEMP`/`TMP=.tmp-transport-key-qa/temp`，避免沙箱默认临时目录权限问题。任务目录有独立 owner.txt，收尾核实绝对路径、归属和重解析点后清理。

最终扩大集合命令：

```powershell
uv run --no-sync pytest tests/contracts/io tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_file_transport_keys.py tests/paratranz/test_file_publication.py tests/paratranz/test_current_path_snapshot.py tests/paratranz/test_categorized_snapshot.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_categorized_file_import.py tests/paratranz/test_ordinary_upload_adapter.py tests/paratranz/test_ordinary_upload_paths.py tests/paratranz/test_single_upload_order.py tests/ui/operations tests/contracts/paratranz tests/smart_assistant/tools/test_paratranz_sync_plan.py --ignore=tests/contracts/paratranz/test_terms_api_contract.py --deselect='tests/ui/operations/test_paratranz_download_recovery.py::test_snapshot_failure_keeps_unsaved_local_content[legacy-io-error]' -q --basetemp=.tmp-transport-key-qa/pytest-final
```

结果：1145 passed、1 failed、1 deselected，105.19 秒，退出码 1。唯一失败是本轮新增旧冲突文件测试误读取 `EntrySummary.translation`，实际 DTO 仅有 translation_hash；改为比较真实摘要哈希后，对同一生产代码运行以下完整边界集合：

```powershell
uv run --no-sync pytest tests/contracts/io/test_paratranz_transport_keys.py tests/paratranz/test_file_transport_keys.py tests/smart_assistant/tools/test_paratranz_sync_plan.py -q --basetemp=.tmp-transport-key-qa/pytest-boundaries-final
```

结果：29 passed，1.19 秒，退出码 0，包含上述失败项的修复复验。其中 28 项已在扩大集合通过，另外 1 项是该失败项的修复复验，不重复累加；扩大集合覆盖的 1146 个不同用例均取得通过结果。此前扩大回归还取得 1139 passed、1 deselected（102.34 秒，退出码 0）；中间聚焦测试的 namespace/scope 与 DTO 字段使用错误均为测试装配问题，修正后通过，不计入最终独立数量。

插件/集合兼容回归：

```powershell
uv run --no-sync pytest tests/converter/test_original_match_dsd_roundtrip.py tests/converter/tests_translation_entry_collection.py -q --basetemp=.tmp-transport-key-qa/pytest-plugin-keys
```

结果：22 passed，0.88 秒，退出码 0；与上述扩大集合无重叠，最终分组验证覆盖 **1168 个不同用例**。不是单次全库 pytest 全绿声明。

最终静态检查：

```powershell
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
```

均退出码 0；2165 个 Python 文件格式通过。相关 tracked 差异使用 `git -c core.whitespace=cr-at-eol diff --check -- <相关路径>` 检查；未跟踪源码也经过 Ruff/格式及逐符号审查。

## 遗留与未验证

- 排除的 legacy 下载还原点 IO 失败为 S11/S12 已独立复现的旧失败，本轮未再次修复或声称通过，详见 S12 记录。网络 terms/typed-client 合同未涉及本轮编解码修改，未重复运行本机 HTTP 或真实账号测试。
- 未执行真实云端写入、全库 pytest、发布构建或线上速度测量。1000 条限定身份使用一个文件写入为受控 prepared HTTP 回归结果，不是线上耗时测量。
- 不自动迁移旧云端布局、译文、评论或历史；旧文件保留。旧版不保证能识别新传输 key，回退时应保留旧数据。

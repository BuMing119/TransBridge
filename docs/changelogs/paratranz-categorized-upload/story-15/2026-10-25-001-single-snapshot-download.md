# S15：当前文件范围与单快照下载

日期：2026-10-25。Epic：paratranz-categorized-upload；Story：S15。用户明确要求 bm-pilot 执行“不用兼容旧的，然后其他该优化的优化”；沿用已授权子 agent 协作。对应 FR3.3/FR3.4.4/FR22.3、ADR-045/047。

## 交付行为

GUI 下载在一轮对照中只验证一次项目成员权限、读取一次完整文件列表、每个当前文件读取一次完整内容。复核选择、策略修改、确认授权和执行复用同一份不可变快照，不再次请求云端。重开窗口或更换目标、账号、配置、来源路径、工程版本、本地内容/修订后重新读取；不建立跨窗口缓存。一次读取失败不会缓存不完整结果。

当前文件范围为普通根文件、共享分类器当前生成的精确分类文件及 `_N.json` 分片。任务名称沿用 QUST:FULL.original 优先规则；不扫描整个插件目录，不读取旧任务名/ID 文件、旧冲突文件、其他路径同 key 内容或保存的文件 ID，不回退全项目 strings。无法定位来源时明确失败。

按当前来源、key 和精确原文匹配；历史词条关联不优先、不阻断、不拆散等价内容分组，也不持久保存下载关联。真实多译文继续复核；当前响应重复 ID 和身份/原文自相矛盾输入不可采用。同任务日志的序号错位继续按精确原文匹配。只原子更新已有译文/状态，保留本地原文、上下文、顺序，不新增/删除源条目。实际合并移除当前项目旧词条关联，其他系统/项目关联保留。

独立上传与双向同步维持其已有公共合同；文件上传继续按当前路径和编码 key 定位。确定性 413 拆分和两个转换脚本保留。旧直接下载、文件选择/批量合并、分类绑定读写/关联回执保存已移除，未开放批量下载计划时明确提示逐插件下载。

## 本轮可归属变更

- 新增 `application/sync/reviewed_snapshot.py`：不可变 ReviewedDownloadSnapshot，只提供内存 fetch，并校验目标、namespace、限制及取消。
- 新增 `ui/operations/download_session.py`：会话按目标、配置、工程版本、本地快照和根路径限定；完整读取成功后才发布快照，保留本轮验证的项目名称。
- 修改 `ui/operations/production_support.py`：SyncRequest 携带下载会话和固定根路径；下载快照不读取历史引用，重复旧引用不再阻塞；下载不应用本地删除。目标检查增加可用状态，避免账号与绑定不一致时身份字段仍相同而误判有效。
- 修改 `ui/operations/sync_preparation.py`：下载准备复用会话快照与权限结果；PreparedSync 保留供授权、执行使用的同一 snapshot port。下载统一 local_state_only，预检说明只更新工程已有译文/状态。
- 修改 `ui/operations/paratranz_sync.py`：相同范围的策略/选择编辑传递同一会话；执行器直接使用 PreparedSync.snapshots。提交回调紧邻本地替换再次校验目标，使用保存默认绑定后最新的权威修订，避免准备后切账号/配置/路径仍覆盖。
- 修改 `ui/operations/paratranz_dialog.py`：对照提示说明本轮内容，重开窗口获取最新云端修改。
- 修改 `paratranz/path_snapshot.py`：仅接受当前精确路径和数字分片；用共享分类核心计算路径。构造时解析可注入网关，保留完整响应、文件归属、ID/上限及取消边界。
- 修改 `ui/operations/categorized_download_scope.py`：从当前完整来源确定分类路径，选择部分词条不改变任务标题索引；删除旧保存绑定和全项目回退。
- 修改 `application/sync/download_planner.py`、`candidate_matching.py`：移除历史关联优先级和占用阻塞；无限定 key 同样要求精确原文自动匹配；相同内容按内容分组，人工选择不被当作下轮永久绑定。
- 修改 `application/sync/local_candidate.py`、`executor.py`：候选构造增加独立 attach_remote_refs/preserve_local_source 参数，默认保留通用同步合同；DOWNLOAD 显式不保存关联并保留本地源结构。
- 删除 `paratranz/categorized_snapshot.py`、`paratranz/workflow/downloader.py`、`ui/operations/authoritative_batch_download.py`；清理 package/workflow 导出。`ui/workbench/cards/download_card.py` 从旧文件选择/直接写入路线简化为仅派发共享计划。
- 修改 `application/projects/categorized_upload.py`：从约 496 行收敛为约 190 行只读权威来源 snapshot/current；保留全部来源水合、baseline、未保存译文、修订和 generation 检查；删除 bindings/file_scope/commit/_save 及引用保存/回滚专属 helper。
- 修改 `application/projects/gui_facade.py`、`bootstrap/persistence.py`、`ui/operations/categorized_upload_bridge.py`：移除三个无消费者绑定接口、持久化注入和 include_bindings 参数。修改 `application/categorized_upload/models.py`、`__init__.py` 删除 FileBinding。
- 删除只测试死保存闭环的 `scripts/benchmark_categorized_upload.py`、`scripts/benchmark_categorized_upload_ui.py`；两个转换脚本未删除，最终回归包含全部 12 个脚本用例。
- 新增 `tests/ui/operations/test_download_snapshot_session.py`：一次读取跨编辑/确认/执行、下一窗口刷新、失败重读、历史重复引用、源保真、目标和本地变化保护；1/1,000/10,000 条同一文件的请求数量不变。
- 新增 `tests/ui/operations/current_download_support.py`：真实 CurrentPathSnapshotAdapter 后的受控文件网关；fake 的 list_entries 仅作测试内容供应，生产不使用项目词条回退。
- 新增 `tests/ui/test_download_plan_routing.py` 15 项入口回归与 `tests/contracts/paratranz/test_local_download_candidate.py` 6 项参数/通用合同回归。重写旧绑定下载合同 `test_categorized_unbound_source_download.py` 为当前范围验收。
- 更新 `tests/paratranz/test_current_path_snapshot.py`、`test_file_transport_keys.py`、`test_workflow.py` 及 I/O facade 合同；删除旧 categorized_snapshot 和直接下载专属测试。
- 更新 UI 下载 authority/recovery/review/feedback/submit/diagnostic/ordinary_path/roundtrip 测试：不依赖旧关联、同轮云端改变不废弃已下载内容、新窗口读取新内容；后续独立上传重新匹配，只有上传确认后才保留其 ID/失效恢复合同。
- 更新下载 resolution/candidate/plan/upload 往返合同；项目分类来源测试保留真实 snapshot/current 和 guard，用畸形历史元数据、来源重开、零关联提交替代死保存闭环测试；UI checkpoint/projection/recovery 监视真实生命周期或完整快照，不再 mock 已删除接口。
- 原位更新需求、ADR-045/047、开发说明、计划和直接相关索引，移除已删除下载 API 的示例。

工作区启动时已有 S12～S14、AI、工程和同步相关大量未提交内容。这里只记录上述符号增量，不把 HEAD 全部差异归入本轮。部分死模块/脚本在开始时为未跟踪文件，删除不会显示为 tracked diff；已显式列出。没有修改 lockfile/schema、提交 Git 或操作真实云端。

## 验证

使用仓库 uv 环境与 --no-sync。主会话设置 UV_CACHE_DIR=.tmp-s15-download-qa/uv-cache、TEMP/TMP=.tmp-s15-download-qa/temp，各轮使用独立 basetemp；子 agent 的专属目录各自清理。

最终代码状态的整合命令：

```powershell
uv run --no-sync pytest tests/contracts/io tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_file_transport_keys.py tests/paratranz/test_file_publication.py tests/paratranz/test_current_path_snapshot.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_ordinary_upload_adapter.py tests/paratranz/test_ordinary_upload_paths.py tests/paratranz/test_single_upload_order.py tests/paratranz/test_workflow.py tests/ui/operations tests/ui/test_download_plan_routing.py tests/contracts/paratranz tests/smart_assistant/tools/test_paratranz_sync_plan.py tests/scripts/test_categorized_conversion.py tests/parser/test_plugin_parser.py tests/converter/test_original_match_dsd_roundtrip.py tests/converter/tests_translation_entry_collection.py --ignore=tests/contracts/paratranz/test_terms_api_contract.py -q --basetemp=.tmp-s15-download-qa/pytest-final
```

结果：**1147 passed，9 条既有弃用 warning，45.89 秒**。没有 deselect 下载用例。仅不运行本轮未改的 terms 本机 HTTP/外部接口合同。

独立且与上述集合不重叠的入口/主题验证：

```powershell
uv run --no-sync pytest tests/contracts/projects/test_authoritative_mutation_paths.py::test_download_card_requires_the_shared_plan_without_direct_projection_writes tests/ui/test_workbench_theme_migration.py -q --basetemp=.tmp-s15-download-qa/pytest-route-final
```

结果：**6 passed，4 条既有弃用 warning，1.93 秒**。最终两组覆盖 **1153 个不同用例**，没有将前面聚焦重跑数量相加。

```powershell
uv run --no-sync ruff check src tests scripts/convert_plugin_to_jsons.py scripts/plugin_eet_to_json.py
uv run --no-sync ruff format --check src tests scripts/convert_plugin_to_jsons.py scripts/plugin_eet_to_json.py
```

结果：全部通过，**2157 文件格式通过**。相关 diff 使用 `git -c core.whitespace=cr-at-eol diff --check` 检查，未改变 Git 配置。

本轮计划、ADR、开发说明和新增 S15 索引共 143 个相关相对链接可解析。尝试遍历整个历史 changelog 索引时，发现已有 `maintenance/requirement-code-review-remediation/2026-08-18-010-Story确认.md` 链接缺失；未修改无关历史条目，不宣称全索引无断链。

中间失败与修正证据：

- 单快照最初 10 项中 1 项失败，揭示账号改变时绑定 resolver 仍返回绑定旧账号字段；增加目标可用状态/ready 校验后，16 项 session+target 回归通过。
- 独立只读审查在 TaskRuntimeCommitGuard.commit 前切换账号，复现旧内容仍提交。修复 commit_local 后同一场景 job=failed、集合不变；账号/配置/路径 3 个回归与原默认绑定修订场景均通过。
- 首轮 619 项整合为 614 通过、5 失败，均为上传往返 fixture 还使用旧下载适配器、依赖下载 ID。改为文件网关和新往返语义后 25 项邻接测试通过，最终 1147 项完整通过。
- S11～S14 曾排除的 legacy 还原点 IO 测试在此定位为旧故障注入：它 patch 活跃 store 实例，但真实恢复写入使用 detached store。改为 patch VariantStore.save_snapshot 的实际写入边界后，5 项还原点/本地保护用例全部通过；没有把此项记作业务修复或继续排除。
- 子 agent 初期运行旧源码型 project contracts 有两项失败，分别要求 AI task_session/context 含已被其他工作移动的字面文本；未修改这些无关断言，最终只选本轮相关的下载卡合同。未宣称全库 pytest 通过。
- 清理过程中一次 Ruff 捕获临时编辑尚未补全的空行；最终格式化及全库检查均通过。

1/1,000/10,000 条文件集成测试包含策略修改、再次复核、授权和真实本地原子替换，均只调用首次 membership、inventory、file 读取，不写云端；这是请求数量验收，不是线上耗时或加速倍数测量。

## 迁移与验证边界

旧云端命名需按当前布局重新上传才能进入在线下载，不自动迁移、改名、删除或转移评论/历史。当前合法裸 key 和离线 JSON 编解码保持；历史工程分类绑定扩展字段不再读取，也不为清理而额外写工程。仓库外调用已删除 API 的调用方需切换当前文件快照和计划入口。

云端在读取后的修改不使本轮快照失效；要获取新内容需重开窗口。不同文件依次读取不构成服务端全项目原子快照。首次网络读取不会因关闭窗口立即中断，资源随 worker 完成释放，晚到结果不会提交；未做真实网络下 Qt 关闭压力或超过 100 窗口压力测试。

没有运行真实云端读写、全库 pytest 或发行构建；没有新增依赖或修改用户翻译数据。代码审查无未解决 finding。任务专属临时目录在核对绝对路径、所有权标记及无重解析点后清理。

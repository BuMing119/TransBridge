# S14：统一分类、任务原名和旧代码清理

日期：2026-10-25。Epic：paratranz-categorized-upload；Story：S14。用户明确授权按已分析方案以 bm-pilot 完成实现和验证，并允许多个子 agent。对应 FR3.4.6/FR3.5、ADR-045/047。

## 交付行为

上传与本地导出共用一套纯分类结果。任务按 Quest ID 分组，文件名优先取同一完整来源中准确 FormID 对应的 QUST:FULL.original；不取译文、日志或目标文本。名称缺失、空白或不同原名歧义时回退 Quest ID，无关联使用未关联任务。清理/截断/大小写重名持续追加来源 Quest ID，直到唯一，真实原名模拟自动后缀也不会覆盖或阻塞。名称在文件选择前确定。

共享分类同时负责其他类型兜底和来源顺序。上传添加插件目录，本地脚本在指定输出根下使用任务/子目录。保留本地/插件 key、S13 传输 key、来源身份、独立通用同步和 413 行为。无需新增云端检查或 ID 映射表。

两个脚本保留转换能力和原导出/转换函数签名，改用命令行路径与规范 transbridge 包导入；移除个人硬编码路径。加载失败不再被空集合掩盖。严格插件入口做最小 TES4 头边界检查，不承诺检测全部二进制损坏；默认 legacy 解析行为不变。

现有云端 Quest ID 文件不自动改名或迁移，新文件名按新路径上传；旧文件继续可读，不删除用户数据。脚本旧平铺任务输出现在统一到输出根/任务/。已删除旧分类上传/计划接口的外部直接调用方需改用共享分类及文件执行入口；存活普通兼容上传保留。

## 可归属本轮的文件变化

- 新增 `src/transbridge/application/io/paratranz_classification.py`：ClassifiedGroup/classify_entries、准确 QUST:FULL 原名索引、源内分组、稳定路径消歧、共享路径清理和来源序号投影；没有网络/UI 依赖。
- 修改 `application/categorized_upload/classification.py`：只包装完整来源的共享组与插件目录，保留来源目录消歧；删除重复分类/任务命名及旧 split_conflicts 分支。`application/file_upload.py` 移除旧分支参数，继续按编码 key 检查唯一性。
- 修改 `converter/translation_entry_collection_export.py`：保留 export_to_categorized_json_files 签名，只调用共享分类与 ParatranzJsonAdapter 写入，创建父目录且不重排已投影顺序；删除旧循环、_dial_filename、get_categorized_file_names。
- 修改 `scripts/convert_plugin_to_jsons.py`、`scripts/plugin_eet_to_json.py`：参数化输入/输出，支持 indent/ensure-ascii，第二个脚本保留公开转换函数；单次调用 PluginParser 的严格入口，异常保留原对象和 cause，成功后才创建导出目录。
- 修改 `parser/plugin_parser.py`：仅 raise_on_error=True 分支新增最小头校验（至少 24 字节 TES4、声明头内容长度不得超出文件），保留原始加载异常及默认兼容行为。
- 修改 `paratranz/workflow/uploader.py`：删除旧 upload_collection、detect_conflicts、ConflictInfo/FileMaps、name_conflicts；保留 upload_collection_as_single、文件定位、四种模式和 413 拆分，简化仍用的文件映射。
- 修改 `ui/workbench/cards/upload_views.py`：删除无人调用的 ConflictResolveDialog/FileSelectionDialog；保留活跃普通/批量窗口。`upload_card.py` 仅移除失效的批量冲突警告分支，保留本轮之前已有的上传路由修改。
- 删除 `application/categorized_upload/` 内的 planner.py、direct.py、executor.py、live_targets.py、checkpoints.py、fingerprints.py、ownership.py：审计确认仅彼此调用和包顶层导出，无业务入口。精简 __init__.py 与 models.py，保留 UploadSource/CategorizedFile/FileBinding/RemoteEntry/RemoteFile/FileCreationRejected 等仍有真实用途的类型。
- 修改 `application/categorized_upload/diagnostics.py`，删除仅服务死执行链的 file_fields/log_plan/log_receipt；保留通用日志作用域/事件/异常。修改 `ui/operations/categorized_upload_feedback.py`，删除旧 receipt_details，保留 UploadProgress。
- 修改 `paratranz/categorized_upload.py`：删除只服务旧执行链的 snapshot/read_file/move_entry，说明该类是普通上传兼容网关；保留 OrdinaryUploadAdapter 使用的方法及新文件网关复用的取消/完整列表/路径工具。file_creation.py 的验证函数仍有真实调用，未删除。
- 新增 `tests/contracts/io/test_paratranz_classification.py`：原名精确选择、回退/歧义、大小写/格式清理/截断/连续撞名、来源隔离、选择稳定、未知类型与全局顺序。
- 修改 `tests/contracts/io/test_paratranz_facades.py`、`test_paratranz_order_export.py`：统一任务目录、上传/本地产物一致、哈希 key 及身份不变、全局顺序不被单文件重算。
- 新增 `tests/scripts/test_categorized_conversion.py`：两个 CLI 在任意 cwd 下使用真实合成插件/EET 转换，源插件字节不变；缺失/目录/截断插件与缺失/损坏 EET 非零退出，无输出目录；函数异常/cause 原样传播且只加载一次。
- 修改 `tests/parser/test_plugin_parser.py`：严格头边界、有效空插件、默认兼容与原异常传播。
- 修改 `tests/paratranz/test_workflow.py`：必要创建/更新/错误/进度覆盖迁至仍存在的单文件入口。
- 删除只测试死执行闭环的 `tests/application/categorized_upload/` 中 test_adversarial_upload.py、test_checkpoints.py、test_direct_upload.py、test_execution.py、test_fixed_paths.py、test_recovery.py，以及 `tests/paratranz/test_categorized_file_import.py`。
- 收敛 `tests/application/categorized_upload/test_planning.py`、test_diagnostics.py：保留共享分类与通用日志安全合同。`tests/paratranz/test_categorized_upload_gateway.py` 保留存活 API 契约，将完整列表/分页/字段/隐私验证改用 find_files，删除已退役快照/移动专属合同。
- 新增 `tests/paratranz/ordinary_upload_support.py`：从已退役测试提取普通适配器仍需要的真实离线 HTTP fixture；修改 test_ordinary_upload_adapter.py 引用。`tests/ui/operations/test_categorized_upload_feedback.py` 保留进度覆盖，移除死回执函数测试。
- 修改需求、ADR-045/047、分类上传开发说明和 paratranz.md：原位替换旧接口和流程，删除已移除 API 的示例；更新计划及直接相关索引。

本轮开始时工作区已有大量未提交/未跟踪修改，含之前 S12/S13 的文件上传、AI、工程和同步工作。以上仅记录可归属的符号增量，不把 HEAD 全部差异归入本轮。旧执行模块及部分旧测试原为未跟踪文件，删除不会显示在 tracked diff，故在此明确列出。未修改 lockfile、包配置或用户工程数据，未 commit/push。

## 验证命令与结果

使用仓库 uv 环境与 --no-sync；设置任务专属 UV_CACHE_DIR=.tmp-s14-unified-qa/uv-cache、TEMP/TMP=.tmp-s14-unified-qa/temp，并使用独立 basetemp。子 agent 自建的其他临时根各自核实归属后清理；主任务根收尾核实 owner.txt、绝对路径和无重解析点后清理。

最后代码状态的整合回归：

```powershell
uv run --no-sync pytest tests/contracts/io tests/application/categorized_upload tests/application/projects/test_categorized_upload.py tests/paratranz/test_file_transport_keys.py tests/paratranz/test_file_publication.py tests/paratranz/test_current_path_snapshot.py tests/paratranz/test_categorized_snapshot.py tests/paratranz/test_categorized_upload_gateway.py tests/paratranz/test_ordinary_upload_adapter.py tests/paratranz/test_ordinary_upload_paths.py tests/paratranz/test_single_upload_order.py tests/paratranz/test_workflow.py tests/ui/operations tests/contracts/paratranz tests/smart_assistant/tools/test_paratranz_sync_plan.py tests/scripts/test_categorized_conversion.py tests/parser/test_plugin_parser.py tests/converter/test_original_match_dsd_roundtrip.py tests/converter/tests_translation_entry_collection.py --ignore=tests/contracts/paratranz/test_terms_api_contract.py --deselect='tests/ui/operations/test_paratranz_download_recovery.py::test_snapshot_failure_keeps_unsaved_local_content[legacy-io-error]' -q --basetemp=.tmp-s14-unified-qa/pytest-handoff
```

**1112 passed、1 deselected**，85.94 秒，退出码 0。覆盖上传、下载、工程、UI、JSON、脚本、插件解析及集合。排除项为 S11/S12 已独立复现的 legacy 下载还原点 IO 失败，本轮未修复或宣称通过。

```powershell
uv run --no-sync ruff check src tests scripts/convert_plugin_to_jsons.py scripts/plugin_eet_to_json.py
uv run --no-sync ruff format --check src tests scripts/convert_plugin_to_jsons.py scripts/plugin_eet_to_json.py
```

均退出码 0，2157 个 Python 文件格式通过。tracked 差异使用 `git -c core.whitespace=cr-at-eol diff --check -- <相关路径>` 检查通过；未跟踪文件也经 Ruff/格式与逐符号审查。源码/脚本/测试检索无已删除接口或旧模块断链。

聚焦证据（与最终回归重叠，不累加）：共享分类/导出/顺序/key 43 项；两个脚本及解析错误处理 24 项；旧入口单文件兼容 59 项；最后网关/普通适配器/新文件 API 153 项。第一次整合为 1124 passed，进一步删除失效网关方法及专用测试后，以最终 1112 为准。

## 审查发现及修复

- 独立审查复现两轮重名处理仍会被真实任务原名再次撞名，已改为持续消解，新增多轮真实名称影子回归。
- 独立审查复现旧 from_plugin 将加载失败变为空集合，脚本正常退出；已使用严格单次加载并加入最小 TES4 头检查，补充失败退出与无输出测试。
- 并行清理期间曾发生 __init__ 瞬态导入已删除模块、旧测试引用被删除 fixture 等收集失败，已同步入口并提取存活 fixture；局部测试改用 find_files 的三处路径装配错误已修正复验。最终回归无这些失败。

## 未验证与兼容边界

未执行真实云端写入/迁移、全库 pytest、线上性能测量或发布构建。terms/typed-client 网络合同未涉及本轮分类改动，未重复本机 HTTP 测试。最小插件头检查不代表完整二进制校验。旧云端文件保留，任务名更改导致的新路径不会自动继承旧文件评论/历史；没有自动删除或迁移行为。

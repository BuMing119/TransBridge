# 分阶段修复剩余旧流程与重复数据处理

- 日期：2026-10-26
- Epic / Story：task-result-lifecycle / S01–S05，第六轮
- 前序：[006 助手成果统一](2026-10-26-006-assistant-shared-results.md)
- 计划：[plan.md](../../../../plans/task-result-lifecycle/plan.md)
- 架构：[ADR-049](../../../adr/049-task-result-lifecycle.md)

## 结果

根据新一轮只读审计，按请求门禁、成果接入、投影成本、旧进度清理、历史读取、综合验证六阶段推进。实现阶段与独立审查分开，发现的问题继续在原责任边界修复。未将此前全部未提交差异归入本记录，未修改依赖和版本配置，未提交或发布。

## 本轮可归属变更

### 助手请求及配置

- `smart_assistant/request_scope.py`：请求外层仅比较工程/版本身份，不因全局内容 revision 增长拒绝读取已保存成果；旧请求回执中的 revision 不再构成永久门禁。固定选区及调用上下文仍保留。
- `smart_assistant/tools/types.py`、`_dictionary_application.py`：捕获来源指纹，词典不再依赖被请求作用域隐藏的可变 active_slot。
- `translation_settings.py`、`tool_translator.py`：配置查询所需两个 helper 归入 Settings，删除 Controller 中重复实现，修复真实配置工具调用错误。

### 下载、词典和导入成果

- 新增 `smart_assistant/tools/entry_task_results.py`：以明确的 download/import/dictionary/source 种类复用 TaskRecoveryStore，保存固定基线、原始候选、未映射项和保存失败内存材料，不伪造 AI ledger。
- `tool_paratranz.py`、`paratranz/service.py`、`application/ports/paratranz.py`：下载冻结目标、配置及偏好，统一分页迭代实现，后页失败不丢前页；默认只保存，显式合入不再联网。相同 key 按原文匹配，重复候选不任意选择，匹配使用索引避免逐候选扫描全部本地词条。
- `_dictionary_application.py`、`tool_parser.py`、`_json_import.py`、`_source_import.py`：词典及追加导入保存成果后逐条合入；部分解析成功保留合法条目、诊断、原始字节；完整 EntryKey 重复的 JSON 候选全部保留并隔离。`application/io/paratranz.py` 增加已读内容解析入口，消除 JSON 二次读取。
- 新增 `application/projects/prepared_source_results.py`；`projects/source_commands.py`、`gui_facade.py`、`persistence/project_provisioning.py`：正式来源准备与助手复用构造契约，准备和登记分离。结构成果保存原始快照和解析条目，恢复不再读取原路径。显式类型编码保留嵌套 SourceSnapshot、TranslationEntry、LocalizedStringRecord、bytes 与容器类型；本地化插件及 Strings 删除后仍可恢复，未使用 pickle 或动态类型加载。
- `_source_import.py`、`translation_task_results.py`：结构登记使用 attempting/failed/committed 状态，只有实际命令成功回执能证明完成；回执写入失败留内存补写。跨重启仅剩 attempting 时不自动重放、不冒认别的操作，也不复活用户已删除的来源。
- `translation_task_results.py`、`translation_task_merge.py`、`tool_translator.py`：已有 get_task_result/merge_task_result 同时处理非模型成果，source 种类分派到结构命令，其余复用公共逐条合入。
- `application/translation/task_recovery.py`：显式划分条目业务数据与配置字段；业务元数据中 token 等字段名不会被当作凭据而拒绝保存，真实配置检查仍保留。
- `application/translation/result_merge.py`、`projects/result_sources.py`、`projects/result_merge.py`：非模型成果捕获来源定位签名，真实定位变化挂起；可选字段不改变旧审阅状态的比较契约。应用结果保留候选原先捕获的定位契约，避免下载反向恢复误判变化；`ui/tools/ai_translator/task_merge_review.py` 完整解码保存的定位字段，保持证据摘要往返一致。

### 即时修改和投影

- `_project_tool_mutations.py`、`_entry_upload_associations.py`：复用 EntryProjectionUpdate，删除整份 to_dict 和重复候选集合；真实 fixture 验证单条编辑/关联只构建一个集合、未变条目对象复用。即时编辑比较实际目标基线，提交使用最新 CAS。
- `application/projects/variant_commands.py`：远端关联唯一性检查前移到命令提交前，只检查引用实际变化的来源 namespace；不同来源共享远端引用及无关来源旧状态不成为全局门禁。
- `tools/types.py`、`tool_editor.py`：标签显式捕获准备前基线，比较标签而非所有内容 revision；注册入口补集合，稳定来源身份保护排队期间的切换，不将集合重建本身视作失效。
- `undo_capture.py`、上述编辑/标签调用者：真实命令成功后撤销回执存储失败仍报告实际成功并给警告，继续前移上下文；提交前失败仍失败，不自动重放已成功命令。

### 旧进度及历史成本

- `ai_translator/translator.py`：删除 ProgressCheckpoint、每批旧文件双写、成功后的旧文件删除和旧恢复分支；删除仅供旧链的 checkpoint_identity、terminology_snapshot 参数。`project_terminology_runtime.py` 与 `ui/tools/ai_translator/source_execution.py` 同步调用契约。
- `ai_translator/post_processor/post_processor.py`：删除旧 checkpoint 执行参数及恢复/写入分支。删除 `post_processor/checkpoint.py` 和 `ui/tools/ai_translator/legacy_checkpoint.py`；没有为旧测试保留专用 codec。
- 当前 runtime-v2 翻译候选、ProofreadCheckpoint、应用层后处理 checkpoint 和现行候选适配继续保留。旧用户文件不创建、不改写、不删除；工程包附属数据仍由既有导入读取边界保留。
- `persistence/project_snapshots.py`：历史列表每文件读取一次，只验证展示元信息，不构建完整 Variant；恢复和删除仍完整验证。损坏元信息跳过并保留原文件，不阻断其他历史。仍会扫描现有历史目录，不宣称已实现无扫描索引。
- `application/sync/use_case.py`、`sync/__init__.py`：删除无生产调用的旧授权 DTO、确认、重新抓取与完整 hash 门禁，保留只读 create_plan；删除对应专属旧测试。
- 删除 `smart_assistant/tools/tool_v1.py` 空壳、`tool_proofreader.py` 重复计数 helper；计数测试改测现行后处理 runtime。

### 测试与文档

- 新增 `test_translation_settings.py`、`test_entry_result_lifecycle.py`、`test_immediate_entry_projection.py`、`test_translation_checkpoint_lifecycle.py`、`test_project_snapshot_listing.py`。
- 请求执行、词典、下载、来源、写回、撤销、Variant 命令、合入、AI 批次及后处理测试迁移到当前契约；保留有意义的来源保真、原文消歧、候选恢复及术语证据断言，删除纯旧执行 API 测试。`test_agent_tool_integration.py` 的 TestTaskManager 在每例前后 reset，修复存活单例任务污染后续控制测试。
- 更新 ADR-049、plan 和直接索引，修正文档 `ai_translator.md`、`post_processor.md`、`ARCHITECTURE.md`、`dev/INDEX.md` 中旧执行 API、参数及旧进度文件自动删除说明。
- 职责复核：通用条目成果与来源编解码分别提取为独立模块，未把这些职责塞入已超长的工具注册模块。`tool_paratranz.py` 仍为既有 880 行工具入口集合，本轮只替换其下载处理并复用独立成果模块；完整注册拆分涉及其他工具与注入契约，不混入此次局部修复。后续新增工具职责前应按服务族提取现有处理器并保持注册接口，不能继续扩展该模块。

## 验证证据

最终在现有 uv 环境运行，使用任务专用短 TEMP/TMP、UV_CACHE_DIR 和 pytest basetemp。Windows 沙箱禁止硬链接的测试经正常审批在同机离线执行；未联网调用模型或云端。

```powershell
uv run --no-sync --offline pytest tests/smart_assistant -q --basetemp=.tmp-fa2 -o cache_dir=.tmp-fc2 --tb=short
# 1092 passed

uv run --no-sync --offline pytest tests/ai_translator tests/application/projects tests/application/translation tests/contracts/translation tests/persistence/v2/test_project_snapshots.py tests/persistence/v2/test_project_snapshot_listing.py tests/ui/tools/test_ai_result_lifecycle.py tests/ui/tools/test_ai_result_publication.py tests/ui/tools/test_ai_result_recovery.py tests/ui/tools/test_ai_task_async_apply.py tests/ui/tools/test_ai_task_history_ui.py tests/ui/tools/test_translation_task_resume.py tests/ui/tools/test_proofread_resume_wiring.py tests/ui/operations/test_batch_download_results.py -q --basetemp=.tmp-fb2 -o cache_dir=.tmp-fc3 --tb=short
# 1485 passed

uv run --no-sync --offline pytest tests/persistence/v2/test_project_snapshot_listing.py tests/persistence/v2/test_project_snapshots.py tests/contracts/paratranz/test_sync_plan_confirmation.py tests/smart_assistant/tools/test_run_postprocess.py -q --basetemp=.tmp-s5t2 -o cache_dir=.tmp-s5c
# 50 passed；与上述矩阵有重叠，不重复累计

uv run --no-sync --offline ruff check src tests
uv run --no-sync --offline ruff format --check src tests
# 全部通过，2217 files already formatted

uv run --no-sync --offline pytest --collect-only -q -o cache_dir=.tmp-fcollect
# 9530 tests collected

git -c core.safecrlf=false diff --check
# 通过
```

最终两大矩阵目录互不重叠，共 2577 项通过。阶段及独立复审的 16、149、67、151、346 等计数与最终矩阵部分重叠，不累加。独立审查发现的部分解析丢失、重复 JSON 候选、来源删除后复活、撤销回执误报、标签切源、可选定位往返等均已补修并定向复验。

早期回归出现 Windows 临时目录/硬链接限制及 os.replace 的 WinError 5、Session 初始化间歇失败；相同相关用例最终矩阵已通过，没有为了通过而增加业务重试或删除测试。首次扩大回归的本地化来源 JSON 编码和 GUI 反向恢复失败已从根因修复。测试仍有既有弃用警告，不把它们视作性能测量。

## 边界与遗留

未执行全仓全部 9530 项、真实模型/ParaTranz 或用户桌面验收。未读取用户 `.transbridge/` 数据，未改手动历史的产品行为。仅内存保留且尚未保存的成果依赖进程存活；结构提交跨重启只剩尝试记录时，显示结果不确定而不自动重放。没有用全局内容校验替代逐条冲突处理。全部本轮任务临时目录及日志在完成记录后清理。

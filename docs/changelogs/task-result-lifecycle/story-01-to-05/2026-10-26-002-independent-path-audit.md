# 第二轮独立审查：补齐旧入口、减少重复处理、协调并发保存

- 日期：2026-10-26
- Epic / Story：task-result-lifecycle / S01–S05
- 前序：[001 固定输入、独立成果与局部合入](2026-10-26-001-fixed-inputs-independent-results.md)
- 计划：[plan.md](../../../../plans/task-result-lifecycle/plan.md)
- 证据：[第二轮 QA](../../../test-reports/task-result-lifecycle-audit2-2026-10-26.md)

## 目的与范围

用户要求新开 Agent 独立检查遗漏，重点是反复校验、重建快照和创建正式版本，并继续根因修复。三个新的 GPT-6.1 Sol high Agent 分别追踪 AI、上传下载、存储与退出，随后跨模块只读复核。基于前序记录后的未提交实现继续工作；本记录不重复认领前序全部改动，也不包含用户已有 pyproject.toml / uv.lock 版本号修改。

实际补齐了旧工程和显式下载入口，完整成果可离线重新处理，不再自动创建下载前正式历史；减少固定 AI 请求、合入证据、稀疏条目查找、保存验证和项目投影的重复工作。另修复旧工程下载与自动保存的交接竞争，等待自动排队，捕获最新内容后再写入，保留期间的新编辑。

## 生产代码变化

### 公共合入、投影与 V2 保存

- 修改 `application/projects/result_merge.py`：`_target_records` 利用 snapshot 的排序不变量定位稀疏目标，密集目标单次遍历；外部关联清理复用同一批记录。
- 修改 `application/projects/variant_commands.py`：两种空 patch 返回原 entries，避免 no-op 遍历，不绕过后续 guard、证据或 dirty 保存。
- 修改 `application/projects/gui_facade.py`：clean 保存不重新发布相同摘要的项目投影。
- 新增 `application/projections/project_entries.py`：按不可变条目对象身份复用上轮冻结行，缓存只保留当前版本；切换、关闭、删除与恢复有明确失效边界。
- 修改 `application/projections/adapters.py`：ProjectProjectionPublisher 持有并使用上述有界行投影器。
- 修改 `application/projections/models.py`：冻结操作幂等；冻结容器构造器也冻结嵌套内容，保持旧 snapshot 不可变。
- 修改 `application/projections/store.py`：`mark_persisted` 直接复用冻结 values，保留通知，不全量复制为可变值再冻结。
- 新增 `persistence/v2/validated_reads.py`：同一保存事务中复用实读 bytes 完全一致的验证结果，退出事务释放缓存。
- 修改 `persistence/v2/repository.py`：旧记录重复读取可复用验证，实际读盘、身份/revision/hash 比较与写后回读仍保留。
- 修改 `persistence/v2/project_save_journal.py`：保存事务启用窄缓存；日志与正式发布共用一次目标 DTO 构造，补偿和恢复协议不变。

### AI 固定输入与成果处理

- 修改 `ui/tools/ai_translator/task_merge_review.py`：从恢复 manifest 获取原任务目标，跨目标只读；异步加载期间切换不能采纳或更改另一版本的内容。
- 修改 `ui/tools/ai_translator/task_consistency.py`：真实冻结请求比较固定 envelope，不再逐次复制配置、递归序列化全部 EntryKey；可变兼容请求保留内容检查。
- 修改 `application/translation/task_merge_evidence.py`：成功回执复用同次已耐久的不可变候选/计划地址；新的提交前检查仍验盘，不同载荷和失败重试不误命中。

### 上传下载真实入口

- 修改 `ui/operations/facade.py`：有生产 launcher 时，旧工程和显式目标下载也走统一窗口。
- 修改 `ui/operations/production.py`：将显式参数传给统一下载 launcher。
- 修改 `ui/operations/batch_download_session.py`：适配旧工程与显式目标；完整缓存/恢复来源不重复请求权限验证，仅缺失来源需要网络。
- 修改 `ui/operations/batch_download_results.py`：旧工程保留原路径身份，V2 才构造 opaque ref；等待保存可取消，成功后的交付诊断进入回执。
- 修改 `ui/operations/categorized_upload_bridge.py`：旧工程普通/分类上传使用固定来源读取适配。
- 新增 `ui/operations/legacy_download.py`：旧工程轻目录、固定来源捕获、公共三方合入、后台 detached 保存与 Qt 交付；保留新编辑、标签与其他关联，实际成功后才确认保存。
- 新增 `ui/operations/reviewed_download.py`：兼容 feature 的工程下载复用独立成果及公共合入，保持已冻结内容。
- 修改 `ui/operations/production_support.py`：固定请求配置、完整来源条目及成果目录；目标检查只比较身份，不重新读取完整本地条目；None 区分旧调用未提供来源集合和真实空集合。
- 修改 `ui/operations/sync_preparation.py`：使用固定执行配置创建服务。
- 修改 `ui/operations/download_session.py`：复用相同目标的固定内容，下载完成立即在原目标目录保留独立成果。
- 修改 `ui/operations/paratranz_sync.py`：复核/提交复用固定输入与成果，移除 `_recovery_snapshot` 正式历史路径；没有工程存储的旧 headless 调用保持原执行兼容。
- 修改 `ui/operations/categorized_download_scope.py`：当前请求的配置与来源集合固定后复用，旧调用保留回退入口。
- 修改 `ui/operations/ordinary_upload_scope.py`：普通上传网关使用固定配置。

### 旧工程保存协调

- 修改 `persistence/variant_store.py`：同路径保存 reservation 跨越捕获、写盘与界面交付，短状态锁不阻塞 Qt；保存权竞争有专用异常供异步入口自动等待。
- 新增 `ui/shell/legacy_project_save.py`：Qt 固定 entries、labels、label library、项目元数据；后台写 detached store；完成时精确比较，期间新增编辑保持 dirty。
- 新增 `ui/shell/project_save.py`：保存队列自动合并、等待后捕获、原目标确认、失败与关闭收尾；acknowledge 后先释放保存权再调用 callbacks，活跃写入不会因关闭提前失去保护。
- 修改 `ui/main_window.py`：原异步保存职责提取并窄委派给 coordinator，关闭接入收尾；不继续扩充过大的窗口类。V2 原有保存行为由回归验证。

以上路径均相对 `src/transbridge/`。同路径协调只覆盖进程内旧格式保存，不冒充 V2 文件 CAS；原子写入、实际目标和原文保护继续存在。

## 测试与文档

新增回归：

- `tests/application/projects/test_result_merge_read_cost.py`：稀疏、密集、缺失目标和关联清理访问成本。
- `tests/application/projects/test_save_projection_cost.py`：clean 保存、dirty 状态通知、空 patch。
- `tests/application/projections/test_project_entry_reuse.py`：单条/批量、切换、关闭、删除恢复、不可变历史、保存状态确认。
- `tests/persistence/v2/test_save_validation_cost.py`：重复验证计数、外部同修订写入、事务边界与 DTO 构造次数。
- `tests/ui/tools/test_ai_result_identity_audit2.py`：原任务目标身份和加载期间切换。
- `tests/ui/tools/test_ai_fixed_request_cost_audit2.py`：10 万 key 检查不遍历/复制，固定 envelope 变化仍检测。
- `tests/application/translation/test_merge_evidence_cost_audit2.py`：回执不重建候选、损坏/不同载荷/写盘失败边界。
- `tests/ui/operations/test_legacy_download_lifecycle.py`：真实 AppContext、Windows 路径及中文版本、后台保存/Qt 交付、重开/取消/重试、无正式历史、固定成果目录与旧接口兼容，最终 14 项。
- `tests/ui/test_legacy_project_save_queue.py`：真实下载与自动保存交接、合并请求、期间编辑、目标切换、失败与关闭，最终 12 项。

修改旧回归以匹配固定输入合同及真实来源：`test_paratranz_download_authority.py`、`test_download_snapshot_session.py`、`test_paratranz_download_recovery.py`、`test_batch_download_production.py`、`test_paratranz_upload_roundtrip.py`、`test_paratranz_target_binding.py`、`test_sync_diagnostic_preflight.py`、`test_upload_mode_routing.py`，均在 `tests/ui/operations/`。保留真实目标变化和来源验证；最后一项修合法 mock 并让意外模态 warning 立即失败。`tests/ui/test_project_open_guards.py` 补齐绕过构造器的 fixture 所缺首轮退出字段，不弱化生产 guard。

更新 ADR-049、AI/ParaTranz 使用说明、计划和关联索引，新增本轮 QA；既有 001 增量及首轮 QA 历史结果保持原样。

## 验证证据

精确命令和分组范围见 [QA](../../../test-reports/task-result-lifecycle-audit2-2026-10-26.md)：

- 主会话后端联合：104 passed；AI/退出联合：118 passed；跨路径保存联合：49 passed。
- 最终完整下载 operations：`.venv/Scripts/python.exe -m pytest tests/ui/operations -q --basetemp=.tmp-audit-transfer-896f/final`，564 passed，43.95 秒。
- 保存协调的既有保存/后台/打开 guard 及新队列：48 passed；保存恢复、关闭、V2 hydration、工程创建：82 passed；补充 V2 完成目标 guard 单项通过。各组有重叠，不累加为不重复总数。
- `uv run --offline --no-sync --cache-dir .tmp-a2-root/uv ruff check src tests scripts/benchmark_variant_snapshot.py` 通过。
- 相同环境 `ruff format --check src tests scripts/benchmark_variant_snapshot.py` 通过，2251 文件；`git diff --check` 通过。

性能计数与五轮局部测量：1,000 条中编辑 1 条的投影序列化从 1,000 次到 1 次；单文件保存完整验证从 5 次到 3 次，实际读取仍为 4 次；双文件保存 DTO 构造 2 次到 1 次；1 万条中稀疏合入 1 条的目标记录访问从两次全扫描到约 14 条；固定 AI 请求和同次回执不再重复序列化完整输入。数值、范围与测量边界见 QA，不代表整套软件加速比例。

## 遗留边界

本轮确认的生产问题已修复，相关最终回归通过。失败来源重试仍重新读取该来源完整集合，已下载 partial 内容独立保留可导出；本轮未扩展逐文件跨重试恢复 schema。实际完整文件保存、源数据筛选及界面安装仍可能全量遍历。未调用真实云端/LLM，未操作用户数据、打包、提交或推送；首轮其他扩大矩阵限制保留在原 QA 中。

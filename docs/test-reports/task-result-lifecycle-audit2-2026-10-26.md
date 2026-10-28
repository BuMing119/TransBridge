# 固定输入与独立成果：第二轮独立审查

日期：2026-10-26。对应 [计划](../../plans/task-result-lifecycle/plan.md) 与 [ADR-049](../adr/049-task-result-lifecycle.md)。本报告只记录用户要求的第二轮全新 Agent 审查与补修；[首轮 QA](task-result-lifecycle-qa-2026-10-26.md) 保留原始验证结果和限制。

## 审查方式与范围

新建三个 GPT-6.1 Sol high 子 Agent，分别追踪 AI、上传下载、保存和退出的实际生产调用链。主会话检查公共合入服务、整合接口和性能证据。工作区基于首轮尚未提交的实现继续修改，不把用户已有版本号改动归入本次工作，不读取 `.transbridge/` 用户数据。

审查区分固定输入检查、覆盖冲突处理、实际持久化保护和不可达兼容 API；不以搜索到 `snapshot` 就认定运行时有问题，也不通过删除原子发布或身份保护来降低检查次数。

## 已确认并修复的根因

- AI 历史成果审阅从当前界面取目标身份，可能把原版本成果应用到新切换的版本。改为使用恢复 manifest 的目标身份；不在原版本时仍可查看成果，禁止写入其他版本，异步加载期间切换也受保护。
- `TaskConsistency` 每次检查真实冻结请求都复制配置并序列化所有 EntryKey。改为比较固定请求 envelope；可变兼容请求仍按内容检查。
- `TaskMergeEvidenceStore.acknowledge` 再次保存同一个已耐久的候选和合入计划。实际回执复用同次不可变证据地址；新的提交前检查仍验证文件，失败和不同载荷不能误用缓存。
- 公共项目合入只处理少量条目时，仍遍历整份版本建立 current 和外部引用索引。利用 immutable snapshot 的已排序 key 查找稀疏目标；大批量继续单次遍历，清理关联复用同一批记录。
- clean 保存仍强制重建项目投影。仅当成功保存后的摘要确实变化时发布新投影；dirty 保存后的清洁状态通知保留。
- 同一保存事务重复解析、验证字节完全相同的旧记录。事务内复用已验证对象，每次仍读取文件并要求原始字节一致；缓存不跨事务或线程，新写入内容仍独立回读验证。
- 项目和版本同时保存时，为日志与发布分别构造相同完整 DTO。改为共用一次构造结果。
- 空状态 patch 仍遍历全部版本条目。两种 patch 入口在空输入时返回原条目，后续身份保护、提交仲裁和 dirty 保存照常执行。
- 手动修改一条词条时，项目投影重新序列化和冻结所有未变条目。投影仅缓存上一份 immutable snapshot 的条目对象和冻结行；相同对象直接复用，变更行重新生成，目标切换与关闭清空缓存。保存状态确认直接复用冻结值，避免先复制为可变字典再冻结；正常界面通知保留。
- 旧工程或显式指定远端目标的下载入口仍绕过新成果流程，旧同步准备/提交反复读取本地内容并生成完整还原历史。真实 UI 统一进入批量下载 session；兼容 feature 的工程下载也保留固定输入和独立成果，再使用公共三方合入。成果目录在开始时固定，完成后切换项目不能把旧成果写到新项目目录。
- 完整成果重新打开后，真实主按钮仍经过远端权限请求。改为只为尚未保存完整内容的来源执行网络核查，已有完整成果复用已保存内容。
- 旧工程来源目录为取得数量和名称而复制所有词条。目录改为轻量计数，实际任务输入捕获时才复制。
- 兼容同步请求新增固定来源集合时，空 tuple 默认值不能区分“旧调用者未提供”和“确实为空”。改用 None 表示未提供并保持旧回退入口；实际捕获为空仍明确拒绝，不把两种状态混淆。

旧工程适配由交叉审查补齐真实 `AppContext` 契约：项目身份可能是 Windows 路径、版本名可能含中文，不强制构造 V2 opaque ID；dirty 由 store 与信号维护，不给只读属性赋值。磁盘 I/O 在后台，当前内容捕获与最终界面交付在 Qt；期间更新的词条/标签保留为 dirty。界面交付失败不能把已经写盘的结果误报成没有保存，诊断进入最终回执。

旧工程下载与普通自动保存原本没有覆盖“捕获 → 写盘 → Qt 交付”的共同协调，另一次保存可能在下载写盘后、界面更新前写入旧值。`VariantStore` 增加同路径保存 reservation，GUI 保存职责从 MainWindow 提取为 coordinator。竞争时 GUI 请求自动排队合并，下载在后台可取消地等待；轮到时才捕获最新输入。期间的新编辑保留 dirty，自动保存继续安排；关闭、失败、身份切换与回调都释放或保留正确的写入归属，不把短时竞争作为新报错门禁。该协调针对进程内旧工程保存，不冒充 V2 的磁盘 CAS。

## 性能证据

以下均使用合成数据，不能外推为整套软件或真实云端任务的加速比例。五轮耗时为局部测量；回归测试另外断言处理次数。

### 固定 AI 请求检查

10 万固定 key、10 万字符 prompt，五次 `require_current`：旧中位 36.541 ms，范围 34.345–46.839 ms；新中位 0.005 ms，范围 0.004–0.011 ms。五次检查的全 key 遍历和配置复制分别从 5 次降为 0 次。

### 合入证据与实际回执

1 万候选及冲突：旧回执路径额外重复保存 evidence，中位 861.352 ms，范围 714.178–939.362 ms；新回执写入中位 69.673 ms，范围 63.773–70.969 ms，包含本地合成文件的原子写入和 fsync。每次真正提交前仍完整验证该次证据。

### 稀疏合入的目标记录读取

1 万条版本中合入 1 条，带关联清理，每轮重复 100 次、五轮。来源读取及提交使用测试端口，测量仅包括合入规划和目标查找，不含磁盘保存或完整界面更新。

- 修改前中位 1.093334 秒，范围 0.741917–1.133222 秒；每轮访问 2,000,000 条记录。
- 修改后中位 0.007483 秒，范围 0.005030–0.007620 秒；每轮访问 1,400 条记录，即每次 14 条。
- 另测 10,000 条中合入 2,000 条，仍只遍历一次整份记录，避免密集合入退化成逐条二分查找。

### 单个版本文件保存

1,000 条词条、MemoryFilesystem、五组交错测量，同一代码仅关闭或开启事务内验证复用：关闭中位 1.1065 秒，范围 0.9306–1.2125；开启中位 0.6926 秒，范围 0.5625–0.7468。每轮完整验证从 5 次降到 3 次；实际文件读取保持 4 次，原子替换保持 1 次。另有独立回归验证双文件事务的版本 DTO 构造从 2 次降为 1 次；该变化不计入上述单文件耗时结论。

### 单条编辑的项目投影

1,000 条中修改 1 条，五组交错测量 application command 与 projection，不含 Qt 安装/绘制。禁用行复用中位 28.018 ms，范围 22.312–79.659 ms；开启中位 2.960 ms，范围 2.536–4.573 ms。每轮 `VariantEntryState.to_dict` 从 1,000 次降为 1 次。缓存只保留当前 snapshot 和当前 key 映射，仍需遍历条目身份，不能称整个编辑过程为 O(1)。

## 已完成的聚焦验证

- AI 生命周期、部分成功、异步应用、重试与恢复组合：125 passed。
- 回执优化后的相关回归：73 passed；全部 14 项新增 AI 测试最终复验通过。
- 公共三方合入、项目合入和目标访问成本组合：37 passed。
- 最终后端联合：104 passed，覆盖全部 projection 测试、空 patch、clean 保存、稀疏合入、durable 保存、双文件日志补偿和证据回执。另一个 Agent 只读交叉审查目标二分查找和事务验证缓存，120 组混合身份样例与完整扫描一致；7 项外部同修订写入、补偿和写后失败回归通过。
- 最终 AI 与退出联合：118 passed，包含跨目标成果处理、固定请求成本、真实生命周期、session/异步应用、取消退出与保存恢复。
- 最终跨路径保存联合：49 passed，包含真实旧工程下载、Qt 保存队列、成果重开、AI frozen-save 和旧版本文件兼容。覆盖下载写盘后暂停 Qt 交付、两次自动保存等待且不提前捕获、最终写入最新内容；保留保存期间的新词条/标签修改、目标切换、写入失败、关闭时不提前释放写入归属。
- 存储与投影 Agent 的 UI 投影、手动编辑、退出、恢复、hydration 与 durable 保存组合：140 passed。各组与上述用例有重叠，不累加成不重复用例总数。
- 保存协调提取后的既有保存/后台/打开守卫及新队列组合：48 passed；保存恢复、关闭、V2 hydration、工程创建扩大回归：82 passed；最后新增 V2 完成时目标切换 guard：1 passed，新队列文件合计 12 项通过。
- 最终完整 `tests/ui/operations` 单进程：**564 passed**，43.95 秒，exit 0；新增 legacy 文件最终共 14 项通过，包含等待另一次保存、排队期间取消和 None/空输入兼容。

相关命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/ui/tools/test_ai_result_identity_audit2.py tests/ui/tools/test_ai_fixed_request_cost_audit2.py tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_ai_result_lifecycle.py tests/ui/tools/test_ai_partial_task.py tests/ui/tools/test_ai_task_async_apply.py tests/ui/tools/test_ai_task_session.py tests/ui/tools/test_recovery_retry_results.py tests/ui/tools/test_ai_result_recovery.py -q --basetemp=.tmp-audit2-ai-regression
.venv/Scripts/python.exe -m pytest tests/application/translation/test_merge_evidence_cost_audit2.py tests/ui/tools/test_ai_result_lifecycle.py tests/ui/tools/test_ai_task_session.py tests/ui/tools/test_ai_task_async_apply.py -q --disable-warnings --basetemp=.tmp-audit2-ai-receipt-green
uv run --offline --no-sync --cache-dir .tmp-a2-root/uv pytest tests/application/projects/test_result_merge_read_cost.py tests/application/projects/test_project_result_merge.py tests/application/translation/test_result_merge.py -q -p no:cacheprovider --basetemp=.tmp-a2-root/read-green --tb=short
uv run --offline --no-sync --cache-dir .tmp-a2-root/uv pytest tests/application/projections tests/application/projects/test_save_projection_cost.py tests/application/projects/test_result_merge_read_cost.py tests/application/projects/test_project_result_merge.py tests/application/projects/test_durable_variant.py tests/application/projects/test_gui_facade_ai_snapshots.py tests/persistence/v2/test_save_validation_cost.py tests/persistence/v2/test_lifecycle_save_transactions.py tests/application/translation/test_result_merge.py tests/application/translation/test_merge_evidence_cost_audit2.py -q -p no:cacheprovider --basetemp=.tmp-a2-root/backend --tb=short --show-capture=no
uv run --offline --no-sync --cache-dir .tmp-a2-root/uv pytest tests/ui/tools/test_ai_result_identity_audit2.py tests/ui/tools/test_ai_fixed_request_cost_audit2.py tests/ui/tools/test_ai_task_consistency.py tests/ui/tools/test_ai_result_lifecycle.py tests/ui/tools/test_ai_task_session.py tests/ui/tools/test_ai_task_async_apply.py tests/ui/test_cancel_application_close.py tests/ui/test_save_recovery.py -q -p no:cacheprovider --basetemp=.tmp-a2-root/ai --tb=short --show-capture=no
uv run --offline --no-sync --cache-dir .tmp-a2-root/uv pytest tests/ui/test_legacy_project_save_queue.py tests/ui/operations/test_legacy_download_lifecycle.py tests/ui/operations/test_batch_download_results.py tests/ui/tools/test_ai_frozen_save_boundary.py tests/persistence/v2/test_variant_store_compatibility.py -q -p no:cacheprovider --basetemp=.tmp-a2-root/legacy --tb=short --show-capture=no --disable-warnings
.venv/Scripts/python.exe -m pytest tests/ui/operations -q --basetemp=.tmp-audit-transfer-896f/final
```

扩大 operations 首次有 9 项失败并在末尾挂起，未将该次写成通过。5 项旧断言迁为固定输入不因无关 revision/config/local 编辑失效，仍拒绝真正目标变化；3 项反馈 fixture 改为合法当前 schema、registry 和真实来源读取端口；unified service 独立 33 项通过。末尾挂起精确定位为 upload-mode fixture 缺 authoritative 标志，触发真实先决条件模态框；修合法 fixture 并令意外 warning 立即失败，该文件 15 项通过。随后才取得上述 564 项完整单进程通过，不再以“可能 Qt 环境问题”解释本轮这一挂起。

`test_project_open_guards` 通过 `__new__` 绕过构造器，漏初始化首轮新增的退出 attempt/progress 字段；本轮补齐 fixture，未降低生产退出保护。此前未得到完整通过的首轮其他扩大矩阵仍保留于首轮报告，不以本轮专项结果覆盖其历史记录。

## 边界

AI 进度与 source-finished 事件不写完整任务记录；记录在阶段转换发生，并已有 pending 合并和相同结果免写。旧 `version_snapshot.py` API 仅被未接入真实窗口的 `versioned_run.py` 引用，保留直接导入兼容，不认定为真实窗口的重复历史创建。

完整下载来源和已完整保存的成果可复用，失败来源重试仍重新读取该来源的完整文件集合；此前成功读取的部分文件已经独立保留，可离线查看或另存。本轮没有增加跨重试的逐文件恢复清单，不宣称不完整来源中的每个文件都只请求一次。当前内容实际保存、源读模型筛选和界面安装也可能仍遍历完整数据；上述优化不等于所有处理都成为常数时间。

本地测试不调用真实云端或 LLM，不打包、提交或推送。沙箱 `os.link` 权限失败的 AI 用例在获准提升后取得通过证据；未修改生产持久化保护来迎合环境。

最终静态检查：`uv run --offline --no-sync --cache-dir .tmp-a2-root/uv ruff check src tests scripts/benchmark_variant_snapshot.py` 通过；相同环境 `ruff format --check src tests scripts/benchmark_variant_snapshot.py` 通过，2251 个文件；`git diff --check` 通过。首次最终 lint 发现新增 legacy 测试的导入排序，修复后重新检查通过。

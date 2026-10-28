# 工程统一恢复已保存的来源成果

- 日期：2026-10-26 至 2026-10-27
- Epic / Story：task-result-lifecycle / S01–S05，第九轮
- 依据：用户反馈“老滚5”大型工程启动仍慢，授权 bm-pilot 统一打开与切换流程；Astra high 规划、Sol 6.1 high 实现与独立审查。
- 计划：[plan](../../../../plans/task-result-lifecycle/plan.md)；契约：[ADR-049](../../../adr/049-task-result-lifecycle.md)，局部修订 [ADR-044](../../../adr/044-source-file-update.md)。

## 行为与范围

来源导入、添加及显式更新时，保存完整来源输入及已解析基线。启动恢复、打开和切换共用持久成果，不再每次读取、散列、解析外部插件。后续人工编辑仍只属于 Variant；来源成果不是正式版本或历史快照，不按每条编辑增长。外部源文件变化或删除不改变工程保存的原文；通过更新来源接受新内容。

现有缺少引用的工程首次正常打开仍需解析，并建立成果；成功激活时才通过既有 CAS 事务登记引用。准备失败不改变正式工程，不增加版本或历史。损坏成果保留译文和只读恢复视图，不静默换成后来被修改的外部输入。不可变文件先于引用发布，失败可能留下未引用内容；本轮不自动回收可能由历史备份引用的内容。

当前 `.transbridge` 包携带成果和完整输入依赖，旧包在读取入口升级；用户不需要选择新旧格式。BSA 本地化输入保留所选 member 字节并生成可移植散装 companion。上传、结果合入及输出也消费同一保存来源；目标覆盖仍保留原子写入及真实目标冲突保护。

## 可归属的修改

以下限定本轮新增或进一步修改，不将工作区其他既有变更归入本轮。

1. 新增 `persistence/source_artifact_store.py`：不可变 manifest 与内容 blob，SHA-256 引用、实际字节校验、完整依赖闭包、先内容后 manifest 的原子发布；包内纯读取验证与导入使用相同存储契约。小二进制记录聚合为有边界验证的切片，避免大量小文件；读取不共享可变解码对象。实测后将恢复的实际引用收集融合到解码，返回前精确比对声明闭包，不额外全树扫描；digest 按唯一值检查，切片边界仍逐项校验，未声明引用不构造读取路径。独立导出仍校验完整闭包。
2. 新增 `persistence/source_artifact_codec.py`：明确类型白名单和 ABI，保存 PreparedProjectSource、baseline、hydration、词条上下文及 SourceSnapshot 全部输入；不使用 pickle。显式重定位来源描述、hydration 和本地化路径，内容身份保持不变。实测后改为单次 tag 分派及预计算字段集合，消除节点重复集合构造；wire 和 ABI 不变，完整 baseline-only 合并状态保留。
3. 新增 `persistence/saved_project_sources.py`：单一持久 preparer；真实准备后持久化，已登记来源按成果读取并核对格式、选项、namespace、fingerprint。完整成果缺失或损坏明确诊断，禁止回退实时文件。
4. `application/projects/source_registry.py`、`persistence/v2/schema.py`：登记 `source_artifact_id` 和真实折叠标记 `folded_into_baseline`。`source_content.py` 区分普通翻译关系与已折叠来源，独立 migration 来源不能被关联关系误吞。
5. `bootstrap/persistence.py`：同一 store/preparer 注入创建、加源、更新、打开、上传及工程包服务。`source_commands.py` 对 AI 已准备来源登记最终成果；普通内部加源只持久化一次，外部传入的已准备对象始终捕获最终内容，不信任旧成果 ID。`provisioning_sources.py` 仅对真正发生译文折叠的 primary 再持久最终基线，不重复编码和读取所有未变来源；`source_update.py` 从已保存成果取得旧基线，保留 migration 实际身份并补真实折叠标记。
6. `persistence/current_project.py`：恢复权限先核验，优先读取成果，收集首次解析后的正式绑定。`prepared_project_read.py` 将绑定固定为私有 bytes，只对 provenance/external_refs 的可变元数据子树保存证据，激活复用前确认；不重新构建或序列化整份版本。`project_lifecycle_loader.py` 在原记录身份核验后更新候选绑定，沿用既有激活 CAS 发布。
7. 新增 `persistence/source_artifact_archive.py`：由捕获的内容形成可移植输入、收集成果闭包，独立验证包内成果及散装输入一致性，拒绝未引用成员。`project_archive.py`、`project_archive_files.py` 接入包导出、验证和发布；不借本地已有缓存掩盖包内缺失。`legacy_project_archive.py` 为旧包登记生成成果和实际解析 namespace，避免首次重开丢失来源。
8. `application/projects/source_hydration_cache.py`：已安装读模型冷读也从成果恢复，缓存键包含 artifact ID，避免插件摘要相同但 STRINGS 已更新仍返回旧原文。`categorized_upload.py`、`result_sources.py` 传入正式来源登记；显式更新仍读取实际新输入。
9. 新增 `tests/persistence/test_source_artifact_store.py`、`tests/persistence/v2/test_saved_project_sources.py`、`test_source_artifact_archive.py`、`tests/application/projects/test_saved_source_consumers.py`：验证进程重建/切换零实时解析、旧引用补齐 CAS、损坏隔离、嵌套证据篡改、来源消失后包往返及实际插件写出、BSA/散装依赖、同摘要 STRINGS 更新和下游冷读。
10. 更新 `test_project_open_reuse.py`、来源登记/命令/创建/更新相关测试：采用正式成果登记及真实折叠契约。`test_project_archive.py` 允许失败发布仅留下合法未引用不可变成果，保持原文件不变检查；外部变化后导出保存内容。`test_categorized_upload.py` 使用真实 bootstrap 服务；`test_batch_download_sources.py` 用实际成果损坏/缺失保留部分成功覆盖；`test_file_source_performance.py` 验证零实时 hash/parse；`test_project_result_merge.py` 保留 CAS 重试和并发编辑保护，移除旧实时散列次数要求。
11. 更新 ADR-049/044、当前 plan 及直接索引，记录跨重启成果契约和明确迁移边界。未修改依赖、构建配置或用户既有版本号；没有提交或推送。
12. `scripts/benchmark_project_open.py` 及对应测试：隔离副本按正式引用复制并核验完整成果闭包，已保存来源不再检查实时路径；缺失或损坏依赖在创建副本前失败。旧无引用工程仍按原行为追踪外部输入摘要。此工具接线不改变本轮冻结基准 harness。
13. 新增 `persistence/v2/schema_execution.py`，`schema.py` 只接入执行作用域：针对实测大量 `descend/evolve`，单次调用复用 canonical 子 schema 的校验器，仍对每个输入执行现有 JSON Schema handler。配置、错误路径、全局语义与类型行为保持；仅明确本地 vocabulary 可优化，未知关键字、作用域引用、显式 resolver、布尔节点保留标准执行。嵌套/异常/线程隔离，不跨调用、不缓存输入校验结果、不新增依赖或第二套业务规则。

职责复核：成果存储、typed codec、包接线、准备证据及 schema 执行分别为独立模块；schema 声明仍集中原文件，不向工程打开入口加入编解码实现。新职责未继续扩张大控制器。

## 验证

Windows、Python 3.12.12，仓库现有 uv 环境；Qt 使用 offscreen，无网络、LLM 或云端写入。测试使用任务专用 UV_CACHE_DIR、TEMP/TMP 和 basetemp。

```powershell
uv run --no-sync --offline pytest tests/persistence/v2 tests/persistence/test_source_artifact_store.py tests/application/projects tests/application/projections tests/integration/bootstrap/test_persistence_v2_projection_composition.py -q --basetemp=.tmp-do-final -p no:cacheprovider --tb=short
# 712 passed，68.58 秒

uv run --no-sync --offline pytest tests/persistence/v2/test_task_recovery_archive.py tests/smart_assistant/tools/test_source_import_authority.py tests/smart_assistant/tools/test_upload_result_lifecycle.py tests/smart_assistant/tools/test_entry_result_lifecycle.py tests/ui/test_project_open_operation.py tests/ui/test_project_open_guards.py tests/ui/test_projection_check_during_open.py tests/scripts/test_benchmark_project_open.py -q --basetemp=.tmp-durable-open-root/ui-ai -p no:cacheprovider --tb=short
# 88 passed

uv run --no-sync --offline ruff check src tests
uv run --no-sync --offline ruff format --check src tests
git -c core.safecrlf=false diff --check
# 通过；含 benchmark 脚本的最后一次检查为 2249 files already formatted
```

早期后端矩阵 704 passed、7 failed：六项依赖实时文件失败/解析次数的旧断言已按新契约更新，限定 34 项复验通过；另一项 Windows 沙盒不允许测试临时文件原子 link，使用隔离目录解除沙盒限制后通过，未加入业务回退。各次矩阵有重叠，不累计总数。未执行全套测试、真实云端或完整桌面人工验收。

后续重复持久化修复：新增 `tests/application/projects/test_source_artifact_publication_cost.py` 7 项，连既有 source_commands/provisioning 共 33 项通过；验证普通 N 来源建项 N 次 put、普通加源一次、折叠和初始译文保存最终值、外部 metadata 修改不能借旧 ID 绕过捕获及 CAS 失败保护。benchmark 工具配套 21 项通过，覆盖成果闭包复制、缺源重开、缺损依赖失败及原始输入摘要不变。

成果解码优化：48 项 store 测试及 23 项来源恢复/归档/发布测试通过；独立审查对冻结前后做 1073 个节点差分，decode 与 closure 结果一致，24 项安全子集通过。校验器优化：新增 `test_schema_execution.py` 最终 40 项，连 variant_validation/repository 共 79 项通过；独立审查另做 6912 次完整错误结构差分及 120 次并发调用，嵌套异常、不同 format checker、显式 resolver、未知 keyword 和下一次 handler 替换均保持等义。以上均不累计进总数。

最终联合命令将第一组后端目录、上述 AI/UI/script 文件合为一次运行（`--basetemp=.tmp-do-complete -p no:cacheprovider --tb=short`）：872 项中 871 passed，1 项 `test_authoritative_baseline_is_required_before_active_changes` 在 Windows 原子替换测试临时文件时出现 WinError 5。相同 production 代码下单独运行完整 `test_persistence_v2_projection_composition.py` 9 项全部通过，未加入业务重试或修改保护；故不能把联合命令描述为一次全绿。该联合收集时 schema_execution 为 39 项，之后追加的第 40 项已由实现与独立审查各自全组复验通过。

独立 Agent 审查发现并复验关闭：译文折叠后保存最终基线、真实折叠与独立 migration 的区分、更新后缺源重开及导出、下游冷读仍访问外部文件、同插件摘要的 STRINGS 缓存失效、旧包引用/namespace 丢失、可变嵌套元数据污染准备证据。独立小 probe 均通过，最终未发现剩余可达缺陷；不将静态审查替代完整运行验证。

## 实际工程性能与限制

真实命名为“老滚5”的工程隔离副本：80 个 plugin.sse 来源、96,306 条词条；project/variant 共 72,075,820 bytes，源文件共 379,223,853 bytes。Windows、Python 3.12.12、Qt 6.10.0、PyQt 6.10.2，Qt offscreen。计时入口为现有 ProjectCoordinator + AppContext + Step2PreviewWidget，不包括完整 MainWindow 的全部订阅；选中来源表格为 377 行，工程准备与激活处理全部词条和来源，不能把表格行数当作工程规模。

性能 Agent 先固定修改前全套源码及同一 seed，之后固定最终源码；首次建立成果使用全新 seed，重启测量使用新 Python 进程和已经生成成果的同一隔离 root，再连续 reload 5 次。测量期间没有并行重型 QA。DEBUG 日志仍执行 DiagnosticLogFormatter 的完整格式化/脱敏，只将条数和字符数落盘，避免保存用户翻译文本；实际日志磁盘字节数与桌面版本不同。最终源码与工作区 1232 个非 pycache 文件摘要一致，82 个原始文件摘要全部未变；原项目、源文件和工程包没有写入。

实际命令使用任务独占临时 wrapper，复用固定的 `scripts/benchmark_project_open.py::measure_project`，每轮立即写入 JSONL：

```powershell
uv run --offline --no-sync python .tmp-durable-open-perf/run.py after-final --source-root .tmp-durable-open-perf/after-final-source --data-root .tmp-durable-open-perf/after-firstbuild/data --repeats 5
uv run --offline --no-sync python .tmp-durable-open-perf/run.py final-firstbuild --source-root .tmp-durable-open-perf/after-final-source --repeats 0
```

wrapper 仅选择该工程、复制 project/variant、隔离 APPDATA/LOCALAPPDATA，按固定 harness 构建服务；新进程显式复用上轮隔离 root。源码副本、输入副本、原始私有摘要清单及 wrapper 均属任务临时文件，清理后可按以上相同步骤与现有脚本复测，不将用户数据加入仓库。

最终结果（秒；中位数及范围只针对 5 次 reload）：

- **第一次建立成果**：完整 155.904，准备 127.467，激活 24.699，GUI 安装 3.656。这是一次额外解析/持久化成本，不是日常重启耗时。新增成果共 234 个文件、669,392,237 bytes，其中 80 个 manifest 共 266,728,187 bytes；内容多版本共用，不按人工词条编辑生成。
- **新进程重启**：完整 86.157，准备 58.155，激活 24.346，GUI 安装 3.574。
- **5 次重开完整耗时**：92.213、90.871、90.610、93.994、92.169；中位 **92.169**，范围 90.610–93.994。
- **5 次重开准备阶段**：58.453、58.218、57.289、59.837、59.055；中位 **58.453**，范围 57.289–59.837。
- 激活中位 27.873，范围 26.738–28.296；GUI 安装中位 5.685，范围 5.395–5.834；最大事件循环间隔中位 5.755，范围 5.429–5.872。各内部计时可能包含子阶段，不能全部相加。
- 每轮 CPU 时间中位 87.547 秒，约占完整 wall time 的 95%；RSS 端点中位 2.557 GB，范围 2.403–2.597 GB；这些是端点采样，不是峰值内存。日志最终仅 3 条/332 字符、格式化约 0.00019 秒，当前瓶颈主要是 CPU 数据处理。

修改前 5 份有效 reload **阶段**样本的准备中位 104.762 秒，范围 66.092–116.025；最终准备中位下降约 **44.2%**。修改前激活中位 23.146 秒，范围 8.988–23.722；最终激活阶段没有改善，反而较该组中位更高。修改前 GUI 安装中位 7.211 秒，范围 2.974–8.128；不能据单个阶段宣称整体等比例提速。

基线限制：原 before 进程计时后报告峰值内存时发生 API 故障，仅保留 4 个 reload 的阶段计时；另一个新进程补跑 cold+1reload 得到第 5 份阶段样本。因此 before 是混合进程的阶段统计，不能用 between-reset 时间（包含 GC/准备）充当精确总耗时。补跑取得精确 cold 109.892 秒、reload 133.337 秒，但只有单个完整样本，不能与最终 5 次中位计算稳定“整体提速百分比”。之后移除不安全的 Windows 自定义内存 API，采用逐轮落盘和现有 psutil 端点统计。

中间版本仅增加持久成果时，重启仍约 104.970 秒；这触发后续根因补修。cProfile 定位到 827 万次 typed decode、1262 万次独立闭包遍历及约 135 万次 schema descend/evolve。先融合闭包与解码，再复用 schema executor；均保留等价性回归。profile 会放大 Python 调用成本，仅用于定位，不与非 profile 的 wall time 比较。

## 遗留边界

**大型工程打开缓慢尚未彻底解决**：当前完整基准仍约 92 秒，事件循环仍有约 6 秒间隔，不能宣称秒开或完全流畅。已完成本轮统一恢复与持久成果契约，但全量 typed 数据还原、Variant 冻结投影及工作台集合构造仍有成本；大型 Variant 继续使用现行 JSON 格式。首次建立成果也增加一次处理及磁盘空间成本。

独立架构复核确认，按需恢复不能以删除非当前 slot 或置空 collection 的尾部补丁实现：现有来源选择同步执行；ProjectionConsistency 将 authority 中缺少 collection 的词条判为偏离；桌面 AI 的 TaskSourcesView/SelectionCapture/SessionCapture、批量写出及 apply-all 迁移依赖完整 slots。必须先统一“注册来源 / 已物化视图”的契约，再接来源加载 worker、任务按范围捕获和来源级恢复状态，否则会缩小 AI/批量任务范围、误判保存或制造新的校验拒绝。延迟 hydration/blob 检查还会改变损坏来源的发现时机，不能静默跳过失败来源。这是后续独立实现切片，本轮没有以不完整视图冒充完成优化。

完整桌面启动、峰值内存、打包发行尚未验收；未执行全套测试。没有修改用户原始数据。主会话的 `.tmp-durable-open-root`、`.tmp-do-qa`、`.tmp-do-final`、`.tmp-do-complete`、`.tmp-do-retry` 与性能 Agent 的 `.tmp-durable-open-perf` 已逐一核对绝对路径及 reparse 边界后删除，并确认不存在；实现/审查 Agent 各自测试临时目录也已清理。清理后原始 82 个文件摘要再次确认不变，其他预先存在的临时目录未处理。

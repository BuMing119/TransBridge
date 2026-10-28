# S01：公共成果合入与恢复契约

- 所属计划：[固定任务输入、独立成果与局部合入](../plan.md)，S01 公共合入与设置。
- 架构依据：[ADR-049](../../../docs/adr/049-task-result-lifecycle.md)。
- 状态：设计草稿；2026-10-26 已按用户目标实现并完成本地相关验证，见 [QA](../../../docs/test-reports/task-result-lifecycle-qa-2026-10-26.md)。未代替用户桌面验收。
- 本文展开现有 S01，不扩大产品范围。主会话负责计划链接、实现和最终状态确认。

## 目标与原始验收标准

计划原文步骤：实现三方真值表与结构变化挂起；读取最新目标数据形成局部 patch；保留全冲突证据和实际应用回执；配置 AI/download 的三种偏好并在启动冻结。

计划原文验收：无关编辑不影响合入；incoming==base 不覆盖新编辑；重复应用 no-op；原文变化/删除不受 prefer incoming 强制覆盖；相关字段保真；设置取消不写盘、错误不谎报成功。

S02 AI 生命周期、S03 下载依赖本协议。S04 提供 no-op 保存及快照复用能力；S05 负责跨消费者集成验收。成果保存、工作台写入、当前项目保存和回执保存是不同状态，不能用单一“成功”布尔值代替。

## 现有接口与职责

- `application/translation/result_merge.py`：`ResultEntryState`、`ResultEntryChange`、`ResultMergeConflict`、`ResultMergePlan` 和 `merge_result_entries`。纯函数不访问 Qt、文件或配置。
- `application/projects/result_merge.py`：`ProjectResultMergeService.merge` 读取权威 Variant 和源文本，调用纯函数，通过现有命令提交稀疏 patch，最多三次 CAS 重试。
- `application/projects/gui_facade.py`：`merge_entry_states` 委派公共服务；`replace_entry_states` / `replace_entry_records` 保持原命令边界。
- `application/projects/result_sources.py`：`read_result_source_texts` 只收集成果目标所属来源的文本；`_InstalledBaselineIdentity` 提供保有基线强引用的身份键。
- `application/projects/source_hydration_cache.py`：`SourceHydrationCache.read_installed` 复用已验证安装基线；`prepare` 继续承担导出等调用的外部文件重新验证。
- `application/projects/lifecycle.py`、`durable_variant.py`：锁内最终身份/CAS 检查、候选原子保存和发布。
- `application/translation/task_merge_evidence.py`：`TaskMergeEvidenceStore` 保存内容寻址的局部覆盖证据；不得替代 TaskRecoveryStore 的成果与任务身份记录。
- `config/task_results.py`：`TaskResultPreferenceRepository` 单独保存 AI、下载默认策略；缺省 `KEEP_CURRENT`。

这些接口已经由 AI、下载生产消费者接入，并经真实 facade、Qt 结果入口和故障注入验证；以下为最终契约与交接依据。

## 三方比较与逐项决策

每项输入是同一个 `EntryKey` 的 base/current/incoming。只遍历 incoming 的目标，未涉及条目、标签、其他外部引用与元数据保留。translation 与 stage 是一个关联组，不能分别自动选择后拼成从未存在过的状态。

判定顺序应明确为：

1. current 缺失、任一状态已删除、original 改变，或受保护的 context 改变：`source_changed_or_missing`，始终 pending。任何策略、手工选择或 no-op 都不能绕过结构边界。
2. 结构安全且 current 组等于 incoming 组：unchanged。incoming 组等于 base 组：保留 current；current 与 base 不同时计入 kept_current，否则 unchanged。以上不再要求旧的手工决定仍然新鲜，因为没有覆盖动作。
3. 对仍可能写入的显式选择验证 `decision_states`。`decision_states is None` 表示没有显式审阅快照；提供映射则必须校验选中项 current 的完整 `ResultEntryState`。同时给 `policies` 时只校验该映射命中项；没有 `policies` 时表示本批候选均已选择。缺失 reviewed 或 current 不相等一律 `decision_stale`、pending。
4. current 组等于 base 组时应用 incoming。其余属于真正冲突：KEEP_CURRENT 保留当前，PREFER_INCOMING 应用新成果，DEFER 挂起。三种处理均保留三方冲突证据。

`base.context is None` 表示调用方明确不依赖上下文；空字符串表示任务捕获的上下文确实为空，后续新增上下文也应被保护。AI 不能为了绕过结构冲突而把已有上下文改成 None。应用时原文和上下文取最新权威值。

`ResultMergePlan.applied` 只有在实际提交成功后才能成为实际应用回执。所有目标必须进入 applied、unchanged、kept_current 或 pending 之一；conflicts 是可与这些分类重叠的完整证据集合，不能拿 conflicts 数量当作剩余未处理数。

## 提交与恢复事件顺序

1. 任务开始捕获目标身份、固定输入与默认策略；完成的候选先进入独立内存成果，并尝试耐久保存。成果写入失败仍保有内存 ledger，可重试保存，不能重调 AI 或丢弃已下载部分。
2. 每次合入读取最新权威 Project revision、Variant snapshot 和安装源文本，重新计算局部 plan；任务开始 revision 不作为永久门禁。
3. 进入 lifecycle 提交锁，核验项目/版本身份及本次读取 revision。CAS 失败不执行 `before_commit`，重新读取并重算；三次仍竞争则返回可重试失败，保留成果。
4. 在同一受保护的提交区间内执行消费者授权守卫，再执行 `before_commit(plan)`，耐久保存本轮 base/current/incoming 和选择；只有成功返回后才能继续。
5. persist=True 时先由 DurableVariantCommitService 保存 Project/Variant 候选，再发布内存；persist=False 发布工作台并保持后续保存状态独立。
6. 提交成功后写成果处理回执。回执失败不能撤销已经发生的应用，也不能宣称成果未产出；重开后重读 current，利用 current==incoming 的 no-op 恢复状态。

公共 `guarded` 必须确认消费者调用了提交闭包。未调用应失败；重复调用应幂等，只执行一次，避免第一次已成功写入后因第二次调用报告整体失败。首次闭包异常须保留原始失败原因，不能按“已经调用”认定成功。

`before_commit` 是 write-ahead 证据，不等于提交回执。证据成功而后续保存失败时，文件保留，但恢复界面只使用实际成功回执证明的应用项；不能仅看到“prefer_incoming”就认定发生过覆盖。恢复旧值也必须使用同一合入协议，重新校验当前值和结构，不直接回写证据。

## 证据和安装缓存边界

TaskMergeEvidenceStore 保存 candidates 和所有冲突的 current；无冲突应用时 current 组等于 base，base 可提供覆盖前译文/阶段。真正冲突必须保存真实 current，不能用任务初始值代替。任务目标身份由对应 TaskRecoveryStore 记录绑定，重新打开处理器必须校验任务身份而非只依赖目录或当前窗口。

内容寻址记录重复保存可复用，但 `path.exists()` 不能证明原记录完整。命中旧文件应校验 schema、task_id 和内容 hash；损坏文件不得被静默覆盖或作为已耐久证据继续写工作台。读取损坏应给出文件级原因并保留原件。

安装缓存必须用 `_InstalledBaselineIdentity(baseline)` 而非裸 `id(baseline)`：包装器的强引用防止旧对象回收后 id 复用误命中。基线安装改变、项目/版本身份、请求权限、选项或指纹改变都应分区。基线对象必须在同一安装状态下稳定，否则安全性保留但缓存收益消失。

`read_installed` 的命中不重新读取磁盘，表示复用工作台已安装的源文本。首次读取仍需校验指纹；冷缓存时外部文件变化不能退回任务 base 伪造 current。重新安装源或依赖后必须更换安装基线身份。一般文件检查仍使用 `prepare`，不能把安装缓存扩展成通用跳过磁盘验证。

来源无法读取时相关目标 pending，其他来源继续。当前消费者显示原文/上下文/条目变化或来源暂不可读取的说明，底层具体异常记录日志；未将底层诊断细分为多个新的业务状态。

## 依赖有序实施与文件落点

1. 修正纯函数判定顺序和显式选择契约，补 `test_result_merge.py`：AI 的仅 decision_states 调用形状、缺少 reviewed、CAS 后新编辑、旧决定下 no-op、translation/stage 交叉修改、结构优先。
2. 在 ProjectResultMergeService 的 guarded 统一检查闭包执行，补 `test_project_result_merge.py` 的不调用/重复调用、证据失败、竞争重算和 no-op 证据路径。无需扩大所有 lifecycle 入口职责。
3. 为 TaskMergeEvidenceStore 增加独立测试：新写、相同内容复用、已有文件损坏、读取校验、before_commit 失败后工作台及磁盘不变。确定写前证据与写后回执的可区分状态。
4. 保持 result_sources / hydration cache 的强引用身份、只读模型和预算，测试安装对象更换及同指纹依赖更新，不只测试原文件 hash 变化。
5. S02/S03 消费者接入同一协议：AI 审阅传完整 decision_states；下载只对显式选项传 policies；成果在失败路径仍能显示、导出、重开和重试；结构冲突不能设置为强制应用。
6. 核验 TaskResultPreferenceRepository 与设置页：修改独立 section，取消不写盘；启动冻结，运行中设置改变只影响后续任务。设置保存失败不得显示保存成功。
7. 联合 S04 复验 dirty no-op：可以保存既有手动修改并清 dirty，但不增内容 revision、不新增完整历史。clean no-op 仍执行必要证据守卫；snapshot 复用不改变不可变语义。

## 本地复核与验收

原始 `uv run --no-sync pytest ...` 因沙箱 uv 缓存权限失败，未触碰 lockfile。改用现有 `.venv/Scripts/python.exe` 后，纯合入测试 22 项通过；使用专属工作区 basetemp 后，项目合入、耐久保存、源缓存测试共 36 项通过。第一次默认临时目录尝试出现 36 个 fixture 权限错误，未计作业务失败。临时目录由本复核创建并在结束时移除。

最终验证覆盖以下命令集合，精确参数、通过数和环境限制见 QA：

```text
uv run pytest tests/application/translation/test_result_merge.py tests/application/projects/test_project_result_merge.py tests/application/projects/test_durable_variant.py tests/application/projects/test_file_source_performance.py -q
uv run pytest tests/ui/tools tests/ui/operations -q
uv run ruff check src tests
uv run ruff format --check src tests
```

生产消费者已经验证：证据写失败不覆盖；项目写失败保留候选；实际回执失败可重试；显式审阅后再编辑不被旧选择覆盖；来源变化局部待处理；失败尝试不提供假恢复事件。核心联合 227 项、设置/config/bootstrap 相关检查及全仓 Ruff/格式通过；扩大矩阵的既有失败、Windows 文件系统限制与 Qt 单进程稳定性边界在 QA 中逐项记录，不把分文件通过等同于全仓全绿。

不改变项目文件格式或用户手动历史。回退实现前保留新增成果与证据，不通过清理未处理记录回退。本文不授权读取 `.transbridge` 用户数据、修改既有版本号/lockfile、提交或发布。

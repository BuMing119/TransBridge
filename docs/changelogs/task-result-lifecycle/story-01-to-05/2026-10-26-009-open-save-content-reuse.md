# 打开、保存与退出共用内容证据

- 日期：2026-10-26
- Epic / Story：task-result-lifecycle / S01–S05，第八轮
- 依据：用户启动恢复大型工程缓慢的反馈、前轮只读计时及本轮 bm-pilot 实施授权。
- 计划：[plan](../../../../plans/task-result-lifecycle/plan.md)；契约：[ADR-049](../../../adr/049-task-result-lifecycle.md)。

## 结果

正常打开时，每份未变工程/版本记录只完整校验一次，同一版本只还原一次词条；候选界面和正式工作台共用冻结词条数据。保存复用实际文件内容的校验证据，目标中未变词条省去逐条 schema 重验，跨条目语义和磁盘写入保护保留。clean 保存及退出继续走现行一致性确认并直接返回，不新增退出捷径或版本系统。对话任务索引首次使用时才后台建立，启动不再为隐藏编辑器预解析插件。

## 可归属的修改

1. `persistence/v2/validated_reads.py`：把保存事务缓存改为操作持有的 `ValidatedReads`；显式跨线程携带、嵌套作用域复用，键包含 repository/ref/实际 bytes。只保存不可变原始内容和元信息，命中返回重新解析的独立 DTO，防止嵌套 dict 篡改污染证据。完整验证的前态与仅差量验证的目标分开标识，后者不能冒充完整前态。
2. `repository.py`：新增 `read_snapshot_record()`，只读返回实际源摘要；旧格式只在内存归一化，不跳过随后正式迁移。load/read_snapshot/save 接统一解码，写入成功后才登记保存结果。`project_activation_journal.py`、`project_save_journal.py` 同步接入；保留实际读取、版本/摘要 CAS、写后读回、日志摘要/参与者检查和冷恢复。
3. 新增 `persistence/prepared_project_read.py`；`current_project.py` 的准备产物携带已构建 VariantSnapshot 和输入证据，`project_lifecycle_loader.py` 在常规候选链中核对输入并复用。准备后 same revision 的不同 bytes 不能发布，失败恢复原基线。独立审查补修旧格式的两种边界：另一正常 load 已迁移时允许匹配确切规范化输出；本次迁移还需原文件摘要相符，不能把外部改写借迁移洗成相同内容。
4. `application/projections/adapters.py` 增加 `prepare()` / `discard_prepared()`，候选和当前缓存隔离，成功后按实际 Project/Variant 对象推广；`project_entries.py` 复用整个冻结 rows 容器。`bootstrap/persistence.py` 注入 opener，`current_project.py` 在 finally 释放候选。正式保存状态等元信息仍更新，不能仅以 revision 求和判断内容相同。
5. 新增 `persistence/v2/variant_validation.py`，由 `schema.py` 在可信前态作用域内构造只含变化 entries/translations/labels 的结构校验视图；全局语义始终检查原始完整目标。比较使用区分 JSON 类型的规范内容，错误位置映射回真实词条下标。独立审查发现 escaped lone surrogate 导致 canonical 失败值被误作相等证据，已修成无法规范化就重新验证，并补真实 raw bytes 回归。
6. `ui/dialogue/controller.py`、`index_state.py`：隐藏状态只追踪来源失效；首次使用才准备关系结构并启动 worker，同源译文变动复用，真实来源或关系字段变化失效。`ui/workbench/translation_table.py` 接受现有集合的成员查询，保持双击编辑能力而不预先复制全部 key。新增 lazy 回归，并调整 editor/scenes/worker_lifetime 测试的首次使用时序。
7. 扩大 QA 发现此前恢复存储事务锁会使旧包导入误拒。`persistence/legacy_project_tasks.py` 只接受准确形状的 `ai-task-recovery/locks/16位小写hex.lock`，沿已有附件入口原字节保留为 `.bin`，不恢复成活锁；`test_legacy_project_task_assets.py` 增加对应断言。没有新增旧执行路径。

新增针对性文件：`tests/persistence/v2/test_project_open_reuse.py`、`test_validated_reads.py`、`test_variant_validation.py`、`tests/application/projections/test_prepared_project_projection.py`、`tests/ui/test_dialogue_lazy_index.py`。`test_save_validation_cost.py` 的计数预期更新为每种实际前后内容各一次，仍断言实际读盘与原子替换发生。

职责复核：输入准备证据、操作级内容证据、差量 schema 分别提取独立模块；repository 约 630 行、save journal 693 行，新增内容仅接入既有读写职责，不再承载证据存储算法。schema 501 行大部分为数据声明，新差量算法在独立 89 行模块中。后续新增存储职责不得继续扩张这些入口，应按既有协议提取完整切片。本轮未改依赖、installer 或已有版本号。

## 行为验证

现有 uv 环境，Python 3.12.12、Windows 11、Qt 6.10.0/PyQt 6.10.2，Qt 自动化为 offscreen；无真实网络或 LLM。实际命令另有任务专用 UV_CACHE_DIR、TEMP/TMP 和 pytest cache。

```powershell
uv run --no-sync --offline pytest tests/persistence/v2 tests/application/projects tests/application/projections tests/integration/bootstrap/test_persistence_v2_projection_composition.py -q --basetemp=.tmp-os8-final
# 最终 659 passed

uv run --no-sync --offline pytest tests/ui/test_project_open_operation.py tests/ui/test_project_open_guards.py tests/ui/test_project_view_installation.py tests/ui/test_projection_consistency_cost.py tests/ui/test_cancel_application_close.py tests/ui/test_save_recovery.py tests/ui/test_save_completion_state.py tests/ui/test_dialogue_lazy_index.py tests/ui/test_dialogue_authority.py tests/ui/test_dialogue_worker_lifetime.py tests/ui/test_dialogue_editor.py tests/ui/test_dialogue_reopen.py tests/ui/test_dialogue_projection_sync.py tests/ui/test_dialogue_scenes.py tests/ui/test_translation_table_model.py tests/ui/test_translation_table_sorting.py tests/ui/operations/test_download_autosave_boundaries.py tests/ui/operations/test_batch_download_autosave.py tests/scripts/test_benchmark_project_open.py -q
# 206 passed

uv run --no-sync --offline ruff check src tests
uv run --no-sync --offline ruff format --check src tests
# 全部通过，2237 files already formatted

uv run --no-sync --offline pytest --collect-only -q
# 9649 tests collected

git -c core.safecrlf=false diff --check
# 通过
```

上述两组目录互不重叠，共 865 项取得通过证据；UI 组先于最后的差量 schema 切片完成，最终 659 项已覆盖该切片。实施阶段的 68、61、54、127、168 等组与最终矩阵有交集，不累计。

全新独立 Agent：UI/投影两组 93 项通过；存储审查先验证 79 项，再用原失败 probe 和 17 项迁移/附件回归闭环，最后差量切片 32 项通过。105 组完整验证与差量验证差分 probe 覆盖缺字段、坏类型、布尔/数字区别、数组重排、错误下标及全局身份约束，结果一致；特殊 surrogate 问题单独发现、修复、复验。

早期一条新测试拼接了未编码 variant 文件名，已改用 repository.path_for；一次 UI 命令引用了不存在的测试名，未执行，随后使用真实文件重新运行。宽回归曾出现旧包锁文件误拒，已修复；SQLite 附件测试的长临时路径触及 Windows 限制，换短 basetemp 后全组通过。未加入业务重试或删除保护来消除测试失败。

## 性能证据

合成 ParaTranz JSON，单来源、无真实用户文本。复用 `scripts/benchmark_project_open.py` 的真实 ProjectCoordinator、AppContext、Step2PreviewWidget；全 MainWindow 订阅不在该基准内。计时不含 fixture 创建；每组首次打开后至少 5 次 reload，未测真实用户工程或峰值内存。

原始前后命令均为：

```powershell
uv run --no-sync --offline python scripts/benchmark_project_open.py --entries 1000 10000 --repeats 5 --output <本轮新临时JSON>
```

- 1k reload：before 中位 1223.21 ms，范围 1014.88–1271.63；after 中位 606.18 ms，范围 200.32–661.57。冷打开分别 390.86 / 222.04 ms。
- 10k reload：before 中位 12424.85 ms，范围 12134.67–12518.24；after 中位 6747.65 ms，范围 6558.55–6833.29。冷打开分别 12049.86 / 6647.29 ms。

机器负载在独立运行间明显变化，故另外用临时 instrumentation 做同进程交替对照，每种各五次。该对照保留当前代码，仅在对照轮恢复重复 schema、第二次 from_dto 与候选投影重复生成；所有文件/写入操作仍执行，不是历史版本的全量回滚。

- 10k 打开对照轮 ms：13155.43、13246.55、12587.01、12971.43、12441.84；中位 **12971.43**。
- 10k 打开复用轮 ms：7001.32、6918.21、6794.20、6754.25、7133.42；中位 **6918.21**，本机该样本减少约 **46.7%**。
- 同场景计数回归：每份工程/版本完整 schema 一次，VariantSnapshot.from_dto 一次，候选/正式投影共享冻结 entries。插件与 snapshot 两条对话索引路径均为启动 build/catalog=0、首次使用=1、同源重开仍=1、换源再使用=2。

保存另用 10k 工程，每次仅修改一条词条，通过真实 gui_project_commands.update_entry/save。独立 before 五次中位 2028.67 ms，after 4131.68 ms，负载差异使这两组不能用于证明优化。因此在同一进程交替启用/禁用差量目标 schema（每种五次，其他读取复用和写入路径相同）：

- 完整目标 schema ms：1638.56、1613.18、1691.23、1760.10、1684.46；中位 **1684.46**。
- 差量目标 schema ms：1258.47、1277.30、1313.82、1227.40、1244.33；中位 **1258.47**，本机该样本减少约 **25.3%**。
- 实际 JSON Schema 遍历计数证明只改一条时 entries、translations、labels 各检查一条；无可信完整前态时仍全量检查。

临时 instrumentation 的对照点：打开轮禁用 `validated_read` 命中和 `prepared_variant` 复用，并使 publisher.prepare 使用普通 project_projection；保存轮使 `reuse_variant_preimage` 绑定 None。均只用于合成基准，不写入生产代码。临时脚本、样本、JSON 和测试缓存记录后清理。

## 兼容与边界

不改变工程文件格式或额外创建正式版本；`.transbridge` 自动升级及原包保留不变。未读取用户 `.transbridge/` 数据，未提交或发布。

仍按现有整文件格式落盘；变化定位、全局身份语义、首次磁盘完整验证及读回仍可能随总量增长，不宣称保存整体为常数时间。未实现跨进程解析缓存，也没有把其他来源改成按需缺省加载；这涉及来源完整性和导出契约，本轮保留现行行为。主要重复计算与未使用的对话索引工作已解决，未测真实桌面、ESP/ESM 大工程端到端速度，也未执行全部 9649 项测试。

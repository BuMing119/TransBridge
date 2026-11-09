# FR32：翻译项目库、独立任务与稀疏保存

- 日期：2026-11-09。
- Epic：project-storage-redesign；Story：S01～S10。
- 依据：[需求](../../../requirements/project-storage-redesign.md)、[ADR-054](../../../adr/054-project-storage-and-independent-tasks.md)、[计划](../../../../plans/project-storage-redesign/plan.md)。
- 分工：按用户要求，生产代码与单元测试由独立 GPT-6.1 Sol / high 子 Agent 完成；主会话审查、执行整体回归与性能对照，并维护文档。未提交或推送 Git。

## 行为变化与实现归属

项目现在使用 `project-store-v1/<ProjectId.encoded>/project.sqlite3`，任务使用独立的 `task-store-v1/tasks.sqlite3`。项目保存原文、译文、必要定位和格式字段，不保存插件字节、完整插件树或历史来源路径。任务成果、报告、日志和恢复材料由任务独立持有；关闭或删除项目后仍可查看、导出任务成果。

普通保存提交稀疏变化；任务合入先显示“已应用、待保存”，项目事务提交实际变化和凭据后才确认持久合入。项目已经提交而任务回执失败时只对账，不重放翻译或业务写入。无修改保存不写文件。保存期间继续编辑、撤销或覆盖候选时，不会丢失新编辑或错误确认候选已保存。

打开先读取当前来源的有限页。完整搜索、排序、全选、批量操作和导出仍覆盖完整范围。插件的对话阅读顺序使用单独索引，翻译文件导出保留原物理顺序。原插件删除后仍能编辑和导出翻译文件；写入插件需要当次重新选择输入，插件生成 SSU9 使用保存的最小字段，不重新保存原插件。

版本复制、命名快照、更新源文件和项目包使用新数据库事务。包只携带正式业务内容及选中的不可变术语资产，排除任务和运行凭据。相同插件字节、不同 Strings 内容也会产生正确的来源修订。旧工程和旧包明确拒绝，原件不迁移、不重写、不自动删除；原有大目录占用不会随本次改造自动释放。

逐文件操作类型和涉及符号见[文件清单](files.md)。主要责任切片如下：

- S01/S02：`storage_contracts.py`、`source_extraction.py`、`source_fields.py`、`native_source_formats.py`、`translation_templates.py`；`project_store/format.py`、`schema.py`、`connection.py`、`writes.py`、`entry_codec.py`、`repository.py`。规范化字段、严格格式身份和直接事务替代完整对象序列化；新建项目无 artifact 发布。
- S03/S04：`working_copy.py`、`save_changes.py`、`variant_commands.py`、`deferred_variant_rows.py`；`project_store/reads.py`、`paged_reads.py`、`variant_rows.py`、`lifecycle.py`、`save_verification.py`；`ui/project_rows.py`、`workbench/project_paging.py`。少量编辑只查目标身份，准确确认保存批次；过期页面、来源切换和关闭后的结果不会覆盖当前视图。
- S05/S06：`task_store/identity.py`、`payloads.py`、`recovery.py`、`results.py`、`reports.py`、`decisions.py`；`tasks/history_service.py`、`result_application.py`；`project_store/receipts.py`。Owner/Session 查询隔离、不可变候选世代、明确清理租约和 cleanup_pending 重试；没有新增按天自动删除策略或容量管理界面。
- S07：`write_inputs.py`、`write_input_coordinator.py`、`plugin_sst_facts.py`、SST adapter/export 与 publish 校验、助手写入入口。当前输入授权、原插件输出保护、配套 Strings、暂存发布和格式保真仍生效。
- S08：`project_store/history.py`、`historical_restore.py`、`source_updates.py`、`archive.py`、`archive_validation.py`、`archive_native.py`、`catalog.py`；`terminology/project_selection.py`、`portable_assets.py`。来源历史、版本、白名单包、复制身份和术语选择单点权威。
- S09：bootstrap、项目/版本 coordinator、`project_drop_probe.py`、GUI task history、助手和同步结果入口均接到新服务。旧项目专用实现不在生产打开/恢复链中；Session 等仍使用的 v2 基础设施保留。
- S10：`benchmark_project_storage.py`、`compare_project_storage.py`、新增 SQLite/真实 Qt/任务回执/项目包测试，以及原有项目、助手和 UI 测试夹具迁移。没有修改依赖锁文件或打包配置。

需求、ADR、计划及三个 Story 的状态和实际落点同步更新；旧规划增量和历史报告保持原样。超限旧模块只保留既有职责的接线，新增 SQL、分页和保存责任已独立。DropRouter 抽出识别后为 483 行；既有 lifecycle、AppContext、Step2 与 facade 仍有规模债务。后续新增职责或退役 facade 的任务接口时应继续拆分，不能据本轮接线例外持续扩张。新增项目/任务存储切片均未超过 500 行。

## 总体验证

主会话实际运行 `UV_NO_CACHE=1 uv run --offline --no-sync python -` 调用 `pytest.main`，逻辑选择如下。Windows 沙箱临时目录以任务自有根运行，限定修改该根的 0700 创建方式、缩短 pytest 目录名；结束恢复设置并清理。未修改生产权限策略。

- `tests/application/projects tests/application/translation tests/contracts/io tests/security/test_archive_attack_corpus.py`：最终 **1277 passed、1 skipped，92.32 秒**。
- `tests/smart_assistant tests/persistence/project_store tests/persistence/task_store --ignore=tests/smart_assistant/test_execution_engine.py`：**1213 passed，63.25 秒**。最后稀疏改动后重跑 `tests/smart_assistant/tools tests/smart_assistant/postprocess`：**375 passed，32.47 秒**。
- `tests/ui/tools -k 'task or result or recovery or download or workflow_routes' --ignore=tests/ui/tools/smart_assistant/test_window_minimum_size.py`：最终 **655 passed、1189 deselected，242.85 秒**。
- 六个打开/拖放/包/窗口测试文件及真实分页对话测试：**101 passed、1 skipped，18.66 秒**。独立窗口覆盖没有省略；任务窗口错误投递测试修复了初始发现与所选结果错误之间的等待顺序。
- 新项目 composition、包术语资产、回执、任务历史、分页工作台/对话及两套 SQLite 测试：最终 **84 passed，12.90 秒**。包含完整读取后 1/100 条修改的精确解码范围、保存中再次编辑、未加载来源保持、标签空更新、外部引用冲突、分页任务合入与同事务凭据。
- bootstrap、publish、三个 GUI integration 和 entrypoints 联合：**331 passed、1 skipped、3 failed，66.92 秒**。三项失败与编码前基线逐项同原因：缺 Owner 的旧工具报告成功断言；术语浏览多出 `Missing/孤立映射`；术语验证诊断多出项目/版本字段。它们未作为本次功能修复，不宣称全库全绿。
- `test_execution_engine.py` 隔离诊断 **15 passed，0.21 秒**。早期与 Qt 用例合跑发生卡住；显式预载 GraphExecutor、将默认检查点限制在任务临时根、使用 `-vv -s -o faulthandler_timeout=15` 后通过，不能据此断言卡住根因已确定。精确核验并清理本轮产生的测试检查点，保留共享目录及原有文件。
- `tests/performance/test_project_storage_scaling.py -s`：**3 passed，62.55 秒**。测试包括 10k→100k 查询/写入成本，以及 100k/80 来源、100k/单来源的真实 Qt 打开和完整筛选。
- `uv run --offline --no-sync ruff check src tests scripts/benchmark_project_storage.py scripts/compare_project_storage.py` 与对应 `ruff format --check`：通过，2523 个文件已格式化；`git diff --check` 通过。

这些分组存在重叠，不相加宣称唯一测试总数。未运行远端 LLM、发行构建、安装器验证或完整全库 pytest。较早临时夹具出现过 Windows 文件替换权限错误和 QApplication 重建导致的原生退出；共享 Qt 生命周期、线程收尾及最新分组已复验通过，不能把所有历史失败笼统归为环境问题。

## 同输入性能与空间证据

旧实现来自只读 `git archive HEAD src`，HEAD 为 `8fc9952e4cb99c7fdf08ecb2f0187010674918ab`；新实现来自本轮工作树。两者独立进程用同一 venv、相同的 `storage-comparison-paratranz-v1` 合成输入、100000 条/80 来源。测量实际 ProjectCoordinator、首屏绘制和完整 100000 条范围读取，再执行 0/1/100/10000 条修改保存。

环境：Windows 11 10.0.26200、Python 3.12.12、Qt 6.10.0 / PyQt 6.10.2、offscreen。首次打开在刚建项之后，没有清空系统缓存；开发回归同期可能有机器负载。每侧 1 次进程内首次打开和 5 次重载，使用中位和范围，不声称 P95、冷缓存结果或用户真实工程承诺。

- 首屏绘制重载中位：**71482 → 103 毫秒**。新版显示首批 100 行；全范围读取并未隐去，累计就绪中位 **71538 → 9383 毫秒**，新版五次为 8.81～9.85 秒。
- 单条持久化保存中位：**29734 → 80 毫秒**；100 条为 **23218 → 90 毫秒**；10000 条为 **34357 → 1705 毫秒**。无修改均零写入。
- 新版编辑准备中位：1 条 18 毫秒、100 条 20 毫秒、10000 条 2322 毫秒；10000 条准备加保存的中位 **4107 毫秒**。旧版补测一轮的对应准备为 5805/5999/10030 毫秒，总计为 38745/39900/45493 毫秒。补测一轮不冒充五轮中位。
- 单条保存进程写字节：旧版约 **63.53 MB**，新版 **25124 B**；100 条最新样本 57924 B，10000 条约 3.58 MB。进程写计数不等于 SSD 物理写放大。
- 建项后 workspace 文件合计 **240367808 → 37040228 B**；五轮保存后 **240294106 → 38817893 B**。本夹具没有历史任务，不能用移动任务目录解释减少，也不能据此预测原始插件项目压缩比例。
- 重载期间首屏心跳最大间隔 **4128 → 53 毫秒**。新版单条保存的最长间隔约 65 毫秒，批量保存约 94 毫秒。编辑准备为同步命令单独计时，此处的心跳观测不覆盖该准备阶段，不据此宣称所有批量 UI 操作无停顿。
- 全范围读取阶段的采样 RSS 峰值约 **1.81 GB → 355 MB**；Qt 定时采样可能漏掉短暂峰值，不是操作系统高水位或整机内存承诺。

原始数据：[旧版五轮](benchmark-head.json)、[旧版编辑准备补测](benchmark-head-edit.json)、[新版五轮](benchmark-current.json)、[存储与 Qt 扩展样本](benchmark-scaling.json)。复验命令：

```text
uv run --offline --no-sync python scripts/compare_project_storage.py --source-root <只读 HEAD 快照或当前仓库> --rows 100000 --sources 80 --repeats 5 --output <新的 JSON 路径>
```

扩展结构基准：10k、100k、100k 加 1000 历史任务，读首批均为 3800 VM 步；单条保存均改变 3 行、16 KiB 主文件页，提交前 journal 12824 B，任务历史没有被项目流程遍历。100k/单来源的完整筛选仍约 3.53～8.28 秒，采样心跳最大 250 毫秒；本轮没有将它包装成即时筛选。

基准曾发现两个真实瓶颈并推动修复：分页 SQL 首批仍全扫描/排序；以及批量条目命令把延迟范围展开后，单条保存虽仅写 25 KiB 仍耗约 10 秒。最终修复索引查询及稀疏工作副本传递，新增真实 Qt/应用命令回归，未通过提高阈值掩盖失败。

## 交付边界

本地实现与相关回归完成。上述三项既有失败、执行引擎早期合跑卡顿的未定根因、单大来源完整筛选延迟、未测冷缓存和未执行发行验证均保留。旧用户目录及真实插件未被修改或清理；没有旧项目迁移器。正式任务历史默认保留，删除和中间材料回收使用显式入口及运行保护。

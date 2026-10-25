# S22：同步保存后的工作台投影与保存交付竞态

日期：2026-10-25。
关联：[当前计划](../../../../plans/paratranz-categorized-upload/plan.md)、[ADR-047](../../../adr/047-opt-in-categorized-project-upload.md)、FR3.3 / FR22.3.2。

## 问题与结果

用户反馈同步完成后，工作台的插件仍显示 44 条未翻译，退出又出现
PROJECTION_AUTHORITY_DIVERGED 和“项目尚未保存”。

保存链已经把候选写入当前本地版本，但同工程/版本投影边界只在外部关联变化时同步关联及修订。
下载不保存远端关联，通常根本不会触发该路径；译文和 stage 始终留在旧工作台集合中。
保存检查正确发现了可见内容与权威版本不一致，故不能通过移除检查修复。

现在统一推进所有已加载槽位的译文和状态，当前表格与统计及时更新，切换插件和重新打开版本保持一致。
真正未提交的本地内容继续保留。单插件/批量共用入口、自动保存当前版本、下载前还原点和失败原地重试保持。

进一步受控复现了并行自动保存竞态：磁盘已写成，Qt 尚未消费更新时，保存线程会误把正常交付间隔
判为未提交编辑，导致自动保存暂停。后台保存现会等待已接收投影完成再核验；Qt 线程不等待。
准备异常保留原始日志并在 Qt 边界重新协调，关闭投影会唤醒等待者，不留下永久等待的保存线程。

## 本轮可归属变更

- src/transbridge/ui/entry_projection.py：替换此前仅关联协调函数，增加统一三方协调。
  译文与 stage 是原子内容对，前后权威字段必须完整且非推断；UI 仍等于旧权威时才推进。
  关联独立协调，修订单调，provenance 仅跟随已确认内容；缺字段不当作清空。
  完整 EntryKey 匹配，保留原文、上下文、元数据、未变集合及真实本地编辑。
  增加数据型准备结果，在发布前校验槽位、集合、成员对象及 legacy 可变字段，
  全部输入有效后才统一替换；输入变化则返回重新协调，不先发布部分槽位。
- src/transbridge/ui/project_view_binding.py：同工程/版本及 stream 内统一消费内容更新；
  后台收到提交时准备集合，Qt 交付时检查输入，必要时按最新视图重新协调；
  只在当前集合实际替换时通知工作台，非当前槽位仍同步。
  保留项目安装、迟到事件及版本隔离守卫。条件变量跟踪接收和交付，
  后台一致性检查等待正常交付，并在比较期间又接收新版本时重读；真正分歧仍拒绝保存。
- 新增 tests/ui/test_authoritative_content_projection.py：7 项真实 AppContext、Preview 和 StatsPanel
  回归，包含 44 条译文、后台交付、未译筛选成员变化、同 key 异 namespace、切换来源，
  以及筛选、倒序、选中、滚动和持久索引保留。
- 新增 tests/ui/test_content_projection_merge.py：19 项内容/关联三方合并回归，
  包括只改 stage 的本地编辑、缺失/推断字段、完整原文限定身份、全副本、队列和版本隔离。
- 新增 tests/ui/test_prepared_entry_projection.py：11 项真实线程与准备竞态回归，
  验证准备及集合构造发生在 worker，发布/通知发生在 Qt；准备后替槽位/集合、
  原地编辑、V2 apply/remove、末槽位失效和连续回执均保持原子性及真实编辑。
- 新增 tests/ui/operations/test_download_workbench_projection.py：7 项真实默认 facade、
  ApiWorker、Service、HTTP 适配器、AppContext、表格及持久化回归，仅替换 HTTP 端口。
  验证当前/全部来源、保存后可见内容、来源切换、退出保存检查、磁盘重开水合；
  受控准备屏障验证并行保存等待、真实草稿不放行、异常回退和关闭唤醒。
- tests/ui/test_dialogue_authority.py：正常提交现在先断言立即刷新，再显式安装旧集合，
  继续独立检验旧 UI 不能覆盖新权威；没有删除原有防覆盖测试。
  test_dialogue_consistency.py、test_dialogue_reopen.py：实际冲突现先由更新后的可见快照发现，
  验证草稿保留及无部分写入，不强制后置检查的旧文案。
- src/transbridge/ui/i18n/messages.pot：同步此前授权的精确入口“从paratranz上同步”，
  删除已经取消的批量下载独立入口消息；中文 PO 原设计使用源文案回退，不增加重复条目。
  ui/shell/action_catalog.py 仅补回“下载并合并”搜索别名；
  tests/ui/ux/test_current_user_journeys.py 同步批量下载兼容别名实际调用统一 download 的合同。
- 更新现行需求、ADR、开发说明、当前计划及直接索引。S11～S21 等历史记录保持原文。
  工作区大量其他未提交修改不归入本轮。

## 复现与验证

真实工作台组件用例在旧实现上 7 项失败，新实现通过；
真实默认下载链用例受控载入本轮修改前两个投影模块，在独立测试进程中 3 项均失败：
磁盘保存断言先通过，但实际可见译文仍为空，退出检查报原错误。
未通过回滚共享生产文件取得该证据。

扩大回归在批量后台准备落地后执行，结果为 **3029 passed、1 skipped、3 deselected**，
180.56 秒。最终补齐保存交付等待后再执行保存/投影/对话/AI 专项，
结果为 **175 passed**，18.70 秒。两组有重叠，不相加。
新增四个回归文件共 44 项。

本轮实际命令（均使用仓库现有 uv 环境、--no-sync；TEMP/TMP 和缓存隔离到本轮临时目录）：

~~~powershell
uv --cache-dir .tmp-projection-sync-20261025/uv-cache run --no-sync pytest tests/ui --ignore=tests/ui/tools tests/application/projects tests/persistence/v2 tests/contracts/paratranz tests/paratranz -q --ignore=tests/contracts/paratranz/test_terms_api_contract.py --ignore=tests/paratranz/test_typed_client.py --deselect=tests/ui/characterization/test_main_window_contract.py::test_autosave_stop_is_idempotent '--deselect=tests/ui/test_assistant_project_navigation.py::test_dirty_save_or_discard_reuses_project_lifecycle_decision[True]' -k 'not test_reordered_source_projects_into_context_only_remote_plan' --basetemp .tmp-projection-sync-20261025/final-integrated

uv --cache-dir .tmp-projection-sync-20261025/uv-cache run --no-sync pytest tests/ui/operations/test_download_workbench_projection.py tests/ui/test_prepared_entry_projection.py tests/ui/test_reference_projection.py tests/ui/test_content_projection_merge.py tests/ui/test_authoritative_content_projection.py tests/ui/test_project_view_installation.py tests/ui/test_background_gui_operations.py tests/ui/test_save_completion_state.py tests/ui/test_save_recovery.py tests/ui/test_dialogue_authority.py tests/ui/test_dialogue_consistency.py tests/ui/test_dialogue_reopen.py tests/ui/tools/test_ai_frozen_save_boundary.py tests/ui/tools/test_ai_launch_refresh_guard.py tests/ui/tools/test_ai_name_publication_roundtrip.py tests/ui/tools/test_generated_term_roundtrip.py tests/ui/tools/test_ai_result_background.py tests/ui/tools/test_ai_result_recovery.py tests/ui/tools/test_project_reference_large_task.py -q --basetemp .tmp-projection-sync-20261025/final-save-and-ai

uv --cache-dir .tmp-projection-sync-20261025/uv-cache run --no-sync ruff check src tests
uv --cache-dir .tmp-projection-sync-20261025/uv-cache run --no-sync ruff format --check src tests
~~~

Ruff 全库通过，格式检查 **2197 files already formatted**。
保留现有 SWIG 和 legacy 直接修改弃用警告。

三项排除均有旧基线证据：

- test_autosave_stop_is_idempotent 的测试 host 只有 _ctx，当前 AutoSaveManager 读取公开 context。
- test_dirty_save_or_discard_reuses_project_lifecycle_decision[True] 的测试 host 缺公开 project_commands。
  上述两项在隔离加载本轮修改前投影模块时同样失败，没有修改无关 fixture。
- test_reordered_source_projects_into_context_only_remote_plan 是此前已独立复现的上传排序断言。

两个自行启动本机 HTTP server 的测试文件沿用前轮沙箱套接字限制排除；
本轮默认下载链通过 HTTP 端口替身覆盖生产 Service/API/文件网关，没有访问真实云端。
一次扩展测试和一次过早加载基线的尝试遭遇 Windows FAISS 原生导入异常；
按正常收集顺序重跑已通过，不把原生崩溃计入功能验证或改动依赖。
未执行全库 pytest、真实云端同步或安装包构建。

## 批量发布阶段测量

80 个真实集合、96,306 个 TranslationEntry、真实不可变 ProjectionSnapshot；
仅测准备和发布，不计 fixture 构造、网络、持久化、ProjectViewState/标签捕获和表格渲染。
每场景三轮，准备在线程池 worker，核对/发布在主线程：

- 44 条内容变化：准备 0.214529 / 0.213298 / 0.209696 秒；
  发布 0.001945 / 0.005048 / 0.001671 秒，中位 **1.95 ms**。
- 全量内容变化：准备 3.508162 / 3.429795 / 3.491734 秒；
  发布 0.031704 / 0.041185 / 0.039919 秒，中位 **39.9 ms**；
  准备对象释放中位 7.07 ms。

初版把全量协调放在 Qt 时纯 helper 中位 4.129 秒，因此增加后台准备；
这不是总 CPU 加速对比，也不代表端到端“无卡顿”。
准备仍处于 ProjectionStore 同步通知期间，其他快照读取可能等待通知锁；
直接 Qt 提交及输入过期后的重新协调仍使用同步路径，未宣称这些少见路径的全量延迟上限。

## 兼容性与收尾

没有修改本地格式、版本数据或用户工程，没有云端写入和 Git 提交。
正常使用重新启动修复后的程序；已经落盘的译文按现有权威水合读取，不要求额外数据迁移。
真正未提交内容仍须成功提交或显式备份，不将其当成旧投影覆盖。

本轮 .tmp-projection-sync-20261025 的基线、测试、缓存和性能产物已在所有 Agent/测试完成后，
校验绝对路径、OWNER 及无重解析点后清理；保留既有 .tmp-batch-download-a0eb07b2、
.transbridge 和用户数据。遗留验证范围及通知锁性能边界如上。

# 回归契约、后台生命周期与检查点更新修正

- 日期：2026-10-01
- Epic/Story：maintenance / regression-suite
- 来源：用户要求核对完整测试失败的实际情况并作出调整。
- 前序证据：[来源更新后完整回归](../../source-file-update/story-01-03/2026-10-01-003-full-regression.md)。保留原始结果，不修改历史记录。

## 测试、资源与文档

- `tests/contracts/projects/test_authoritative_mutation_paths.py`：扫描新增的 `projection_restore.py`，将两个回滚写入点移至实际函数；保留旧模块扫描和严格集合相等约束。
- `tests/integration/terminology/test_migration_fault_rehearsal.py`：验证迁移至当前 Project schema，重命名旧 V3 用例；保留旧文件备份字节校验，区分 Project 与术语 SQLite 版本常量。
- `tests/integration/terminology/test_ui_workflow.py`：按[已实施的紧凑改版](../../terminology-localization-profiles/story-09-workbench-scheme-hub/2026-09-10-002-术语工作台紧凑布局与已选副本浏览.md)验证“术语、版本、报告”三页，保留业务命令委托检查。
- `docs/requirements.md` 的 FR5.16.30、验收及变更历史，`plans/project-terminology-build-versioning-reporting/plan.md` 和 Story 11：原位修正当前规范中的旧四页描述，保留明确标注日期的历史证据。
- `tests/paratranz/test_entry_upsert.py`：分别断言同项目引用成功、跨项目引用遇重复 key 失败；保留远端数据不变及不扫描猜测 ID 的检查。
- `tests/ui/test_accessibility_contracts.py`：使用正式 `TaskActionAvailability`，保留 Escape 不停止任务及可访问说明检查。
- `tests/ui/tools/test_layout_stability.py`：检查单一输入卡、高度上限、固定纵向布局、长文本滚动而不撑高；保留发送和自动模式检查，不锁死旧 112 像素实现值。
- `src/transbridge/ui/i18n/messages.pot`：本轮补两条搜索消息；前轮已补的“更新源文件…”保留，资源覆盖测试不放宽。
- 将 `tests/application/terminology_sync/test_identity.py`、`test_models.py` 原样移动为 `test_terminology_sync_identity.py`、`test_terminology_sync_models.py`，消除默认收集重名，不改 import mode、不排除目录。
- `tests/quality/test_success_chains.py`：固定输入 ZIP 时间戳及受控确定性测试打包前的文件 mtime，保留真实打包器和全部指纹比较。FR23 不要求生产 ZIP 跨实际时间字节恒等；不控制时间的真实九阶段成品内容/hash 测试另行通过。

## 生产修复与新增回归

- `src/transbridge/ui/dialogue/controller.py`：通过带 QObject 生命周期的接收者投递索引结果，仅弱引用控制器；窗口销毁后拒绝迟到成功/错误回调，继续清理 worker。新增 `tests/ui/test_dialogue_worker_lifetime.py` 两项回归。
- `src/transbridge/ui/tools/smart_assistant/request_timeline_view.py`：独立读取器接收 Future，窗口关闭先停投递再清理，移除捕获窗口的后台回调及裸销毁 lambda；对应时间线测试新增父窗口先销毁的释放回归。
- 三项新生命周期测试在子进程内加载 HEAD 旧实现后全部失败，修复后通过。确认修复了两处迟到回调访问/持有已销毁 UI 的风险；不能据一次连续通过证明旧随机 native access violation 的所有根因均已消除。
- `src/transbridge/application/tasks/checkpoint.py`：`mark_committed()` 受控浅复制已验证的不可变记录，仅更新修订和新增 ID 集合，不再每次重新验证全部历史。新 ID 严格检查字符串及非空；构造和反序列化仍全量验证，数据格式不变。
- `tests/contracts/test_checkpoint_runtime.py`：新增八项字段完整性、原对象不变、往返和非法 ID 回归；保留幂等测试。计时前清理前置垃圾，测量中 GC 开启，20 样本和 P95 小于 100 毫秒门槛不变。
- 诊断先证明前置垃圾可干扰测量，但预清理后聚合仍超时。探针排除计时中 GC、trace/profile 与后台线程后，旧实现 P95 仍为 101.79 毫秒；profile 显示历史记录重复校验占主要耗时。生产优化后聚合 P95 为 17.373 毫秒，20 次更新约 600 万次调用降为 1421 次；不能把全部失败归因为 GC。
- `tests/contracts/test_task_runtime_backends.py`：用真实条件等待入口的事件握手代替提前启动 sleep 后的最短耗时断言，验证任务未结束时 shutdown 不返回、任务不取消，完成后限时返回。保留 grace 上限与独立超时检查。

## 验证

- 七个原失败相关文件联合：`uv run pytest tests/contracts/projects/test_authoritative_mutation_paths.py tests/integration/terminology/test_migration_fault_rehearsal.py tests/integration/terminology/test_ui_workflow.py tests/paratranz/test_entry_upsert.py tests/ui/test_accessibility_contracts.py tests/ui/tools/test_layout_stability.py tests/ui/foundation/test_locale_service.py -q` → 47 passed。
- `uv run pytest tests/application/terminology tests/application/terminology_sync tests/ui/tools/terminology tests/integration/terminology -q` → 192 passed。
- `uv run pytest tests/quality/test_success_chains.py -q` → 12 passed；`uv run pytest tests/test_fomod_typed_pipeline.py::test_real_small_fomod_runs_all_nine_stages_and_publishes_verified_archive -q` → 1 passed。四轮间隔 2.1 秒的受控真实流水线指纹一致。
- `QT_QPA_PLATFORM=offscreen`，`uv run pytest tests/ui -q` → 1056 passed、1 skipped，70.79 秒；没有隔离 UI 模块，没有原生崩溃。生命周期专项 62 passed。
- 同样离屏环境下 `uv run pytest -q -ra --disable-warnings` → 连续执行 4806 项，4797 passed、8 skipped、1 failed，692.65 秒，无原生崩溃；唯一失败为随后修正的 checkpoint 性能项，原始值 101.509 毫秒。这一轮早于最后的性能与 shutdown 夹具修正，不能改写为整库全绿。
- 仅预清理垃圾后的完整 contracts 曾为 547 passed、2 skipped、1 性能失败；性能优化后聚合探针曾为 555 passed、2 skipped、1 shutdown 时序夹具失败。两次都继续定位修正，没有用单独通过代替聚合验证。
- **最终 `uv run pytest tests/contracts -q` → 556 passed、2 skipped，78.47 秒。** checkpoint/runtime/graph 专项 100 passed；shutdown 事件握手额外 20 轮通过。最后新增的八项 checkpoint 回归包含在最终 contracts 中；最后修正后未重复整库执行。
- 最终 `uv run ruff check src tests`、`uv run ruff format --check src tests`（1433 文件）及 `git -c core.safecrlf=false diff --check` 通过。

八项跳过为五项 Windows 符号链接权限、一项非法文件名、一项长路径环境限制及一项缺少专用 ParaTranz 远端凭据。仍有既有弃用警告，本轮不扩展为警告清理。未做真实远端写入、发行安装包或人工桌面验收；没有提交代码，任务自建临时工程已清理。

`docs/changelogs/INDEX.md` 增加本记录；`plans/source-file-update/plan.md` 链接后续修正证据，并区分整库原始结果与最后复测。

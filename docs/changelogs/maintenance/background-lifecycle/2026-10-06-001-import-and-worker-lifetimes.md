# 修复迁移原子性与后台任务生命周期

- 日期：2026-10-06
- Epic / Story：maintenance / background-lifecycle
- 前序：[工作台译文导入修复](../translation-import/2026-10-06-001-import-worker-and-plugin-text.md)
- 范围：后续全面审查发现的六类当前入口缺陷、两项旧 AI 兼容线程缺陷，以及真实绘制回归发现的导航画笔类型错误。

## 行为与生产文件变化

- 修改 [parse_coordinator.py](../../../../src/transbridge/ui/coordinators/parse_coordinator.py)，新增 [migration_coordinator.py](../../../../src/transbridge/ui/coordinators/migration_coordinator.py)：XML、Strings、ESP、JSON、SST 共用独立草稿与受保护的提交。后台不修改 live collection、路径或 lookup；GUI 提交前校验捕获的工程、版本、修订、集合内容和来源信息。任一来源解析失败、目标变化或提交失败时保留原状态，损坏 XML 不再报告成功。保留两个旧私有入口作为薄委托，原协调器由本轮开始的 672 行降为 474 行。
- 新增 [legacy_migration.py](../../../../src/transbridge/application/io/legacy_migration.py)：独立准备 XML/Strings 草稿，复用现有匹配规则和严格 Strings 适配器，不依赖 Qt。保留 EET→XT→Strings 顺序，以及成功读取 lookup、同译文 XML 来源登记的原语义；无状态变化不重复提交修订。
- 新增 [worker_registry.py](../../../../src/transbridge/ui/worker_registry.py)，修改 [workers.py](../../../../src/transbridge/ui/workers.py)：ApiWorker 启动时由应用保活。finished bridge 只传整数标识进入 GUI 队列，不使用 QObject.destroyed 的 Python 清理回调；结果处理后使用非阻塞 wait(0) 确认原生线程退出，未退出则由应用持有的计时器复查。
- 新增 [close_input_guard.py](../../../../src/transbridge/ui/shell/close_input_guard.py)，修改 [window_lifecycle.py](../../../../src/transbridge/ui/shell/window_lifecycle.py)：主窗口保存前后等待全部注册 ApiWorker（包括子窗口）、后续任务与模态确认，再释放工程。等待期间暂停普通窗口输入，模态确认仍可操作；保存失败恢复原窗口状态。
- 修改 [export_tab.py](../../../../src/transbridge/ui/paratranz/export_tab.py)：导出进度改用 worker 信号更新 GUI，启动前保留局部所有者引用。
- 修改 [string_detail_dialog.py](../../../../src/transbridge/ui/paratranz/string_detail_dialog.py)、[string_dialog_lifecycle.py](../../../../src/transbridge/ui/paratranz/string_dialog_lifecycle.py)：标题栏、Esc、accept/reject/done 统一异步等待，保留首次 result。对话框持有计时器，等待 queued 结果及其触发的后续任务，安全跳过已删除的 worker。
- 修改 [string_navigation.py](../../../../src/transbridge/ui/paratranz/string_navigation.py)：主题刷新时缓存 QPen，修正 QPainter.setPen(QBrush) 类型异常，保持 paint 阶段不创建画笔。
- 新增 [sync_dispatch.py](../../../../src/transbridge/ui/tools/terminology/sync_dispatch.py)，修改 [sync_view.py](../../../../src/transbridge/ui/tools/terminology/sync_view.py)、[window.py](../../../../src/transbridge/ui/tools/terminology/window.py)：术语后台任务保有无 QWidget parent 的信号桥；queued activity 更新界面，关闭/销毁时解绑并拒绝晚到结果，晚到错误保留日志与 traceback。
- 修改 [_mixed_worker.py](../../../../src/transbridge/ui/tools/ai_translator/_mixed_worker.py)、[run_controller.py](../../../../src/transbridge/ui/tools/ai_translator/run_controller.py)、[run_view.py](../../../../src/transbridge/ui/tools/ai_translator/run_view.py)：业务结果使用 completed(dict)，恢复原生 finished()。成功、失败、取消均在原生 finished 后安排删除，日志收尾期间仍视为运行中；收尾异常保留原因，不发送第二个业务终态。
- 修改 [_translation_worker.py](../../../../src/transbridge/ui/tools/ai_translator/_translation_worker.py)：日志目录初始化纳入异常边界，逐个关闭资源并记录原始 traceback，错误不越过 run()。

## 回归文件

- 新增 `tests/ui/test_legacy_migration_safety.py`，扩展 `tests/contracts/io/test_migration_import.py`：真实异步版本切换/编辑、损坏 XML、多源原子失败、提交拒绝、三种 Strings 格式、无新增译文时的元数据兼容。
- 新增 `tests/ui/test_worker_shutdown_registry.py`：主/子窗口任务、queued 结果及串接任务、保存后任务、模态确认、失败恢复、finished 后仍未原生退出的阶段、隔离进程 20 轮 GC 和延迟删除。
- 新增 `tests/ui/test_paratranz_async_lifecycle_regressions.py`：进度线程归属、五种退出入口、queued 完成和后续同步、已删除线程、真实 QImage 导航绘制。
- 新增 `tests/ui/tools/terminology/test_sync_lifecycle.py`：真实 TaskRuntime 回调线程、正常成功/失败、关闭及直接销毁、晚到结果和解绑。
- 新增 `tests/ui/tools/test_legacy_ai_worker_lifecycle.py`：六个隔离子进程覆盖成功/失败/取消与慢速/失败日志收尾，两项真实线程测试覆盖日志初始化失败及逐个资源关闭。
- 更新旧 Mixed 业务信号引用：`tests/contracts/translation/test_mixed_report.py`、`tests/ui/tools/test_ai_translator_slices.py`、`tests/ui/tools/test_layout_stability.py`、`tests/ui/tools/test_mixed_completion_integrity.py`、`tests/ui/tools/test_workflow_progress.py`。

## 验证证据

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --no-cache --no-sync pytest tests/ui tests/contracts/io tests/converter -m 'not integration and not llm and not slow' -q -p no:cacheprovider --maxfail=10 --basetemp .tmp-worker-fixes-01a10fab/main/full-native
uv run --no-cache --no-sync pytest tests/contracts/translation/test_mixed_report.py -q -p no:cacheprovider --basetemp .tmp-worker-fixes-01a10fab/main/mixed-contract
uv run --no-cache --no-sync ruff check src tests
uv run --no-cache --no-sync ruff format --check src tests
git diff --check
```

- 最终离线集成：**1566 passed、1 skipped、2 deselected**，退出码 0，用时 212.77 秒。27636 条警告主要来自现有兼容 API 弃用提示。
- 混合报告契约：**8 passed**，退出码 0。
- 线程与关闭最终集中回归：**47 passed**，已包含在完整离线结果中，不累计。
- 全仓 Ruff 检查、1521 个文件格式检查、diff 空白检查通过。
- 独立复核暴露并修复 `isRunning=False` 但 `wait(0)=False` 的边界；集成过程中 registry 析构回调的访问违例已通过移除 destroyed 回调修正，最终完整重跑正常退出。
- Windows 沙箱的 pytest 临时目录权限限制通过提权运行本地测试解决，未安装依赖或修改锁文件；本任务专用临时目录在交付前清理。

## 兼容、职责与未验证项

无需数据或存储迁移。旧 AI 私有业务信号调整已同步全部仓库调用者。既有大文件中的修正限于线程/关闭原职责；完整迁移切片已提取，术语派发和关闭输入管理各自独立。前轮插件导入修复保留且由本轮集成复验覆盖；用户既有 `installer/setup.iss`、`pyproject.toml`、`uv.lock` 修改不属于本记录。

未运行真实网络集成、LLM、slow 测试、完整仓库测试或发行包构建；未取得朋友的原始/译文 ESP 做现场实物复验。旧 AI 生命周期问题已修，仍未确认当前默认界面可达。当前明确修复范围无未完成项；没有执行提交或发布。

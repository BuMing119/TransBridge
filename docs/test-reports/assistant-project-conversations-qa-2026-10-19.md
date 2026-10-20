# 助手会话工程绑定与窗口导航 QA

日期：2026-10-19 开始，2026-10-20 收尾（Asia/Shanghai）  
范围：[assistant-ui-remediation S24～S26](../../plans/assistant-ui-remediation/plan.md)、[FR13.5～FR13.8 / FR19.5～FR19.6](../requirements.md)、[ADR-018](../adr/018-project-session-persistence-v2.md)。  
状态：本地实现、定向验证与记录完成。完整助手 UI 组合保留一次 6 秒终态等待超时，后续有界诊断未复现作用域错误或请求丢失；不宣称整个组合全绿。详见验证限制。

## 验收与归属

本报告承接本会话已实现的会话绑定、分组浏览、执行前切换确认及外框/侧栏修正，再记录用户调用 bm-pilot 后补齐的验证和修复。以当前 Git 差异、未跟踪源文件及本会话工具证据确定归属；开工前已有的 `pyproject.toml`、`uv.lock` 版本调整，以及并行工作线的工程创建、ParaTranz、保存恢复和输入焦点修改不计入本次成果。共享文件仅归属本报告明确的符号和行为。没有提交、推送或发布。

验收覆盖：工程与版本固定绑定；预览不切工作台、不写回或恢复执行；统一校验发送、继续、重试、确认和撤销；取消与失败保留草稿；精准安装目标版本后才采集上下文；旧记录、失效工程、迟到回调、关闭与恢复；分组/焦点、两种主题、窄宽度、大字体、系统默认外框及窗口交互。

## 审查发现与处理

### Major：缺少 owner 的旧会话被预览误拒绝

- 触发：合法旧格式记录没有 `owner`；工作台已打开工程，用户点击这条未绑定历史。
- 证据：`GuiSessionCommandFacade.read_conversation()` 直接对 `SessionSnapshot.from_dto()` 的占位 owner 做严格比较，得到 `SESSION_OWNER_MISMATCH`；同一记录通过既有 `V2SessionSnapshotRepository.load()` 的 `owner_scope_missing` 兼容路径可恢复。
- 影响：用户能找到旧会话却无法阅读；前轮使用“有 owner、无工程”的样本没有覆盖真正的旧格式。
- 处理：已提取共享 `session_snapshot_for_context()`，只对真正缺少 owner 的合法旧记录作内存归一化，并保留原工程绑定。预览不迁移或写回文件；明确 owner 不因过时的降级诊断而被覆盖。v1、v3、当前格式缺 owner、显式 owner 不符及无写盘/激活回归通过。

### Major：会话目录首次读取阻塞 Qt 线程

- 触发：`PanelSessions.restore()/switch()/refresh()` 调用 `list_sessions()`；scope 缓存未命中或保存后记录签名变化。
- 证据：scope 校验会读取并验证完整 Session；注入 10 条各 30ms 的读取后，刷新耗时约 0.312 秒，全部调用发生在 Qt 主线程，排队的 0ms QTimer 直到函数返回才处理。
- 影响：大历史或慢磁盘下侧栏刷新、启动和切会话冻结；热缓存不能消除冷路径。
- 处理：已由 `SessionDirectory` 在后台读取目录和工程标签，Qt 只安装最新完整快照；合并连续刷新，保留旧列表和焦点，忽略关闭/过期结果。新测试阻塞实际冷读取和文件签名变化后的读取，证明 QTimer 仍可响应；缓存仍只作展示，不能替代记录与打开流程的执行校验。
- 同路径补漏：启动时工程仍在加载，不能消耗待恢复标记；安装完成后再选择当前范围会话。目录错误不误建新会话，迟到结果不能替换用户已明确新建的会话。目录、工作台导航、初次恢复、提示和执行范围组合 115 项通过，其中新增目录用例 14 项。

### 按最终截图改用系统默认外框

用户明确取消自绘深色轮廓，要求与主窗口一致的默认外框。最终实现移除窗口绘制层和圆角 mask，使用操作系统标题栏、窗口按钮、边框及缩放命中区；内部页首保留会话和模型信息。原生测试与离屏布局测试分开记录，不把 Qt 客户区截图或单个命中常量当作完整系统窗口交互证据。

### 联合回归中的键盘用例状态不一致

首次 518 项助手 UI/关闭组合得到 505 passed、1 failed、12 skipped。失败为 `test_keyboard_navigation.py` 的 Tab 顺序断言；紧前四文件组合可重复触发。运行时探针确认预期的“停止当前生成”按钮已隐藏、禁用，实际焦点为“发送消息”，`view._running=False`，Tab 正确跳过隐藏按钮。最初尝试等待会话和目录就绪，局部 32 项通过，但完整组合仍复现，因此不能归因于目录恢复。

根因在测试：`ConversationPresentation` 的常驻 150ms timer 通过 `refresh()` 按控制器实际状态设置 running；用例只修改 view，控制器仍为 IDLE，任一次正常刷新都会隐藏停止按钮。最终测试屏蔽启动模型的执行回调，通过真实控制器进入 THINKING，再主动刷新并断言停止按钮仍可用。保留生产定时器和真实投影，不依赖延时或放宽键盘顺序断言；最终复验见下文。

## 验证命令与结果

环境：仓库 uv 环境，Python 3.12.12、pytest 9.1.1、PyQt6；使用 `--no-sync --no-cache` 保持现有依赖和锁文件。Windows 对部分 pytest 临时目录需要工具权限扩展，所有目录均为本任务新建，并在收尾核对归属后清理。

### 补录前已有证据

- 前轮最终 17 个相关 UI/导航测试文件共 174 passed，包含窗口形状、主题实际像素、180/200/260px 侧栏和 10/14pt 字体、工程确认与执行入口；该数字不与本轮重复用例相加。
- 本轮修复前先运行下列后端/工程链路组合：164 passed，退出码 0；此前单独记录过的 Windows catalog 原子替换瞬时失败在本次该组合未复现。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --no-sync --no-cache pytest tests/application/sessions tests/persistence/v2/test_project_open_records.py tests/persistence/v2/test_project_open_activation.py tests/persistence/v2/test_assistant_session_migration.py tests/ui/test_assistant_project_navigation.py tests/ui/test_project_open_operation.py tests/ui/test_project_open_guards.py tests/ui/test_main_window_coordinators.py tests/smart_assistant/test_request_execution.py -q -p no:cacheprovider -o faulthandler_timeout=30 --basetemp .tmp-pilot-closure-root/backend
```

### 最终结果

后端和工程打开最终复验：上方同一组合改用 `--basetemp .tmp-pilot-closure-root/backend-final-approved`，170 passed，退出码 0，3 条既有 SWIG 弃用警告。首次受限运行无法读取其新建的 pytest 临时目录，以 `PermissionError: WinError 5` 退出；在正常文件权限下重跑后全过，没有为临时目录权限问题修改产品代码。

独立只读复审：`test_gui_session_scope.py`、`test_persistence_adapter.py`、`test_request_session_scope.py`、`test_execution_entrypoint_scope.py`、`test_session_workspace_navigation.py` 共 89 passed，未发现新的可复现作用域问题。重复测试不与最终联合回归相加。

Windows 原生验证：15 passed，退出码 0，3 条既有 SWIG 弃用警告，150% DPI。包含 12 个仅 Windows 执行的用例和 3 个窗口契约用例；八方向缩放命中、标题拖动命中、系统最小化/最大化/恢复/关闭命令、最小尺寸及关闭复用均通过。仅操作自建测试 HWND；没有进行人工鼠标拖动或跨显示器 DPI 验收。

```powershell
$env:QT_QPA_PLATFORM='windows'
uv run --no-sync --no-cache pytest tests/ui/tools/smart_assistant/test_window_native_interaction.py tests/ui/tools/smart_assistant/test_window_frame.py -q -p no:cacheprovider -o faulthandler_timeout=30 --basetemp .tmp-pilot-native-agent
```

全库静态检查通过，最终格式检查覆盖 1948 个文件（共享工作区另有并行工作线增加文件，不计为本任务产出）：

```powershell
uv run --no-sync --no-cache ruff check src tests
uv run --no-sync --no-cache ruff format --check src tests
git -c core.safecrlf=false diff --check
```

全部助手 UI 与关闭契约采用以下组合：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --no-sync --no-cache pytest tests/ui/tools/smart_assistant tests/ui/test_ai_task_shutdown.py tests/ui/test_runtime_task_shutdown.py tests/ui/test_worker_shutdown_registry.py -q -p no:cacheprovider -o faulthandler_timeout=30 --basetemp .tmp-pilot-closure-root/ui-controller-state-approved
```

修正真实控制器测试状态后，键盘用例已在完整组合通过；该轮结果为 505 passed、1 failed、12 skipped，退出码 1。12 项 skip 均为需要 Windows 平台的原生窗口测试，已另行验证。唯一失败变为 `test_new_inputs_interrupting_routing_do_not_drop_the_earlier_batch` 的 6 秒终态等待超时；前两轮完整组合该用例通过。现场三条输入、两批路由均已持久化并 applied，前两请求完成，第三请求已接纳执行轮但尚未调用 provider；不能把该现象称为输入丢失。

对此用例及相邻快速输入的两轮定向复验均明确得到 2 passed：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
uv run --no-sync --no-cache pytest tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py::test_new_inputs_interrupting_routing_do_not_drop_the_earlier_batch tests/ui/tools/smart_assistant/test_request_lifecycle_panel.py::test_three_rapid_inputs_are_all_saved_routed_and_answered -q -p no:cacheprovider -o faulthandler_timeout=30 --basetemp .tmp-pilot-routing-review-1
```

第二轮使用独立目录 `.tmp-pilot-routing-review-repeat`。后续最多 8 轮、每轮 2 例的诊断中，16 份落盘状态均为三个请求 completed；由于该循环最终终端输出被截断，不将其记为精确的“16 passed”。

原失败现场的第三条输入接收时间为 `13:51:05.367764`，第三请求获选执行为 `13:51:11.024877`，已消耗约 5.66 秒，接近 6 秒总等待。证据更符合等待预算耗尽，具体延迟来源未确定；没有通过延长超时、吞异常或猜测修改执行门来制造全绿。该次超时作为剩余验证限制，后续若稳定复现，应对第三轮准备/交付作独立时序诊断。

键盘竞态修复后的全部前序 17 个文件组合另有 186 passed；不与完整组合相加。计划、ADR、QA 与增量记录中的 24 个相对链接核对通过，最终差异检查通过；任务临时目录已核对归属并清理，既有历史增量未修改。

## 职责与兼容性复核

- 新职责分别落在会话预览/导航、执行门、归属缓存、后台目录、工程导航适配、状态提示及分组/行模块；窗口外框交给系统，领域与持久化层不引入 PyQt。
- 当前已改生产文件中仅 `ChatWidget` 超过复核阈值（549 行、36 方法）；本次只接入既有请求入口的统一校验，没有增加目录/切换/窗口职责。风险仍是已有 UI 与请求生命周期耦合，后续新增责任前须沿输入或渲染边界拆出完整切片并回归。
- 不升级 Session schema，不批量重绑旧历史；保留无工程通用会话。读取、恢复与执行是不同合同；工程失效不影响可读历史，不能使其落到当前工作台执行。
- 新版兼容旧会话目录；旧版目录解码器不认识新增可选绑定字段，降级需要先备份并兼容转换目录，不能声称旧版可直接读取新版目录。本次不降级或批量改写用户数据。

## 未覆盖范围

不调用真实 LLM 或 ParaTranz，不评估模型语义效果、网络账户、安装包发布及无关全库业务。Windows 原生自动测试仅作用于自建测试窗口，不操作用户当前工程；它不替代人工高 DPI 多显示器、触控或屏幕阅读器验收。最终遗留问题以审查结论和执行结果为准。

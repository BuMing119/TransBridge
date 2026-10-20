# Story-04：开始中心、启动策略、最近/恢复与插件建项 UI

- **所属计划**：[guided-ui-workflows](../plan.md)
- **状态**：草稿
- **实现对齐**：2026-10-20，多选建项与单次确认；[QA 证据](../../../docs/test-reports/guided-multi-plugin-project-qa-2026-10-20.md)
- **需求**：FR26.2～FR26.4、NFR1.6
- **依赖**：S01 UX 合同、S02 建项用例、S03 任务/恢复投影

## 目标与验收边界

原样承接 plan S04：实现任务导向开始中心和可返回编辑的建项 draft，同时保留 FR8.4 正常自动恢复。开始中心是 Shell 的展示上下文，不是关闭工程、停止任务或清空 dirty 的生命周期命令。

## 启动状态决策

`StartDestinationState` 由公开 projection 派生：

- `RESTORING_LAST`：存在合法 active reference，显示非模态恢复状态；成功导航 Workbench。
- `START_CENTER_EMPTY`：无 active reference，显示首插件主动作。
- `START_CENTER_RECOVERY_FAILED`：自动恢复失败，保留诊断、最近工程和修复入口。
- `START_CENTER_USER_REQUESTED`：用户主动返回；当前工程仍保持 active，可一键返回 Workbench。

缺 ParaTranz token、LLM、Embedding 或其他可选服务只影响对应 action enabled reason，不参与上述启动目的地判定。现有 `ToolWindows.show_config()` 不得在 MainWindow 构造阶段强制执行。

## 建项 draft

冻结/可替换的 `GuidedProjectDraftState` 保留单源字段 `source_path`，以 `additional_source_paths` 接收其余原始插件，通过 `source_paths` 返回完整顺序；同时保存工程名、默认版本名、迁移来源、高级选项、诊断、preview token 和 revision。`begin(path)` 兼容旧单源入口，`begin_sources(paths)` 初始化多源草稿；新草稿递增 revision，避免编辑后重选的展示被旧 revision 拒绝。

Coordinator 只处理 draft；一次“确定”通过现有后台 dispatcher 连续调用 S02 prepare/commit。编辑影响 fingerprint 的字段后旧 preview token 失效，in-flight guard 拒绝重复提交。全部原始插件映射到 S02 `source` 和 `additional_sources`，已有译文单独保存在 `migration_sources`。

多选使用 `QFileDialog.getOpenFileNames`；工程名默认取首个插件的 stem，允许修改。单源继续显示路径，多源显示数量及有界滚动列表，文件项提示和无障碍文本提供完整路径。取消选择不覆盖当前草稿；失败保留全部输入并显示失败文件。空选择本身不创建空工程，空工程仍由高级入口显式发起。

## 交互顺序

```text
shell starts
  -> attempt FR8.4 authoritative restore
  -> success: Workbench
  -> no target/failure/user return: StartCenter

choose one or more plugins -> edit draft / optional migration sources
  -> summary -> one confirm
  -> background prepare_create -> background commit_create
  -> prepare complete source hydrations in worker -> atomically replace Project view -> Workbench
  -> failure: keep all draft inputs and diagnostic
```

本地工程优先来自只读 Project catalog，legacy 环境回退 workspace；恢复任务来自 S03 的只读恢复投影。资源缺失的工程项不可激活，不展示“继续”。

## 实施步骤

1. 用 S01 wireframe 定义 StartCenterViewPort、StartCenterViewState 和稳定 intent。
2. 把启动目的地判定从 `MainWindow.__init__` 的副作用拆到 shell presenter；先移除无 token 强制配置模态行为。
3. 接入 FR8.4 current project opener，分别处理 success/no-target/diagnostic，不改变 lifecycle 服务。
4. 实现 recent/recovery projection 和 resource revalidation；禁止每次 paint 访问磁盘。
5. 通过 guided coordinator 接入 S02 facade；提交前开启工作台切换保护，在后台核验旧视图、提交工程并消费全部独立基线来源的 hydration（含未折叠的迁移来源），用 `prepare_project_slots` 保留 EntryKey、字符串 ID、format、源快照并应用正式版本的译文。确认词条覆盖完整后，通过既有 `install_project_open` 一次替换工程信息与全部来源；空工程也必须替换为空集合。不得向旧工程逐项追加或为了显示而再次解析。旧工程未保存时保留原内容；提交后的视图准备失败须保留正式记录、禁用不完整视图编辑并提示重新打开。
6. 实现“返回开始页/返回当前工程”，明确不触发 close/save/cancel；dirty 状态继续在工程栏显示。

## 文件与测试

- 界面：`src/transbridge/ui/shell/start_center.py`、`start_center_controller.py`、`start_center_landing.py`。
- 草稿：`src/transbridge/ui/coordinators/guided_project_coordinator.py`；通过 `project_creation_operation.py` 复用 `ui/source_hydration.py`、工程视图安装事务与 MainWindow 后台 dispatcher。
- 验证：`tests/ui/test_start_center_multi_plugin.py`、`test_start_center_guided_project.py`、`test_start_center_draft_focus.py`、`test_start_center_responsive_layout.py`、`test_start_center_primary_contrast.py`、`test_project_source_hydration.py`。

重点测试：首次无工程、正常恢复、损坏/缺失 active、用户返回、dirty、后台任务运行、无 token、本地解析/保存/写回、draft 返回编辑、重名/解析/UoW 失败、迟到 restore event。多选 ESP/ESM/ESL、一份请求/一次提交、全部 collection 加载、取消/重选、失败保留参数、字符串 ID 保真和 50 个插件列表滚动继续保留；`tests/ui/test_project_creation_transition.py` 额外验证已有工程下创建单源/多源/空工程、独立迁移来源、失败保留旧内容及新工程立即保存。

原生 Windows 文件窗口的 Ctrl/Shift 多选和真实大插件加载性能尚未手工验证；离屏 Qt 测试只证明接线、状态及布局，不代表这些验证已完成。

## 回退与边界

开始中心可以按 feature flag 回退为旧 Workbench 空状态，但 S02 权威建项不能回退到 legacy 写路径。配置窗口仍可从设置或相关 disabled reason 打开。用户返回开始中心不是项目关闭；若未来需要“关闭并返回”，必须调用既有 dirty-decision lifecycle intent。

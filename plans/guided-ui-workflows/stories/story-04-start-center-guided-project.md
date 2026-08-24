# Story-04：开始中心、启动策略、最近/恢复与插件建项 UI

- **所属计划**：[guided-ui-workflows](../plan.md)
- **状态**：草稿
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

计划新增冻结/可替换的 `GuidedProjectDraftState`：主插件路径、建议/编辑后的工程名、默认版本名、迁移来源列表、高级解析选项、识别状态、诊断、preview token 和 revision。

Presenter 只处理 draft；确认时调用 S02 prepare/commit。编辑任何影响 fingerprint 的字段后旧 preview token 失效。重复点击确认由 revision + in-flight guard 合并为一次 command。

## 交互顺序

```text
shell starts
  -> attempt FR8.4 authoritative restore
  -> success: Workbench
  -> no target/failure/user return: StartCenter

choose plugin -> edit draft -> optional migration sources
  -> prepare_create -> summary
  -> back: preserve draft
  -> commit_create -> projection event -> Workbench
```

最近工程来自 workspace/project projection；恢复任务来自 S03 RecoveryCatalog。资源缺失的最近项可显示修复/移除入口，但不能显示“继续”。

## 实施步骤

1. 用 S01 wireframe 定义 StartCenterViewPort、StartCenterViewState 和稳定 intent。
2. 把启动目的地判定从 `MainWindow.__init__` 的副作用拆到 shell presenter；先移除无 token 强制配置模态行为。
3. 接入 FR8.4 current project opener，分别处理 success/no-target/diagnostic，不改变 lifecycle 服务。
4. 实现 recent/recovery projection 和 resource revalidation；禁止每次 paint 访问磁盘。
5. 实现 guided draft presenter，并接入 S02 facade；成功只等待 projection event 后导航。
6. 实现“返回开始页/返回当前工程”，明确不触发 close/save/cancel；dirty 状态继续在工程栏显示。

## 文件与测试

- 新增：`src/transbridge/ui/shell/start_center.py`
- 新增：`src/transbridge/ui/coordinators/guided_project_coordinator.py`
- 修改：`ui/main_window.py`、`ui/app.py`、`ui/coordinators/project_coordinator.py`、shell composition
- 新增：start destination、recent/recovery、guided draft、no-token local journey 和 duplicate intent tests

重点测试：首次无工程、正常恢复、损坏/缺失 active、用户返回、dirty、后台任务运行、无 token、本地解析/保存/写回、draft 返回编辑、重名/解析/UoW 失败、迟到 restore event。

## 回退与边界

开始中心可以按 feature flag 回退为旧 Workbench 空状态，但 S02 权威建项不能回退到 legacy 写路径。配置窗口仍可从设置或相关 disabled reason 打开。用户返回开始中心不是项目关闭；若未来需要“关闭并返回”，必须调用既有 dirty-decision lifecycle intent。

# Story-07：其余超重组件与 AppContext 兼容面收敛

- **所属 Plan**：[UI 展示层模块化与上帝窗口拆分](../plan.md)
- **状态**：已完成（2026-08-19）
- **优先级**：P1
- **前置依赖**：S03～S06 已迁移主要消费者
- **下游**：S08

## 目标

清理主切片迁移后剩余的超重 UI、隐式依赖和 `AppContext` 广域依赖，让所有残留例外都有明确责任与退出条件。

## 原始验收标准

- [x] 复核并处理仍越过 hard gate 的 UI 模块，例如 ParaTranz detail、translation progress、download card 等；没有无 owner 的豁免。
- [x] `ui/context.py` 只保留 ADR-018 定义的 projection/compatibility facade；纯 DTO/helper 迁到内聚模块，新 Presenter 不依赖完整 AppContext。
- [x] 不用共享可写状态 mixin 降行数；每个新模块有单一职责和单向依赖。
- [x] 全仓跨组件私有访问、parent lookup、View 直连 infra/persistence 清单归零，或有时限豁免与承接 Story。
- [x] 旧 compatibility API 只在调用方迁完且 V2 门禁通过后删除；未迁消费者不被静默破坏。

## 当前重点

- `AppContext(QObject)` 同时包含 signals、filter/label/selection、legacy collection slots、workspace/project projection、safe mutate 和权限 helper；ADR-018 已决定其 projection/compatibility 身份，本 Story不重做状态模型。
- S04 后仍需复核 `download_card.py`；S05 后复核 `_translation_progress_window.py`；ParaTranz `string_detail_dialog.py` 按 View/form 与 command/result mapping 拆分。
- 规模审计覆盖所有 `src/transbridge/ui/**/*.py`，不是只处理当前列出的文件。

## 计划边界

- 新 Presenter 依赖 feature-specific `ProjectProjectionPort`、`SelectionPort`、`LabelCommandPort` 等，不接收完整 AppContext。
- `CollectionSlot` 等纯 DTO 只有在无 Qt/owner 语义时才迁到邻近 projection/contracts 模块，并从旧路径重导出。
- `safe_mutate` 的主线程语义若仍有消费者，保留为 compatibility adapter；不能只为降方法数拆成共享 `self` mixin。
- compatibility inventory 记录 `symbol -> consumers -> replacement -> removal gate`。

## 实施步骤

1. 在 S03～S06 后重跑尺寸/依赖审计，以真实剩余问题而非 2026-08-19 快照确定顺序。
2. 对每个 hard-gate 模块识别独立 form/view、presenter/controller、mapping；先补 characterization，再迁职责。
3. 为新 UI 切片定义窄 port，并把调用方从完整 AppContext 迁走；每批迁移后验证 projection owner 未变化。
4. 把纯 DTO/mapping/helper 移入内聚模块并保留兼容 import；Qt signals/projection facade 继续由 AppContext 承担。
5. 清理 parent lookup、跨私有访问、View-infra import 和到期豁免；无法清理项创建明确承接 Story/blocker。
6. 复核所有新模块没有 import cycle、共享可写 singleton、mixin 规避或无释放订阅。

## 边界与错误

- `AppContext` property setter 可能同时兼容 legacy 和 V2 projection；删除前必须分别覆盖两种模式。
- projection callback 跨线程时继续排队到 GUI thread；纯 Presenter 不直接操作 QObject。
- 权限 helper、filter/label setter 的异常和 signal 次数保持现状，不能因 facade 变薄重复 emit。
- 对话框取消不能提交 command；partial operation/result 的显示与 S01 等价。
- 无法证明仓外消费者不存在的符号只 deprecate/重导出，不直接删除。

## 文件变更

- 修改 `src/transbridge/ui/context.py`
- 按最终 inventory 修改 `ui/paratranz/string_detail_dialog.py`、`ui/tools/ai_translator/_translation_progress_window.py`、`ui/workbench/cards/download_card.py`
- 新增内聚 DTO/mapping/ports 文件，实际路径由依赖归属决定
- 修改 `scripts/audit_ui_modularity.py` 和相关 UI/contract/compatibility tests

## 测试与建议命令

- `pytest tests/ui tests/contracts/ui -q`
- `pytest tests -k "context or projection or paratranz or translation_progress or download_card" -q`
- 审计脚本全仓运行；legacy/V2 双模式、跨线程 projection、取消/失败/关闭和 public import tests。

## 风险与回退

最大的风险是误删兼容入口或改变 ADR-018 owner。按 consumer batch 迁移；旧 facade 委托新 port，直到最后一个消费者和 V2 门禁通过。单组件拆分可切回旧 composition，不回滚已经稳定的 ports。

## 未决问题

- 最终剩余模块清单由 S07 开始时的审计生成；本文件列举项不是允许忽略其他 hard-gate 文件的白名单。

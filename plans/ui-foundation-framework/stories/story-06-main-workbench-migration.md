# Story-06：Shell、开始中心与 Workbench 关键路径迁移

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：已完成（2026-08-24）
- **优先级**：P0
- **前置依赖**：S04 公共组件/适配器、S05 设置入口
- **下游依赖**：S07、S08、S09

## 目标

迁移用户最常驻的 Shell、Start Center 与当前 Workbench composition，验证 Palette-first 在真实大表和业务状态色上的性能与状态保真，同时保持 FR26 的导航、引导与任务上下文。

## 原始验收标准

- [x] MainWindow 壳、菜单/状态栏、Start Center、Guidance、Task Center、Command Palette/Context Help 与当前 Workbench/Step2/project bar/workflow slices 使用语义/业务令牌。
- [x] Step1/Step3、project prompt overlay 与旧 operation cards 先经过 production reachability 审计；不可达兼容模块只登记 owner/删除门禁，不得为了主题迁移重新接回当前界面。
- [x] Stage、标签、隐藏/锁定、已翻译/未翻译、focus/filter 等状态在浅/深主题均清晰，关键状态具有文字/图标/边框等非纯颜色信息。
- [x] Step2 大表格主题切换保持 row identity、选择、滚动位置、编辑内容和增量 render generation；不得全量重建业务数据。
- [x] 迁移文件不再出现裸主题颜色；仍保留的结构 QSS 有审计豁免原因。
- [x] 主窗口 geometry 的历史 `QSettings` 与 UI preference 权威状态分离，主题不得从 QSettings 读取。
- [x] 窗口打开 P95 和主题切换 heartbeat 满足 NFR1.4。

## 受影响数据流

```text
ThemeService palette change
  -> MainWindow/Start Center/shell standard widgets inherit automatically
  -> Workbench/Guidance/Task Center/Command Palette inherit automatically
  -> Step2 theme adapter refreshes existing item brushes/delegate cache
       (no collection/projection query, no row regeneration)
  -> Stats/domain adapters refresh visible semantic brushes
```

当前 Step2 的表格装填和编辑已经有 generation/queued batch 约束。主题 revision 不是数据 revision：不能触发 `_populate_table()`、修改 `_row_map`、重置筛选或创建新 `QTableWidgetItem`。如果 item foreground/background 仍必须显式设置，则只遍历当前已物化 items；大表性能不足时应改为 delegate 按 DomainBrushes 绘制，而不是重新装填。

## 状态映射

- Stage：domain token + 文本 label；保持现有 stage 数值/业务含义，不从 UI 主题反向修改 `STAGE_COLORS` 的领域数据。
- 隐藏/锁定：背景/边框之外必须有文字、图标或独立列状态；locked 空译文发布门禁不变。
- 已翻译/未翻译：文本/状态 label 与颜色共同表达。
- 标签：用户自定义颜色需要通过可读前景/背景适配器处理；保留原始用户色作为数据，但 theme adapter 负责对比度和选中/focus 状态。
- focus/filter：使用 semantic focus/selection；不能与“标记/标签”业务状态混淆。

## 实施步骤

1. 从 S01 inventory 为 MainWindow、shell、guidance 与 Workbench 建立逐文件迁移表，先标注每个颜色属于 semantic/domain/user-data/structure，并记录 compatibility 模块是否从生产 composition 可达。
2. 让 MainWindow composition、StartCenterWidget、ShellIntentComposition 下的可视 root、WorkbenchWidget/Step2 facade 与 ProjectBar 接收窄 ThemeView/UiFoundation；不要从模块全局或 QObject parent 链查找。
3. 移除 `_ApiStatusIndicator` HTML 里的 green/red/#888，改为 StatusBadge/semantic tokens，并保留文字“正常/异常/请求中”。
4. 迁移 Start Center、GuidanceBanner、Task Center、Command Palette/Context Help、project bar、workflow actions、warning overlay、stats 和 filter chips；结构 QSS 集中调用 S04 helper。
5. 重构 Step2 显式 QColor 路径：domain brushes 在 revision 变化时刷新 visual role，不修改 item UserRole/entry identity/selection/edit buffer/scroll。
6. 标签用户色通过 `resolve_user_color(raw_color, surface, state)` 适配；非法/低对比颜色有稳定回退和非颜色 label。
7. 保留 QSettings geometry/state 兼容，但加边界注释/测试证明 ThemeService 不读取它；未来统一窗口布局配置另立需求。
8. 更新 inventory 状态和豁免；新增基准对比 S01，验证窗口打开、切换和大表 heartbeat。

## 边界与错误处理

- 正在编辑单元格时切换：editor widget 保持焦点/文本/selection，palette 自动更新；不得提交或取消编辑。
- 增量装填途中切换：新 item 使用新 snapshot；已装填 item 收敛到相同 revision；旧 generation callback 仍由原数据 generation 规则管理。
- 用户标签颜色非法：不修改持久化标签数据，UI 使用 neutral fallback 并提示可修正。
- compatibility light 与 default light 视觉有差异：功能/state contract 优先，差异记录而非恢复散落 QSS。
- theme adapter 异常：表格保留 last-good brushes，数据/选择不变。

## 测试策略

- MainWindow/Workbench 浅、深、system 的真实 widget smoke。
- Start Center/Workbench 两个启动目的地，以及 Guidance、Task Center、Command Palette/Context Help 的浅、深、system 状态矩阵。
- FR26 J01/J02/J08/J09 在主题切换前后 intent、D/M/N、焦点、取消与返回上下文不变。
- Step2 固定大集合：切换前后 entry keys、row identity、selection、scroll、edit text、render generation 相等。
- 增量装填/筛选/聚焦/标签编辑与主题切换 race。
- 状态可辨识：文字/icon/accessible description，不只比较像素颜色。
- audit：迁移文件零裸主题颜色；结构豁免含 owner/reason/removal gate。
- performance：窗口打开回归、250ms switch、200ms heartbeat、无 model rebuild counter。

## 文件变更清单

- 修改 `src/transbridge/ui/main_window.py`
- 修改 `src/transbridge/ui/shell/*.py`
- 修改 `src/transbridge/ui/guidance/*.py`
- 修改 `src/transbridge/ui/workbench/*.py`
- 仅在 reachability 审计证明仍可达时修改 `src/transbridge/ui/workbench/cards/*.py`、`step1.py`、`step3.py`
- 新增 `tests/ui/test_workbench_theme_migration.py`
- 新增 `tests/ui/test_shell_theme_migration.py`
- 更新 `plans/ui-foundation-framework/migration-inventory.md`

## 风险与回退

Step2 是性能和数据状态最高风险点。迁移应先以 compatibility light 验证数据保真，再启用 dark；若显式 item brush 更新超预算，可仅回退该表到 last-good/compat adapter，不能回退全局 ThemeService 或重建业务表。

## 未决问题

- Stage 颜色常量目前位于 converter domain module。是否把“状态色”完全移出 domain 留给实现时按非 UI 调用方审计决定；最低要求是 UI 不再直接依赖具体 hex，Stage label/ID 保持领域权威。

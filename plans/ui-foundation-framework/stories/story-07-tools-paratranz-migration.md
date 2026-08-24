# Story-07：Smart Assistant、AI Translator、操作计划与 ParaTranz/FOMOD 视觉迁移

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：草稿
- **优先级**：P0
- **前置依赖**：S04 公共适配器、S06 Shell/Workbench 迁移模式；FR26 S08/S09 交接已完成
- **下游依赖**：S09 最终门禁

## 目标

迁移自定义 QSS、富文本、Delegate 和业务报告最集中的 Smart Assistant、AI Translator、Operation Plan、ParaTranz 与 FOMOD，同时证明主题变化不污染运行中的任务、计划 draft 和表单状态。

## 原始验收标准

- [ ] Smart Assistant 的 message bubble、thinking、tool/plan card、session list、task monitor、quick actions 与 Markdown 使用 Foundation snapshot。
- [ ] AI Translator 的配置、批次、进度、预览和报告窗口使用语义/业务令牌，成功/失败/警告/差异在两种主题中可读。
- [ ] ParaTranz tabs、dialogs、Stage 颜色和 `_NavItemDelegate` 使用同一 domain tokens；Delegate 不在每次 paint 解析颜色字符串。
- [ ] 非模态 Operation Plan、预检/结果状态和 FOMOD panel 使用同一 semantic/domain tokens；主题变化不得重新生成 draft、confirm token、preflight、Task Run ID 或产物路径。
- [ ] 已打开对话框与后续新建对话框在一次 revision 后一致；销毁窗口不泄漏 subscription。
- [ ] 主题切换不影响正在运行的 Task、输入内容、选择、报告数据或网络请求。
- [ ] 上述表面迁移后的裸颜色/QSS 清单归零或只有带理由的结构豁免。

## 子系统边界

### Smart Assistant

- `message_bubble.py` 的 Markdown widget 由 S04 adapter 注入 theme；全局 `_RENDERER` 不能缓存某次主题对象而永久失效，应缓存 Qt-free parser/renderer 或按 fingerprint 更新 render theme。
- `session_list_widget.py` 的 `_COLORS` 和动态整段 QSS 改为 palette/property/公共 component；active/hover 仍由 widget state 驱动，不为每次 mouse move 重编译 QSS。
- `task_monitor.py` 的 `_STATUS_COLORS` 由 DomainTokens 取值，状态 label 保留。
- `tool_card.py`、`plan_card.py`、`thinking_indicator.py`、`quick_actions.py` 迁移结构/颜色所有权并保持现有执行/取消 signal。
- `chat_widget.py` 的 auto mode QSettings 不属于主题；本 Story 不擅自改变业务配置，但 inventory 必须标记给统一配置后续处理。

### AI Translator

- 配置、batch、progress、preview、report/history 等窗口的 success/error/warning/muted/diff 使用 semantic/domain tokens。
- 规则、模型、范围等业务值不因 theme revision 重建或保存。
- 进度 worker 和 TaskRuntime 信号仍是数据源；主题仅改变呈现，不发起/取消任务。
- preview/report table 的 cell brush 在 revision 改变时更新可见视觉，不修改报告对象、candidate 或 apply selection。

### ParaTranz

- `_strings_common.py` 可继续权威保存 Stage labels，但 UI color 由 DomainTokens 适配。
- `_NavItemDelegate.paint()` 在构造/主题 revision 时缓存 QBrush/QPen，paint 只 lookup；不得每帧 `QColor("#...")`。
- strings、terms、members、mails、issues、overview、history、files、export、contribution 和 config dialogs 逐一按 inventory 迁移。
- 网络 worker、token/config、selection 和 pagination 不进入 ThemeService。

### Operation Plan / FOMOD

- `ui/operations/plan_dialog.py` 只渲染既有 `OperationPlanViewState`，主题 revision 不调用 presenter 的 `open/edit/preflight/confirm`，也不改变 session owner/revision/request digest。
- 上传、下载、写回与 FOMOD 共用视觉 primitive，但继续保持四条 application use case 和 typed request；不得为主题迁移建立通用执行器。
- `ui/tools/fomod/fomod_panel.py` 的输入、建议输出、校验与运行状态使用 domain tokens；切换主题不覆盖用户手工输出、不重新选择归档、不触发文件访问。

## 事件顺序

```text
ThemeService emits revision N
  -> standard controls inherit palette
  -> each live subsystem root receives one adapter callback
       -> replace cached DomainBrushes / rich-text theme
       -> viewport.update() or targeted widget.update()
       -> no data reload, no network call, no Task command
  -> newly opened dialog reads snapshot N at construction
```

根组件优先持有一个 subscription 并向内部适配器分发，避免每个小 label 都订阅 ThemeService。对话框关闭时 root subscription 释放。

## 实施步骤

1. 依据 inventory 为五个表面建立逐文件 owner，按 Smart Assistant → AI Translator → Operation Plan → ParaTranz → FOMOD 顺序迁移；每个表面完成后独立运行回归。
2. 先替换静态颜色常量和颜色型 QSS，再迁移动态 hover/active/status；结构 QSS 通过 S04 helper。
3. 接线 Markdown/rich-text theme，验证长文本 fallback、代码块、链接和表格在两主题中可读且不重复 parse theme CSS。
4. 对 AI/ParaTranz 表格建立 targeted visual refresh；断言数据对象、selection、scroll 和 edit buffer 不变。
5. 重构 `_NavItemDelegate` 和其他 paint 路径为 revision cache；使用 viewport update，不重建 model/list。
6. 给运行中的 fake Task/worker/network request 加主题切换集成测试，记录调用计数应为零变化。
7. 给 operation plan 加 draft/request digest/confirm token/preflight/Run ID 不变断言，给 FOMOD 加输入/输出/文件访问计数不变断言。
8. 验证打开/关闭 100 次 panel/dialog 后 subscription 与 RSS 回到预热基线；更新 inventory 和豁免。

## 边界与错误处理

- 流式 Markdown 正在追加时切换：后续 chunk 与既有内容使用同 revision 或在一次最终 render 收敛，不能丢文本/重复消息。
- 工具确认卡正在等待用户：按钮 enable/权限/step ID 不变。
- AI/ParaTranz 网络回调与 theme callback 同一 event-loop turn：先后顺序不得影响数据结果；视觉 refresh 读取最新 snapshot。
- 用户自定义标签/远端 Stage 未知：使用 neutral domain fallback + 原始文字/数值，记录一次诊断。
- 对话框对象已 deleteLater：subscription owner guard 忽略，不访问 C++ deleted object。

## 测试策略

- Smart Assistant：streaming、Markdown、tool/plan、session active、task status、panel close/reopen。
- AI：配置 dirty、running progress、preview selection、report/history content hash 在切换前后相等。
- ParaTranz：Delegate paint call 计数/无颜色解析、tabs/dialogs、network request count 不变。
- Operation/FOMOD：plan session/digest/token/preflight/Run ID、归档输入输出及文件访问计数不变；关闭 non-modal plan 仍保持零业务副作用。
- FR26 J04～J07 固定旅程在 theme matrix 下保持原 D/M/N、焦点、取消与结果返回上下文。
- 全部：existing/new dialog revision 一致、listener 释放、audit 清单、浅/深/system。
- 性能：代表性三子系统同时打开的 250ms/heartbeat/RSS 门禁。

## 文件变更清单

- 修改 `src/transbridge/ui/tools/smart_assistant/*.py`
- 修改 `src/transbridge/ui/tools/ai_translator/*.py`
- 修改 `src/transbridge/ui/paratranz/*.py`
- 修改 `src/transbridge/ui/operations/*.py`
- 修改 `src/transbridge/ui/tools/fomod/*.py`
- 修改/适配 `src/transbridge/infra/markdown_renderer.py`
- 新增 `tests/ui/test_tool_theme_migration.py`
- 新增 `tests/ui/test_paratranz_theme_migration.py`
- 更新 `plans/ui-foundation-framework/migration-inventory.md`

## 风险与回退

动态 QSS 最密集的 SessionList 和复杂表格最易产生 repaint 回归；Operation Plan/FOMOD 则最容易因错误接线重复预检或副作用。按子系统 root 独立 adapter 接线；某子系统失败可暂时使用 compatibility adapter，不回退已经稳定的全局 palette 和其他子系统。

## 未决问题

- MarkdownRenderer 长期是否应从 `infra/` 移到 UI 包不在本 Story 做目录重构；本 Story只修正依赖注入方向并保持公开导入兼容。

# Story-05：主题设置、预览与恢复 UX

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：草稿
- **优先级**：P1
- **前置依赖**：S03 ThemeService、S04 公共组件/适配器
- **下游依赖**：S06、S09

## 目标

让用户安全选择主题，并明确区分“预览”“当前会话已应用”和“偏好已持久化”。

## 原始验收标准

- [ ] 通用设置入口提供 system/light/dark、当前 effective scheme、即时预览、应用、取消和恢复默认。
- [ ] 预览只使用隔离 preview widget/snapshot，不修改业务窗口或持久化配置；取消后无残留 revision/cache/listener。
- [ ] 应用成功后当前和新窗口一致；重复应用当前值幂等。
- [ ] 写入失败时用户可选择保持本次会话主题或恢复持久化主题，提示不泄漏底层路径。
- [ ] 未知 theme ID、Provider 移除和系统 scheme unknown 有稳定回退说明。
- [ ] 首期界面不出现导入、编辑、市场或任意皮肤入口，但展示 Provider 元数据的控件边界可复用。

## 交互状态

设置对话框维护独立 `UiSettingsDraft`：

```text
persisted_preference  # 打开对话框时的配置值
active_preference     # 当前 ThemeService 值
draft_preference      # 用户尚未应用的选择
preview_snapshot      # ThemeService.preview 返回，不递增全局 revision
dirty                 # draft != persisted
```

状态流：

```text
open -> edit draft -> isolated preview
  -> cancel: dispose preview, active/persisted unchanged
  -> apply:
       ThemeService.set_preference(draft, persist=True)
       -> applied+persisted: close/refresh baseline
       -> applied+not persisted: ask keep-session or revert
       -> rejected: keep dialog + show stable message
  -> restore default: set draft(system, transbridge.default), still requires apply
```

预览不能临时调用 `QApplication.setPalette()`，否则会让业务窗口闪烁并污染性能结果。Preview widget 获得 candidate palette/snapshot，只在其自身 subtree 展示公共组件状态矩阵。

## 设置入口边界

- FR26 当前通过 Action Catalog/Intent Router 提供“服务与 API 配置”，它是 canonical `SETTINGS_SERVICES` intent。FR24 新增独立的外观/通用设置 intent（计划名 `SETTINGS_APPEARANCE`）并接入同一 catalog/router/menu/command palette；不得把主题入口偷接到 `SETTINGS_SERVICES` 或恢复第二套菜单 callback。
- 通用 `SettingsDialog` 可提供 API 配置跳转，但继续派发既有 `SETTINGS_SERVICES`，不能复制 ParaTranz token 表单或把凭据和 UI theme 状态揉成一个 owner。
- 通用设置首期至少有“外观”“语言/无障碍说明”“API 配置入口”。S08 才真正接入 locale catalog。
- Provider 列表只展示 Registry 已注册的安全定义和 metadata；首期列表只有内置项，不展示不可用的导入/编辑按钮。
- theme/locale/guidance 共享现有 `UiPreferenceRepository` 的 `[ui]` 原子更新边界；应用一个 draft 不得覆盖 FR26 的 `guidance_mode`。

## 实施步骤

1. 定义 `UiSettingsDraft` 与 presenter/view-model，所有比较基于稳定 ID/scheme，不把 QObject/QPalette 序列化。
2. 实现 `ThemePreviewWidget`，覆盖按钮、输入、表格、状态 badge、focus、错误/警告和文本层级；candidate palette 只设在 preview root。
3. 实现 SettingsDialog 的模式选择、effective scheme 提示、Provider metadata、preview、apply/cancel/default。
4. 处理 `ThemeApplyResult` 的 `applied/persisted/unchanged/rejected`。持久化失败后用户选择保持 session 或调用 last persisted preference 回滚。
5. 扩展 Action Catalog、Intent Router/Composition 与菜单/命令搜索，注册一个 canonical 外观设置 intent；保留现有 API ConfigDialog 行为和凭据安全，不复制 token 表单逻辑。
6. 对 unknown theme/provider removed/system unknown 显示可恢复说明；内部 code 可写日志，用户信息不含配置路径/堆栈。
7. 增加关闭/反复打开 preview 的 cache/subscription 稳定性测试。

## 边界与错误处理

- 对话框打开期间系统主题变化：若 draft=system，重新生成隔离 preview；若 draft=explicit，只更新 effective-system 辅助说明，不覆盖用户 draft。
- Provider 在对话框打开后被注销：Apply 返回 unknown；保留 draft 供用户查看并要求选择可用项。
- 连续快速改变 combo：预览可 debounce 到同一 event-loop turn，但不得使用持续 timer；旧 preview generation 必须丢弃。
- persist 成功、apply 失败：ThemeService 应先 apply 后写或完成补偿；设置层不能留下配置与 active 不一致而不提示。
- cancel 不撤销对话框打开前已经由系统事件产生的合法 active theme 变化。

## 测试策略

- presenter：dirty/default/unchanged 与四类 apply result。
- Qt：preview 隔离、业务窗口 palette 未改变、cancel 释放、apply 后新旧窗口一致。
- 配置错误：read/write failure、未知 ID、provider removal、system unknown。
- 性能：快速预览不重复编译相同 fingerprint，反复打开/关闭 RSS/listener 稳定。
- 文案：错误提示不包含绝对路径、exception repr 或秘密。

## 文件变更清单

- 新增 `src/transbridge/ui/settings_dialog.py`
- 新增 `src/transbridge/ui/foundation/preview.py`
- 修改 `src/transbridge/ui/shell/action_catalog.py`、`intent_composition.py`、`menu_builder.py` 与命令搜索相关测试
- 修改 `src/transbridge/config/ui_preferences.py`，复用 `src/transbridge/bootstrap/composition.py` 已注册的 `ui_preferences`
- 最小修改 `src/transbridge/ui/main_window.py` 的 composition，不新增直接菜单/业务 callback
- 最小适配 `src/transbridge/ui/paratranz/config_dialog.py`
- 新增 `tests/ui/test_ui_settings_dialog.py`

## 风险与回退

即时全应用预览会造成重绘和状态污染，因此明确采用隔离 subtree。若新设置容器存在回归，可保留原 API 配置入口并暂时只开放独立“外观设置”，ThemeService 合同不变。

## 未决问题

- 设置对话框采用 tabs 还是侧栏是局部 UI 选择，不改变本 Story 合同；应优先复用 S04 primitive 并保持键盘顺序。

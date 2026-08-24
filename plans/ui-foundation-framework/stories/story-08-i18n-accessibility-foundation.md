# Story-08：i18n 与无障碍基础合同

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：草稿
- **优先级**：P1
- **前置依赖**：S04 公共组件、S06 关键路径组件
- **下游依赖**：S09 最终审计

## 目标

解决 M69/M70 的框架缺口，但避免把全量历史文案迁移或持续辅助功能轮询塞入主题热路径。

FR26 已为开始中心、Workbench、AI、Operation Plan、Task Center、Command Palette 与危险操作建立可观察的焦点/Enter/Esc/accessible name/非纯颜色合同。本 Story 在其上增加 Foundation helper、locale 资源和主题对比度验证，不重新定义这些交互。

## 原始验收标准

- [ ] `LocaleService` 使用统一 gettext catalog、source locale、fallback 和配置持久化；首期 locale 切换明确重启生效。
- [ ] 缺失 catalog/msgid 回退源语言并聚合诊断，不在 paint/刷新热路径重复日志。
- [ ] 公共组件不固化中文，关键路径（应用菜单、设置、主题错误/回退）完成 msgid 接入。
- [ ] 公共组件设置 accessible name/description、合理 focus policy、可见焦点和键盘顺序；仅颜色状态有等价文本/图标。
- [ ] 关键文字/背景和 focus/selection 组合通过对比度检查，字体与 DPI 缩放不截断关键设置控件。
- [ ] 最低 PyQt6 6.5 路线不依赖 Qt 6.10 accessibility hints；未来 hints 有显式适配接口。
- [ ] `tests/ui/test_accessibility_contracts.py` 与 FR26 J01～J09 accessibility/focus 断言在 light/dark/system matrix 下继续通过；主题/locale 切换不新增快捷键 owner 或改变危险操作确认语义。

## Locale 合同

计划新增 Qt-free 翻译 facade，Qt widget 只在构造/显式 retranslate 时调用：

```text
LocalePreference(locale_id)
LocaleSnapshot(source_locale, active_locale, catalog_version, fallback, diagnostics)

LocaleService.start() -> LocaleSnapshot
LocaleService.gettext(msgid) -> str
LocaleService.ngettext(singular, plural, n) -> str
LocaleService.set_preference(locale_id, persist=True) -> LocaleChangeResult
LocaleService.close() -> None
```

首期 `set_preference` 成功写入 `[ui] locale` 后返回 `restart_required=true`；当前窗口继续使用启动 snapshot。这样避免混合语言和全窗口扫描。`zh-CN` 暂定 source locale；catalog 路径和打包清单必须支持 onedir/PyInstaller。

缺失 msgid 直接回退 source string；诊断按 `(locale, msgid, catalog_version)` 去重/计数，应用结束或诊断页聚合展示，不逐 paint/log。

## 无障碍合同

- 公共组件构造时设置 `accessibleName`；复杂控件/状态设置 `accessibleDescription`，动态状态变化同步可访问文本。
- 键盘可到达所有主要操作，无 keyboard trap；tab order 由容器显式验证。
- focus indicator 与相邻颜色至少 3:1，普通文字/背景至少 4.5:1，大文字至少 3:1；禁用项按规则豁免但仍不可成为唯一提示。
- 关键信息不得仅靠红/绿、深/浅表达；同时提供 label、icon、pattern、边框或状态列。
- 在 100%/150%/200% DPI 或等价字体放大下，设置对话框关键文字、按钮和选择器不截断；大数据表允许滚动，不要求强行压缩。
- Qt 6.10 `QAccessibilityHints` 通过可选 `AccessibilityHintsSource` adapter 暴露，最低 6.5 默认返回 unavailable，不改变功能。

## 数据流

```text
startup -> LocaleService loads configured catalog once
        -> MainWindow/Settings/common components call gettext during construction

user changes locale -> validate catalog -> persist preference
                    -> show restart-required message
                    -> no global retranslate, no mixed current window

theme validation -> contrast checks on declared adjacent token pairs
component construction -> accessibility helper sets names/focus/description
```

Locale 和 Theme 拥有独立 revision/事件。Theme change 不重新加载 catalog；locale preference 不重新编译 palette。

## 实施步骤

1. 定义 LocaleService/snapshot/result 和 catalog loader，注入资源 root 与 ConfigRepository，避免测试访问真实安装目录。
2. 建立 gettext template/catalog 目录、source locale 和打包发现合同；首期至少提供 source catalog 和一个测试 locale。
3. 接线 `GuiFoundation.locale`，启动失败回退 source locale；close 清理 catalog cache，不操作 ThemeService。
4. 把 S04 公共组件文案改为 msgid；迁移 app menu、SettingsDialog 和主题错误/回退提示，保留其他历史文案 inventory。
5. 实现 accessibility helper：name/description/focus/state text/contrast pair registry；组件无需后台 observer。
6. 复用 FR26 已有 tab/focus/Enter/Esc 合同，为 SettingsDialog 与 Foundation 新组件补齐 DPI probe；对动态 StatusBadge/Task 状态同步 accessible description。
7. 增加审计：公共组件裸中文、缺 accessible name、颜色-only 状态和未声明 contrast pair。

## 边界与错误处理

- locale ID 非法/路径逃逸：拒绝并保持当前/source，不拼接任意用户路径。
- catalog 损坏或版本不匹配：回退 source，返回 `locale_catalog_invalid`；不能让 GUI 启动失败。
- persist 失败：不承诺下次生效；对话框保持原 persisted selection 并提示。
- msgid 缺失：返回 source string；诊断去重，绝不返回空字符串。
- 200% DPI 下窗口超屏：允许可滚动布局，关键 Apply/Cancel 必须可达。
- 自定义用户标签/远端文本不进入 gettext；它们是数据，不是 UI source strings。

## 测试策略

- Locale：存在/缺失/损坏/forward catalog、fallback、plural、diagnostic 去重、restart_required、配置失败。
- 打包：资源清单可发现、非 ASCII 安装路径。
- Accessibility：accessibleName/Description、tab traversal、focus visible、非颜色状态。
- Contrast：declared token pairs 按未四舍五入 ratio 门禁；普通文字 4.5:1、UI/focus 3:1。
- DPI：100/150/200% 下设置/菜单/关键 component sizeHint 与可达性。
- 性能：locale/theme idle 无 timer；gettext 热 lookup 不引发 catalog reload。

## 文件变更清单

- 新增 `src/transbridge/ui/foundation/locale_service.py`
- 新增 `src/transbridge/ui/foundation/accessibility.py`
- 新增 `src/transbridge/ui/i18n/` 及首期 catalog/template
- 修改 `src/transbridge/ui/app.py`
- 修改 `src/transbridge/ui/main_window.py`
- 修改 `src/transbridge/ui/settings_dialog.py`
- 修改 S04 公共组件
- 新增 `tests/ui/foundation/test_locale_service.py`
- 新增 `tests/ui/foundation/test_accessibility_contract.py`
- 修改并复用 `tests/ui/test_accessibility_contracts.py`，不要建立一套与 FR26 冲突的键盘/焦点真源

## 风险与回退

全量文案迁移会显著放大范围，因此首期只要求 Foundation 与关键路径。LocaleService 失败始终回退 source locale；accessibility helper 失败不能移除 Qt 默认键盘行为。完整历史迁移必须另立后续 Story/Plan 增量。

## 官方验收依据

- [WCAG 2.2 1.4.3](https://www.w3.org/TR/WCAG22/#contrast-minimum)：普通文字最小 4.5:1，大文字 3:1。
- [WCAG 2.2 1.4.11](https://www.w3.org/TR/WCAG22/#non-text-contrast)：识别 UI 控件、状态和必要图形的视觉信息至少 3:1。
- [WCAG 2.2 1.4.4](https://www.w3.org/TR/WCAG22/#resize-text)：文本放大到 200% 不丢失内容或功能；桌面 Qt 以字体/DPI probe 作为对应工程门禁，而不宣称 Web conformance。

## 未决问题

- 第二个正式语言及翻译维护流程不在本 Story 决定；测试 locale 只验证合同，不能伪称产品已完成多语言翻译。

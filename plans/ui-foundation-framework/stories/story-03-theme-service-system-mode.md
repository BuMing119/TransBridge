# Story-03：ThemeService、Palette 应用与系统模式

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：已完成（2026-08-24）
- **优先级**：P0
- **前置依赖**：S02 主题模型、Registry 与内置主题
- **下游依赖**：S04～S09

## 目标

建立 GUI 进程唯一主题 owner，并在应用构造阶段完成高性能 Palette-first 接线。

## 原始验收标准

- [x] `ThemeService.start/set_preference/snapshot/close` 和 `theme_changed` 遵守 ADR-020；只有 effective fingerprint 改变才递增 revision 和发信号。
- [x] 使用 Fusion + `QPalette` 应用标准控件颜色，不调用 `allWidgets()`、不对所有 widget 手动 polish、不使用颜色型全局 QSS。
- [x] `system` 通过 Qt 6.5 `QStyleHints.colorScheme/colorSchemeChanged` 事件驱动；Unknown 稳定回退浅色；显式 light/dark 不依赖 Qt 6.8 setter。
- [x] `ui/app.py` 在创建业务 widget 前构造并启动 `GuiFoundation`，显式传入 `ConfigRepository`；关闭时先断开 UI 信号再关闭 AppRuntime。
- [x] `[ui] theme_mode/theme_id/locale` 通过统一 repository 原子更新；无效值和写失败保留最后有效状态并返回稳定错误码。
- [x] ThemeService 只能在 GUI 主线程应用 Qt 快照；跨线程请求安全排队或明确拒绝。

## 所有权与启动顺序

```text
app.main
  -> QApplication
  -> default_config_repository / injected repository
  -> GuiFoundation.create(app, repository)
       -> register builtins
       -> load [ui] preference
       -> ThemeService.start()
       -> set Fusion once + apply initial QPalette
       -> connect QStyleHints.colorSchemeChanged when mode=system
  -> AppContext
  -> MainWindow(..., ui_foundation=foundation)
  -> app.exec
finally:
  -> ui_foundation.close()
  -> app_runtime.close()
```

GUI Foundation 与 `AppRuntime` 并列，由 GUI entrypoint 所有。不得写入 `app_runtime.state`，也不得让 bootstrap/application import PyQt。

## 计划新增接口

```text
ThemePreference(mode, theme_id)
ThemeSnapshot(revision, provider_id, theme_id, scheme,
              fingerprint, tokens, palette, cache_namespace)
ThemeApplyResult(status, persisted, snapshot, diagnostics)

ThemeService.start() -> ThemeApplyResult
ThemeService.set_preference(preference, *, persist=True) -> ThemeApplyResult
ThemeService.preview(preference) -> ThemeSnapshot       # no apply/persist
ThemeService.snapshot() -> ThemeSnapshot
ThemeService.close() -> None

GuiFoundation(theme, locale, accessibility, registry, config)
GuiFoundation.close() -> None
```

`theme_changed` 使用 `(revision: int, snapshot: object)`；订阅方必须检查 revision 单调性。Service 内维护 `_last_good_snapshot`、`_started`、`_closed` 和 GUI thread identity。

## 状态与事件规则

1. `start()` 只成功启动一次；重复调用返回当前 snapshot，不重复连接 signal。
2. preference resolve 为 effective scheme。system + Unknown 固定回退 light，直到收到真实 `colorSchemeChanged`；不启动 timer。
3. definition 先由 Registry resolve，再由 `qt_palette.py` 把 canonical RGBA 编译为 `QColor/QBrush/QPalette`。编译结果按 fingerprint 缓存。
4. candidate 与 current fingerprint 相同时直接 `unchanged`；persist 请求只有用户偏好确实变化时才写配置。
5. 应用顺序：保存 last-good Qt/app 状态 → `QApplication.setPalette(candidate.palette)` → 更新 snapshot/revision → emit。异常则恢复 last-good palette/snapshot，不 emit candidate。
6. 系统信号不持久化 effective scheme，只保留 `mode=system`；用户 light/dark 变更才写 preference。
7. close 断开 styleHints、取消 queued request、清缓存并拒绝后续 apply；不得关闭 ConfigRepository 或 AppRuntime。

## 配置合同

统一 `[ui]` section 继续由现有 `UiPreferenceRepository` typed adapter 管理；FR24 扩展其 snapshot/save result，不新建第二个配置 owner：

```ini
[ui]
theme_mode = system
theme_id = transbridge.default
locale = zh-CN
guidance_mode = auto
```

底层读取仍通过 `ConfigSnapshot.value()`，更新仍由 adapter 在一次 `update_sections({"ui": {...}})` 中提交。无效 enum/theme ID 不修改文件，运行时回退并产生 `theme_unknown`/`theme_preference_invalid`。配置写失败时可应用到本会话，但 `persisted=false`，S05 负责 UX 决策。现有 `guidance_mode` 必须原样保留，主题写入不得覆盖 FR26 引导偏好。

## 实施步骤

1. 在 `qt_palette.py` 建立 semantic token 到 Qt `ColorRole/ColorGroup` 的完整映射，编译时校验无遗漏；不要在 paint 热路径转换颜色。
2. 实现 `ThemeService` 的状态机、幂等、last-good 回滚和 GUI thread guard。跨线程公共方法可使用 queued signal 到 owner thread，或明确抛 `theme_wrong_thread`；整个项目只保留一种策略。
3. 实现 `GuiFoundation` 组合对象，封装 Registry/builtins/config/ThemeService，供测试替换 repository 和 system scheme source。
4. 扩展现有 `config/ui_preferences.py` 的 `UiPreferenceRepository` 与 `bootstrap/composition.py` 中既有 `ui_preferences` 注册；再修改 `ui/app.py` 启动顺序。Foundation start 失败时尝试直接编译内置 light；若仍失败，记录错误并使用 Qt 默认 palette，GUI 仍启动。
5. 修改 `MainWindow` 构造函数显式接收 Foundation 或窄接口；向 Workbench/ParaTranz 的深入传递留给 S06/S07，不能暂时创建 singleton。
6. 建立假的 styleHints/config repository 与真实 QApplication integration tests。
7. 在 S01 benchmark 中加入真实 start/repeated apply/system signal 采样，确认无 idle timer/扫描。

## 边界与错误处理

- `QApplication.instance()` 不存在：构造失败 `theme_application_missing`，不能偷偷创建第二个 QApplication。
- Fusion style 不可用：保留平台 style，应用 palette 并产生降级诊断；不得阻断业务 GUI。
- system signal 在 close 后到达：连接已断开或 closed guard 忽略。
- apply 后配置写失败：返回 current snapshot + `persisted=false`，不回滚内存主题；S05 可让用户选择回滚。
- application palette apply 抛异常：恢复 last-good；若首次启动无 last-good，使用 Qt default/compat-light 并记录。
- QObject 已删除订阅：标准 Qt signal 自动断开；Foundation 自建订阅必须有释放句柄。

## 测试策略

- 纯状态：首次启动、重复启动、same fingerprint、revision 单调、close 幂等。
- Qt：Fusion/palette roles、existing/new widget 继承、ApplicationPaletteChange、自定义 observer 单次信号。
- 系统：light/dark/Unknown、快速抖动、离开 system 后旧信号不生效。
- 配置：缺 section、非法 mode、未知 theme、原子写成功/失败、未写 effective scheme。
- 恢复：compile/apply/emit 前异常、last-good 还原、首次 fallback。
- 线程：worker request 或拒绝合同、Qt pixmap/palette 只在 GUI thread。

## 文件变更清单

- 新增 `src/transbridge/ui/foundation/qt_palette.py`
- 新增 `src/transbridge/ui/foundation/theme_service.py`
- 新增 `src/transbridge/ui/foundation/runtime.py`
- 修改 `src/transbridge/ui/app.py`
- 修改 `src/transbridge/ui/main_window.py`
- 修改 `src/transbridge/config/ui_preferences.py` 与 `src/transbridge/bootstrap/composition.py`；仅在现有原子更新能力不足时局部扩展 `src/transbridge/config/repository.py`
- 新增 `tests/ui/foundation/test_theme_service.py`
- 新增 `tests/integration/gui/test_ui_foundation_startup.py`

## 风险与回退

应用级 palette 变化会触发 Qt 全局 repaint，这是唯一允许的全局更新。不得为优化而跳过现有窗口，也不得手动遍历所有 widget。迁移期可选择 compatibility light；关闭 Foundation 后业务 runtime 和数据不受影响。

## 未决问题

- 跨线程 `set_preference` 是 queued future 还是 fail-fast，由实现根据现有 UI 调用点确定；无论选择哪种，结果类型和测试必须唯一，不能有时同步有时异步。

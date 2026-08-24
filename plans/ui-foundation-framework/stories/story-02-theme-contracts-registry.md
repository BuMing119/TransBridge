# Story-02：语义令牌、Provider、Registry 与内置主题

- **所属 Plan**：[高性能统一 UI 基础框架](../plan.md)
- **状态**：草稿
- **优先级**：P0
- **前置依赖**：S01 性能基线与迁移清单
- **下游依赖**：S03 ThemeService、S04 公共组件、S09 扩展合同

## 目标

建立不依赖 Qt 的稳定主题数据合同，并交付浅色、深色和迁移期兼容浅色定义。

## 原始验收标准

- [ ] `ThemeManifest`、`ThemeTokens`、`ThemeDefinition`、`ThemeProvider`、错误和注册结果为冻结值对象/Protocol，不 import PyQt。
- [ ] 令牌覆盖基础、语义和业务三层；业务层至少覆盖 Stage、标签、差异、译文、任务、报告状态。
- [ ] validator 一次性检查 schema/version、ID、token 完整性、颜色/数值、引用闭合、关键对比度、资源预算和冲突。
- [ ] 无效 provider/theme 整体拒绝，Registry 不留下部分状态；同一 provider 重复注册结果幂等。
- [ ] 内置 light/dark 在相同结构令牌下具有完整语义 token；兼容浅色只用于渐进回退并明确移除门禁。
- [ ] 不允许 Provider 提供 Python widget、回调、网络资源或原始全局 QSS。

## 数据模型与接口

以下均为计划新增的 Qt-free 类型：

```text
ThemeScheme: light | dark

ThemeManifest(
  schema_version, provider_id, theme_id, version, display_name,
  supported_schemes, resource_budget, compatibility
)

PrimitiveTokens(colors, typography, spacing, radii, sizes)
SemanticTokens(window, surface, text, border, focus, selection, disabled,
               link, success, warning, error, info)
DomainTokens(stage, labels, diff, translation, task, report)
ThemeTokens(primitives, semantic, domain)
ThemeDefinition(manifest, scheme, tokens, resources, fingerprint)

ThemeProvider.manifest() -> ThemeManifest
ThemeProvider.load(theme_id, scheme) -> ThemeDefinition

ThemeRegistry.register(provider) -> RegistrationResult
ThemeRegistry.resolve(theme_id, scheme) -> ThemeDefinition
ThemeRegistry.unregister(provider_id) -> RegistrationResult  # test/future lifecycle only
```

值对象使用 `@dataclass(frozen=True, slots=True)`、tuple/frozenset，避免每次访问复制 dict。`fingerprint` 由规范化 manifest、scheme、tokens 和 resource descriptors 计算，不包含显示名称本地化结果。

稳定 ID 采用小写点分命名，例如 `transbridge.builtin` / `transbridge.default`；禁止空白、路径分隔符和大小写冲突。schema forward version fail closed，旧 version 是否迁移必须通过显式 adapter，Registry 不猜测。

## 令牌与校验规则

- primitives 是 Provider 内部构建材料；生产业务组件不得获得按色阶取值的便捷 API。
- semantic 覆盖标准 Qt roles 与交互状态；domain 以稳定状态 key 映射现有 `STAGE_LABELS/STAGE_COLORS`、Task 状态、报告 severity 等业务含义。
- 普通文字/背景初始门禁 4.5:1；大文字和必要 UI 图形/焦点边界 3:1。disabled 可豁免对比度，但不得成为唯一状态信息。
- 所有颜色在注册时规范化为不可变 RGBA 整数或 canonical string；运行时不解析 `#hex`。
- 资源 descriptor 仅允许包内逻辑 ID、相对安全路径、类型、字节数和 checksum；拒绝 URL、绝对路径、`..`、符号链接逃逸和超预算总量。
- `RegistrationResult` 包含 `registered | unchanged | rejected`、稳定 error code 和不泄漏路径的诊断。

## 实施步骤

1. 定义枚举、manifest、三层 token、definition、provider Protocol、结果与异常。保持 `model.py` 可在没有 PyQt 的环境导入。
2. 实现 canonical serialization/fingerprint，确保字段顺序、dict 输入顺序或重复 load 不影响结果。
3. 实现 validator pipeline：结构 → ID/version → token/value/reference → contrast → resource policy；收集可定位诊断后整体拒绝。
4. 实现 Registry 的 copy-on-write 注册：先在临时候选上验证 provider 的所有声明 scheme，再一次替换 registry snapshot；异常 provider 不能污染旧状态。
5. 建立内置 default light/dark 和 compatibility light。浅/深共享 typography/spacing/radius/size，只改变 palette/domain 色值，保证切换不重布局。
6. 把现有 `translation_entry.STAGE_COLORS` 视为迁移输入而非新权威；DomainTokens 完整覆盖 stage key，并在 S06/S07 后逐步停止 UI 直接读取旧颜色。
7. 增加纯 Python tests，特别验证模块加载图中不出现 `PyQt6`。

## 边界与错误处理

- Provider `manifest()`/`load()` 抛异常：返回 `theme_provider_failed`，Registry 保持原 snapshot。
- 同 provider/theme/version/fingerprint 重复注册：`unchanged`，不重复保留对象。
- 同稳定 ID 但不同 fingerprint：首期 `theme_id_conflict`，不自动升级；未来升级流程需要显式 replace policy。
- 缺少某个 domain 状态：整个 scheme 拒绝，不能运行时回退黑色。
- alpha 导致实际复合对比度不明：按声明背景计算；不能证明的关键组合拒绝。
- 兼容主题只允许内置 provider 注册，外部 provider 不得使用保留 namespace。

## 测试策略

- Qt-free import 与依赖图测试。
- dataclass 冻结、slots、canonical fingerprint 和 mutation 尝试。
- light/dark token 全量、对比度、所有 Stage/Task/report 状态覆盖。
- forward/negative schema、非法 ID、缺 token、NaN/负尺寸、递归引用、资源逃逸/超预算。
- provider 异常、部分 scheme 失败、ID 冲突、幂等重复注册和 unregister 生命周期。

## 文件变更清单

- 新增 `src/transbridge/ui/foundation/__init__.py`
- 新增 `src/transbridge/ui/foundation/model.py`
- 新增 `src/transbridge/ui/foundation/registry.py`
- 新增 `src/transbridge/ui/foundation/builtins.py`
- 新增 `tests/ui/foundation/test_theme_registry.py`
- 新增 `tests/ui/foundation/test_builtin_themes.py`

## 风险与回退

令牌过细会增加迁移成本，过粗则业务组件再次派生颜色。以现有实际状态清单为边界，新增 token 必须是跨两个以上组件复用或稳定业务语义。此 Story 不接线 QApplication，可安全独立回退。

## 未决问题

- 未来外部主题资源使用 zip 包还是安装目录不在本 Story 决定；Provider/descriptor 合同不得假设具体载体。

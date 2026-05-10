# ADR-001: TranslationEntry 作为统一翻译数据模型

- **状态**: 已接受
- **日期**: 2026-01 (回顾性记录于 2026-05-06)
- **决策者**: BuMing

## Context

TransBridge 需要处理来自多种来源的翻译数据：ESP/ESM 插件、EET XML、XT XML、DSD JSON、ParaTranz API。每种来源有不同的数据格式和字段语义。需要选择一种数据建模策略来统一管理这些异构数据。

## Decision

采用 **TranslationEntry 作为唯一统一数据模型**。所有来源（ESP/EET/XT/JSON/ParaTranz）的翻译条目在进入系统后立即转换为 `TranslationEntry` 实例，下游所有操作（筛选、编辑、导出、AI 翻译、写回）仅操作该统一格式。

```python
@dataclass
class TranslationEntry:
    id: str          # 唯一标识: {editor_id}:{form_id}|{index}~{TYPE:FIELD}
    key: str         # 与 id 相同，保持向后兼容
    original: str    # 原文
    translation: str # 译文
    stage: int       # 翻译阶段 (0=未翻译, 1=AI翻译, 2=已确认)
    context: str     # 类型上下文: {TYPE:FIELD}|{extra_info}
```

## Consequences

- **正**: 解析器与下游逻辑完全解耦，新增数据源只需添加工厂方法
- **正**: 统一的序列化/反序列化接口（JSON）
- **正**: 所有 UI 组件、AI 翻译、写回模块仅依赖单一数据模型
- **负**: ID 格式较复杂，携带了来源格式的编码信息
- **负**: 某些来源特有字段（如 EET 的 TRADUIT 状态）在转换中丢失

## Alternatives Considered

- **多态类型层级**: 为每种来源创建 TranslationEntry 子类 → 拒绝：增加复杂度，下游需要 instanceof 判断
- **字典/JSON 透传**: 不转换，保留原始格式 → 拒绝：下游代码需要理解所有来源格式

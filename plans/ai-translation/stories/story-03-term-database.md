# Story 03: 术语库管理器

**所属方案**: `plans/ai-translation/plan.md`
**状态**: ✔️ 已实现

## 概述

多来源术语库，按优先级合并。支持动态术语库（按 ESP 绑定）、静态术语库（JSON/Excel）、ParaTranz 同步。

## 关键设计

- **TermDatabaseManager**: 四来源（manual/auto/paratranz/json/excel）+ 优先级合并
- **DynamicTermDatabase**: 按 ESP 绑定，数据目录 `data/ai_translator/{esp_stem}/`
- **match_terms()**: 精确子串匹配 + 大小写 + 冠词规范化
- **match_terms_enhanced()**: 两阶段召回（精确匹配 + 语义向量检索）
- **_in_flight_terms**: threading.Lock 保护的共享缓存，并发批次间术语实时可见
- **来源优先级**: manual > auto_name/auto_dialogue > paratranz > json > excel

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/term_database.py` | TermDatabaseManager, DynamicTermDatabase, TermEntry |

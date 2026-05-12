# 001: 修复 check_quality _replace 崩溃

**日期**: 2026-05-11
**类型**: 改
**关联**: Epic: AI 后处理系统 > Story 07: PostProcessor 编排器

## 修改文件

### `src/transbridge/ai_translator/post_processor/post_processor.py` (改)
- **修改内容**: `_execute_decisions()` 方法中，pass 和 reject 两个分支均不再创建新的 `TranslationEntry` 然后调用不存在的 `entry._replace(updated)`，改为直接原地修改 mutable dataclass 的字段：pass 分支设置 `entry.translation` + `entry.stage = 1`，reject 分支设置 `entry.stage = 0`。同时移除方法内部不再需要的局部 `TranslationEntry` import
- **原因**: `TranslationEntry` 是 `@dataclass` 而非 `namedtuple`，没有 `_replace` 方法。运行 `check_quality` 工具时裁决通过/打回条目会触发 `AttributeError: 'TranslationEntry' object has no attribute '_replace'` 崩溃。由于条目对象与 collection 中存储的是同一引用，直接修改字段即可生效，无需构造新实例替换

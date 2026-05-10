# Story 05: 翻译控制器

**所属方案**: `plans/ai-translation/plan.md`
**状态**: ✔️ 已实现

## 概述

AI 翻译主控制器，协调批次执行、并发控制、断点续传、暂停/停止。是整个 AI 翻译模块的调度中枢。

## 关键设计

- **AutoTranslator**: translate() 方法驱动全流程
- **并发控制**: ThreadPoolExecutor + max_concurrent 限制
- **断点续传**: ProgressCheckpoint 持久化到 `data/ai_translator/{esp_stem}/{esp_stem}_progress.json`，翻译完成后自动删除
- **暂停/停止**: threading.Event + _CancelledByPause/_CancelledByStop (BaseException)，信号穿透 except Exception
- **流式增量写回**: 每收到 chunk 就调用 extract_partial_pairs() → 实时更新 Collection
- **递归重试**: 响应中有 missing 条目时对半拆分，直到单条目或最小批次

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ai_translator/translator.py` | AutoTranslator, TranslatorConfig, TranslationResult, ProgressCheckpoint |

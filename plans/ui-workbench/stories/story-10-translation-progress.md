# Story 10: AI 翻译进度窗口 + 后台 Worker

**所属方案**: `plans/ui-workbench/plan.md`
**状态**: ✔️ 已实现

## 概述

AI 翻译执行期间的进度窗口和后台 Worker。支持暂停/停止/后台运行。

## 关键设计

- **_TranslationWorker(QThread)**: 封装 AutoTranslator.translate() 调用，信号 progress/result/error
- **_TranslationProgressWindow**: 显示当前批次进度、总进度、成功/失败/跳过计数
- **暂停/停止**: 按钮触发 → AutoTranslator._paused.set() / _stopped.set()
- **后台运行**: 支持将窗口最小化，翻译在后台继续
- **LLM 日志**: 可打开 _LLMLogViewer 查看 LLM 原始响应

## 涉及文件

| 文件 | 说明 |
|------|------|
| `src/transbridge/ui/tools/ai_translator/_translation_worker.py` | _TranslationWorker |
| `src/transbridge/ui/tools/ai_translator/_translation_progress_window.py` | _TranslationProgressWindow |
| `src/transbridge/ui/tools/ai_translator/_llm_log_viewer.py` | _LLMLogViewer |

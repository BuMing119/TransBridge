# 003: QA审查 — Story 25 后处理工具统一发现 5 Blocker + 5 Critical

**日期**: 2026-05-20
**类型**: 改（QA审查报告）
**关联**: Epic: Agent工具系统全面扩展 > Story 25: 后处理工具统一

## 修改文件

### `docs/test-reports/story-25-postprocess-unification-qa.md` (增)
- **修改内容**: QA审查报告，对比 AI 助手后处理工具（`run_postprocess` + `start_polish`）与 GUI `PostProcessor` 五阶段流水线的功能一致性
- **原因**: 用户要求检测 AI 助手使用工具能否达到原后处理工作流效果

## 发现摘要

**5 Blocker（运行时崩溃）**：
1. `tool_proofreader.py:93` — `PostProcessor()` 不接受 `llm_client`/`term_manager`/`esp_path` 关键字参数
2. `tool_proofreader.py` — 未调用 `register_default_checkers()`，五阶段流水线静默空转
3. `tool_translator.py:187` — `LLMPolisher(intensity=...)` 参数名和必需参数均错误
4. `tool_translator.py:186` — 导入路径 `llm_polisher` 不存在，实际文件为 `polisher.py`
5. `tool_translator.py:184` — `_tool_start_polish` 未创建 `LLMClient` 实例

**5 Critical（功能缺失）**：
1. 缺少 Excel 报告生成 (Story 10-13)
2. 缺少断点续传 (checkpoint)
3. 缺少润色预览确认 (Story-09 `_PolishPreviewDialog`)
4. 独立润色不加载 LLMConfig 润色配置
5. v1 `check_quality` 同样未调用 `register_default_checkers()`
# Story 10: 报告生成后端与Excel导出

**所属方案**: `plans/ai-post-process/plan.md`
**状态**: ✔️ 已实现
**对应需求**: FR6.10.3, FR6.10.4, FR6.10.8(部分)

## 概述

实现 `ReportGenerator` 类，聚合翻译/后处理/润色结果数据，生成结构化 Excel 报告文件（`.xlsx`），管理文件轮转（保留最近 20 份）。

## 验收标准

- [ ] `ReportGenerator.generate_translate_report()` 生成翻译模式 Excel（Summary/Entries/Issues/Refinements/Arbitrations 五个 Sheet）
- [ ] `ReportGenerator.generate_polish_report()` 生成润色模式 Excel（Summary/Entries/Polish 三个 Sheet）
- [ ] 文件命名：`{esp_stem}_{mode}_report_{YYYYMMDD_HHMMSS}.xlsx`，输出到 `data/ai_translator/{esp_stem}/reports/`
- [ ] 自动清理：保留最近 20 份报告，删除更早的文件
- [ ] Excel 写入失败时不抛异常，返回 `None` 并记录日志
- [ ] `TranslationResult` 新增 `report_path: str | None` 字段

## 实现步骤

### 步骤 1: 创建 ReportGenerator 类
- 新建 `src/transbridge/ai_translator/post_processor/report_generator.py`
- 类构造函数接收：`esp_stem: str`
- 输出目录：`data/ai_translator/{esp_stem}/reports/`，不存在则自动创建
- 涉及文件: `src/transbridge/ai_translator/post_processor/report_generator.py` (新)

### 步骤 2: 实现翻译报告生成
- 方法 `generate_translate_report(result: TranslationResult) -> str | None`
- 数据源：`result.success_count/failed_count/skipped_count/new_dynamic_terms`、`result.post_process_result`（PostProcessResult 含 issues/auto_fixed/needs_review）、需从 post_processor 传入的中间数据（refine_results/polish_results/decisions）
- **Summary Sheet**: total_checked, issue_count, error_count, warning_count, info_count, passed, rejected, pending, refined_count, polished_count, config_snapshot（后处理配置）, timestamp, esp_stem
- **Entries Sheet**: entry_id, original, initial_translation, refined_translation, polished_translation, final_translation, stage, verdict, verdict_reason, confidence, issue_count, issue_types
- **Issues Sheet**: entry_id, issue_type, severity, message, suggestion, original, translation
- **Refinements Sheet**: entry_id, refined_translation, confidence, fixes_applied, note
- **Arbitrations Sheet**: entry_id, verdict, reason, confidence, suggested_action
- 使用 `openpyxl`（项目已有依赖），设置合理的列宽和自动筛选
- 涉及文件: `src/transbridge/ai_translator/post_processor/report_generator.py`

### 步骤 3: 实现润色报告生成
- 方法 `generate_polish_report(polish_results: dict[str, PolishResult], entries: list[TranslationEntry], stats: dict) -> str | None`
- stats 包含：total/accepted/rejected/failed count
- **Summary Sheet**: total_entries, accepted_count, rejected_count, failed_count, polish_level, config_snapshot, timestamp, esp_stem
- **Entries Sheet**: entry_id, original, original_translation, polished_translation, accepted(bool), confidence, changes_summary
- **Polish Sheet**: entry_id, change_aspect, before, after, reason
- 涉及文件: `src/transbridge/ai_translator/post_processor/report_generator.py`

### 步骤 4: 文件轮转与命名
- 私有方法 `_rotate(dir, keep=20)`：列出目录中所有 `.xlsx` 文件，按修改时间排序，删除最旧的超出 keep 的部分
- 文件命名模板：`f"{esp_stem}_{mode}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"`
- mode 参数：`"translate"` 或 `"polish"`
- 涉及文件: `src/transbridge/ai_translator/post_processor/report_generator.py`

### 步骤 5: 扩展 TranslationResult
- 在 `TranslationResult` dataclass 中新增字段：`report_path: str | None = None`
- 翻译完成后由调用方设置此字段
- 涉及文件: `src/transbridge/ai_translator/translator.py`

## 关键接口

```python
class ReportGenerator:
    def __init__(self, esp_stem: str):
        self._esp_stem = esp_stem
        self._output_dir = os.path.join(
            LLMConfig.get_ai_translator_dir(esp_stem), "reports"
        )

    def generate_translate_report(
        self,
        result: TranslationResult,
        refine_results: dict | None = None,
        polish_results: dict | None = None,
        decisions: dict | None = None,
    ) -> str | None: ...

    def generate_polish_report(
        self,
        polish_results: dict[str, PolishResult],
        entries: list[TranslationEntry],
        stats: dict,
    ) -> str | None: ...

    def _rotate(self, keep: int = 20) -> None: ...
```

## 架构依赖

- `openpyxl` — 项目已有依赖，无新增
- `TranslationResult` — 需新增 `report_path` 字段
- `PostProcessResult` / `PolishResult` — 已有数据结构，直接读取
- `LLMConfig.get_ai_translator_dir()` — 已有方法，获取数据目录

## 边界条件

- 输出目录不存在 → 自动创建
- Excel 写入权限不足 → 捕获异常，返回 None，记录日志
- 空结果（success_count=0）→ 仍生成报告（Summary 全0，其他 Sheet 为空表）
- 后处理未启用 → 翻译报告的 Issues/Refinements/Arbitrations Sheet 仅含表头

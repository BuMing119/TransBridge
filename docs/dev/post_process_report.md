# AI 翻译后处理报告

## 职责

在 AI 翻译的五阶段后处理（检测 → 修复 → 润色 → 裁决 → 执行）全部结束后，自动生成一份结构化 Excel 报告文档，帮助用户了解译文质量状况、定位需要人工复核的条目，并追踪各阶段的修改细节。

---

## 报告结构

报告采用多 Sheet Excel（`.xlsx`）格式，项目已内置 `openpyxl` 依赖，无需额外安装。

### Sheet 1: Summary（汇总）

| 字段 | 说明 |
|------|------|
| `total_checked` | 检查后处理的总条目数 |
| `issue_count` | 发现问题总数 |
| `error_count` | error 级别问题数 |
| `warning_count` | warning 级别问题数 |
| `info_count` | info 级别问题数 |
| `passed` | 裁决通过条目数 |
| `rejected` | 裁决打回条目数 |
| `pending` | 裁决待审条目数 |
| `refined_count` | 经历 LLM 修复的条目数 |
| `polished_count` | 经历 LLM 润色的条目数 |
| `config_snapshot` | 后处理配置快照（各阶段开关、润色范围/级别、严格模式等） |
| `timestamp` | 报告生成时间 |
| `esp_stem` | 来源插件名 |

### Sheet 2: Entries（条目明细）

| 字段 | 说明 |
|------|------|
| `entry_id` | 条目唯一 ID |
| `original` | 原文 |
| `initial_translation` | 后处理前的初始译文 |
| `refined_translation` | LLM 修复后的译文（如有） |
| `polished_translation` | LLM 润色后的译文（如有） |
| `final_translation` | 实际写回的最终译文 |
| `stage` | 最终 stage（0=打回，1=通过，2=待审） |
| `verdict` | 裁决结果：pass / reject / pending |
| `verdict_reason` | 裁决理由 |
| `confidence` | 裁决信心度（0-1） |
| `issue_count` | 该条目检测出的问题数量 |
| `issue_types` | 问题类型列表（逗号分隔） |

### Sheet 3: Issues（问题明细）

| 字段 | 说明 |
|------|------|
| `entry_id` | 关联条目 ID |
| `issue_type` | 问题类型（如 `term_mismatch`、`placeholder_missing`、`low_quality` 等） |
| `severity` | error / warning / info |
| `message` | 问题描述 |
| `suggestion` | 修复建议 |
| `original` | 原文快照 |
| `translation` | 译文快照 |

### Sheet 4: Refinements（修复明细，可选）

| 字段 | 说明 |
|------|------|
| `entry_id` | 关联条目 ID |
| `refined_translation` | 修复后的译文 |
| `confidence` | 修复信心度 |
| `fixes_applied` | 应用的修复项说明 |
| `note` | 修复器附加说明 |

### Sheet 5: Polish（润色明细，可选）

| 字段 | 说明 |
|------|------|
| `entry_id` | 关联条目 ID |
| `polished_translation` | 润色后的译文 |
| `confidence` | 润色信心度 |
| `changes` | 改动说明（维度/改动前/改动后/理由） |
| `note` | 润色器附加说明 |

---

## 文件生成机制

### 生成位置

```
data/ai_translator/{esp_stem}/reports/
```

使用与 AI 翻译数据相同的插件隔离目录，便于管理和追溯。

### 文件命名

```
{esp_stem}_post_process_report_{YYYYMMDD_HHMMSS}.xlsx
```

示例：`MyMod_post_process_report_20260413_143052.xlsx`

### 自动清理（Rotate）

报告生成器会自动保留最近 20 份报告，删除更早的历史文件，防止目录无限膨胀。

---

## 核心类与接口

### PostProcessReportGenerator

**路径**: `src/transbridge/ai_translator/post_processor/report_generator.py`

**职责**: 读取 `PostProcessResult` 及五阶段中间数据，写入 `.xlsx` 报告。

```python
class PostProcessReportGenerator:
    def generate(
        self,
        result: PostProcessResult,
        esp_stem: str,
    ) -> str:
        """
        生成后处理报告。

        Args:
            result: 后处理结果（包含 issues、execution_result、
                    refine_results、polish_results、decisions）
            esp_stem: 插件文件名（不含扩展名），用于确定输出目录和文件名

        Returns:
            生成的报告文件绝对路径
        """
```

---

## 与现有系统的集成

### 数据流

```
AutoTranslator.translate()
    │
    ▼
后处理完成 → PostProcessor.process_entries()
    │
    ▼
 enrich PostProcessResult with:
    - refine_results
    - polish_results
    - decisions
    │
    ▼
 PostProcessReportGenerator.generate()
    │
    ▼
 写入 .xlsx 到 data/ai_translator/{esp_stem}/reports/
    │
    ▼
 报告路径 → TranslationResult.pp_report_path
    │
    ▼
 UI 进度窗口展示 "打开报告" 按钮
```

### 关键修改点

| 文件 | 修改内容 |
|------|----------|
| `post_processor/base.py` | `PostProcessResult` 新增可选字段 `refine_results`、`polish_results`、`decisions`，用于在离开 `process_entries()` 后仍保留中间数据 |
| `post_processor/post_processor.py` | 在 `process_entries()` 返回前将中间字典附加到 `result` |
| `ai_translator/translator.py` | `TranslationResult` 新增 `pp_report_path: str \| None = None`；后处理完成后调用报告生成器 |
| `ui/tools/ai_translator/_translation_progress_window.py` | 翻译完成弹窗增加 "打开报告" 按钮 |
| `ui/tools/ai_translator/_batch_translation_progress_window.py` | 批量完成弹窗展示报告列表，支持打开单个报告或报告目录 |

---

## UI 交互流程

### 单插件翻译

翻译完成后弹出 `QMessageBox`：

```
┌─────────────────────────────────┐
│  翻译完成                        │
├─────────────────────────────────┤
│ 成功：XXX 条                     │
│ 失败：YYY 条                     │
│ 质量检查：ZZ 错误，WW 警告        │
│                                 │
│ 后处理报告已生成。                │
├─────────────────────────────────┤
│ [打开报告]        [确定]         │
└─────────────────────────────────┘
```

点击 **"打开报告"** 后，使用 `QDesktopServices.openUrl()`（或 `os.startfile` on Windows）打开生成的 Excel 文件。

### 批量翻译

全部插件翻译结束后：
- 若只有一个插件生成了报告：与单插件模式相同，直接提供 "打开报告" 按钮。
- 若有多个插件生成了报告：弹出列表对话框（复用 `_BatchResultDialog` 风格），列出每个插件及其报告路径，用户可单独打开某一份报告，或选择 "打开报告目录" 一键定位文件夹。

---

## 使用场景示例

### 场景 1：快速定位需复核条目

1. 打开 `Entries` Sheet。
2. 按 `verdict` 列筛选 `pending` 和 `reject`。
3. 查看 `verdict_reason` 和 `issue_types`，快速了解为何被打回或待审。

### 场景 2：检查术语一致性

1. 打开 `Issues` Sheet。
2. 按 `issue_type` 筛选 `term_mismatch`。
3. 查看 `suggestion` 列，确认标准译法。

### 场景 3：审阅润色效果

1. 打开 `Entries` Sheet。
2. 筛选 `polished_translation` 非空行。
3. 对比 `initial_translation` 和 `polished_translation`，评估润色质量。

---

## 依赖关系

```
report_generator
    │
    ├─► openpyxl（项目已有依赖）
    │
    ├─► post_processor/base（PostProcessResult）
    │
    ├─► post_processor/llm_refiner（RefineResult）
    │
    ├─► post_processor/polisher（PolishResult）
    │
    ├─► post_processor/llm_arbiter（ArbiterDecision）
    │
    └─► ai_translator/translator（TranslationResult）
```

---

## 相关文档

- [post_processor.md](post_processor.md) - 后处理模块详解
- [ai_translator.md](ai_translator.md) - AI 翻译模块
- [INDEX.md](INDEX.md) - 文档索引

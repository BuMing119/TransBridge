# Story 25 后处理工具统一 — QA 审查报告

**日期**: 2026-05-20（更新 2026-05-20 全项目健康扫描）
**审计范围**: `tool_proofreader.py` (Story 25) + `tool_translator.py` (独立润色) + 全项目导入路径扫描 → 对比 GUI `PostProcessor`
**前置审计**: [proofreader-tools-capability-audit.md](proofreader-tools-capability-audit.md) (2026-05-18)
**方案文档**: `plans/agent-tool-expansion/stories/story-25-postprocess-unification.md`
**结论**: 🔴 **Story 25 修复不完整 + 全项目发现 9 个 Blocker/Critical** — 16 项待修复

---

## 一、Story 25 设计回顾

上一轮审计（2026-05-18）发现 5 个旧工具全部调用不存在的 API，建议用统一 `run_postprocess` 替代。Story 25 按建议实现了：

| 旧审计建议 | Story 25 实现 | 
|-----------|--------------|
| 统一 `run_postprocess` 包装 `PostProcessor.process_entries()` | ✅ 已实现 `_tool_run_postprocess` |
| 创建 LLMClient + TermDatabaseManager | ✅ 代码中有创建逻辑 |
| 通过 phases 参数控制阶段 | ✅ 支持 |
| 废弃 5 个旧工具 | ✅ 已标记 deprecated，取消注册 |
| 后续：独立润色 (`start_polish`) | ✅ 存在于 `tool_translator.py` 但实现有 bug |

---

## 二、Blocker 问题（运行时崩溃）

### B1: `PostProcessor()` 构造参数错误

**文件**: `src/transbridge/smart_assistant/tools/tool_proofreader.py:93-98`
**严重级别**: 🔴 Blocker

```python
# 当前代码（错误）:
processor = PostProcessor(
    llm_client=llm_client,       # ← PostProcessor.__init__ 不接受此参数
    config=config,
    term_manager=term_mgr,       # ← PostProcessor.__init__ 不接受此参数
    esp_path=getattr(ctx, 'esp_path', None),  # ← PostProcessor.__init__ 不接受此参数
)
```

**实际签名**: `PostProcessor.__init__(self, config: PostProcessorConfig | None = None)` — 仅接受 `config`。

**后果**: `TypeError: PostProcessor.__init__() got an unexpected keyword argument 'llm_client'`，工具**启动即崩溃**，后台线程中的异常被 catch 后返回 "后处理异常" 但实际零工作完成。

**正确模式**（参考 `translator.py:632-636`）:
```python
processor = PostProcessor(pp_config)
processor.register_default_checkers(
    term_manager=term_mgr,
    llm_client=llm_client,
)
```

---

### B2: 未调用 `register_default_checkers()`

**文件**: `src/transbridge/smart_assistant/tools/tool_proofreader.py:93-98`
**严重级别**: 🔴 Blocker

即使修复 B1，`register_default_checkers()` 从未被调用。这导致：
- `self._checkers` 为空 → 阶段1 检测跳过，零问题发现
- `self._refiner` 为 None → 阶段2a 修复跳过
- `self._polisher` 为 None → 阶段2b 润色跳过
- `self._arbiter` 为 None → 阶段3 回退到基于规则的裁决 → 因零问题全部 pass
- 阶段4 → 所有条目 stage 设为 1（检查通过）

**后果**: 修复B1后工具不会崩溃，但**整个五阶段流水线静默空转**，所有条目无条件通过。`get_quality_report` 始终返回 `issue_count=0`。

---

### B3: `LLMPolisher()` 构造参数错误

**文件**: `src/transbridge/smart_assistant/tools/tool_translator.py:187`
**严重级别**: 🔴 Blocker

```python
# 当前代码（错误）:
polisher = LLMPolisher(intensity=intensity)  # intensity 不是合法参数！
```

**实际签名**: `LLMPolisher.__init__(self, llm_client: LLMClient, term_manager=None, game_profile="skyrim_se", target_lang="zh_CN", polish_level="moderate")` — 第一参数 `llm_client` 必填，参数名是 `polish_level` 不是 `intensity`。

**后果**: `TypeError: LLMPolisher.__init__() missing 1 required positional argument: 'llm_client'`，独立润色任务**启动即崩溃**。

---

### B4: `LLMPolisher` 导入路径错误

**文件**: `src/transbridge/smart_assistant/tools/tool_translator.py:186`
**严重级别**: 🔴 Blocker

```python
# 当前代码（错误）:
from src.transbridge.ai_translator.post_processor.llm_polisher import LLMPolisher
#                                              ^^^^^^^^^^^^^ 文件不存在！
```

**实际文件名**: `polisher.py`（不是 `llm_polisher.py`）。

**后果**: `ModuleNotFoundError: No module named 'src.transbridge.ai_translator.post_processor.llm_polisher'`。

---

### B5: `_tool_start_polish` 未创建 LLMClient

**文件**: `src/transbridge/smart_assistant/tools/tool_translator.py:184-196`
**严重级别**: 🔴 Blocker

即使修复 B3/B4，`LLMPolisher` 需要 `llm_client: LLMClient` 实例才能调用 LLM API。`_tool_start_polish` 中从未创建 `LLMClient`（对比 `_tool_start_translation` 通过 `AutoTranslator` → `TranslatorConfig` 间接创建，以及 `_tool_run_postprocess` 显式创建）。

**后果**: 即使修复导入和构造参数，LLM 调用无法发生，润色任务**静默失败**。

---

## 三、Critical 问题（功能严重缺失）

### C1: 缺少 Excel 报告生成

原后处理工作流（ai-post-process Story 10-13）提供：
- `ReportGenerator` 生成多 Sheet Excel：翻译模式 5 Sheet（Summary/Entries/Issues/Refinements/Arbitrations），润色模式 3 Sheet
- `ReportDialog` 多 Tab QDialog（汇总/条目/问题）
- 批量跨插件汇总
- 历史报告文件列表 + 双击打开 Excel

AI 工具现状：
- `get_quality_report` 仅返回模块级 `_last_report` 字典的文本摘要（checked/issue_count/auto_fixed + 前50条问题）
- 无 Excel 文件生成，无持久化，无历史记录
- 进程重启后所有报告丢失

**影响**: 用户无法通过 AI 助手获得结构化报告，无法导出，无法追溯历史。

---

### C2: 缺少断点续传

`PostProcessor.process_entries()` 支持 `checkpoint: PostProcessCheckpoint` 参数，在每批次完成后保存进度，中断后可恢复。AI 工具从未创建或传入 checkpoint。

**影响**: 大量条目后处理时，如遇中断无法恢复，需从头重跑（产生重复 LLM 费用）。

---

### C3: 缺少润色预览确认

原 Story-09 方案要求 `_PolishPreviewDialog`：三列对比（原文/原译文/润色结果），逐条接受/拒绝。AI 工具无等效交互。

**影响**: 润色结果直接写入（或无法写入），用户无机会审核。

---

### C4: 独立润色不加载 LLMConfig

`_tool_start_polish` 不读取 `LLMConfig` 中的润色配置（`polish_scope`/`polish_level`），仅从参数获取 `intensity`（且参数名错误）。

**影响**: 独立润色与 GUI 润色行为不一致。

---

### C5: `_tool_start_polish` 不更新 `_last_report`

**文件**: `src/transbridge/smart_assistant/tools/tool_translator.py:184-223`
**严重级别**: 🟠 Major

`_tool_start_polish` 的 `_run()` 内联函数执行润色完成后，**完全不写入模块级 `_last_report`**（grep 确认 `tool_translator.py` 中无任何 `_last_report` 引用）。对比 `_tool_run_postprocess`（`tool_proofreader.py:102-115`）在完成后详细填充 `_last_report`。

**后果**: `get_quality_report` 工具永远看不到独立润色的结果，用户无法通过 AI 助手查询润色历史。

**修复**: 在 `_tool_start_polish` 的 `_run()` 完成分支中写入 `_last_report`：
```python
import time
global _last_report  # 需要跨模块访问 tool_proofreader 中的全局变量
_last_report = {
    "phase": "polish",
    "entry_count": len(entry_ids),
    "intensity": intensity,
    "timestamp": time.time(),
}
```

---

### C6: `intensity` 参数值与 `polish_level` 不匹配

**文件**: `src/transbridge/smart_assistant/tools/tool_translator.py:187,506`
**严重级别**: 🟠 Major

工具参数 schema 定义的 `intensity` 可选值为 `light/medium/heavy`（第 506 行），但 `LLMPolisher.__init__` 的 `polish_level` 参数期望值为 `light/moderate/aggressive`（`polisher.py:176`）。即使 B3/B4/B5 全部修复，用户传入 `"medium"` 会直接透传给 `polish_level="medium"`，这不是 `LLMPolisher` 的合法值。

**后果**: `LLMPolisher` 可能因未知 `polish_level` 值而行为异常或降级到默认行为，用户意图的润色强度无法正确传递。

**修复**: 在调用 `LLMPolisher` 前做值映射：
```python
_level_map = {"light": "light", "medium": "moderate", "heavy": "aggressive"}
polish_level = _level_map.get(intensity, "moderate")
polisher = LLMPolisher(llm_client=llm_client, polish_level=polish_level)
```

---

### 其他 Minor 问题

- **M1**: `_tool_start_polish` 不支持 `translation_scope` 条目解析 — 与 `start_translation`（`tool_translator.py:69-89`）和 `run_postprocess`（`tool_proofreader.py:37-48`）不一致，用户必须手动传入 `entry_ids`。
- **M2**: `_last_report` 在 `tool_proofreader.py:89` 的 `global _last_report` 声明后从后台线程无锁写入，存在竞态条件。多任务并发时后续写入可能部分覆盖前次报告。

### 补充发现（独立验证时新增，2026-05-20）

- **E1**: `start_polish` 已注册且对 LLM 可见（translator namespace，`permission: write`，`require_confirmation: true`，`is_long_running: true`）——LLM 调用即触发 B3/B4/B5 崩溃。注册表 `tool_translator.py:539-541`，工具名 `start_polish`。在同文件已有 `start_translation mode=polish` 的情况下，两个润色入口会困惑 LLM。短期可取消注册或标记 deprecated，长期应废弃 `start_polish` 统一使用 `start_translation mode=polish`。
- **E2**: `_tool_run_postprocess` 无 API Key 前置检查。对比 `_tool_start_translation`（`tool_translator.py:40-65`，MA11 检查），`run_postprocess` 直接调用 `LLMConfig.load_from_file()` + `create_llm_client(llm_cfg)` 而不检查 `api_key` 是否配置。无 Key 时用户看到的是后端异常 `"后处理异常: ..."` 而非友好提示。建议在 `_tool_run_postprocess` 开头添加 API Key 检查。

---

## 四、功能覆盖矩阵

| 原后处理功能 | Story | GUI | `run_postprocess` | `start_polish` |
|-------------|-------|-----|:---:|:---:|
| 术语一致性检查 | S01 | ✅ | ❌ B1/B2 | — |
| 格式校验 | S02 | ✅ | ❌ B1/B2 | — |
| 质量门禁 (LLM) | S03 | ✅ | ❌ B1/B2 | — |
| LLM 修复 | S04 | ✅ | ❌ B1/B2 | — |
| LLM 润色（流水线内） | S05 | ✅ | ❌ B1/B2 | — |
| LLM 裁决 | S06 | ✅ | ❌ B1/B2 | — |
| 五阶段协调 | S07 | ✅ | ❌ B1/B2 | — |
| 独立润色入口 | S09 | ✅ | — | ❌ B3/B4/B5 |
| Excel 报告生成 | S10 | ✅ | ❌ C1 | — |
| 报告对话框 | S11 | ✅ | ❌ C1 | — |
| 完成流程集成 | S12 | ✅ | ❌ C1 | — |
| 历史报告查看 | S13 | ✅ | ❌ C1 | — |
| 断点续传 | S07 | ✅ | ❌ C2 | — |
| 润色预览确认 | S09 | ✅ | ❌ C3 | — |

---

## 五、修复建议

### Phase A: 修复 Blocker（使工具能运行）

**A1 — `tool_proofreader.py`** (修复 B1 + B2):

```python
# 替换行 93-98:
# 错误:
# processor = PostProcessor(llm_client=llm_client, config=config, term_manager=term_mgr, esp_path=...)
# 正确:
processor = PostProcessor(config)
processor.register_default_checkers(
    term_manager=term_mgr,
    llm_client=llm_client,
)
```

**A2 — `tool_translator.py`** (修复 B3 + B4 + B5):

```python
# 修复行 186 (导入路径):
from src.transbridge.ai_translator.post_processor.polisher import LLMPolisher

# 修复行 184-196 (创建 LLMClient + 正确参数):
def _run():
    try:
        from src.transbridge.ai_translator.post_processor.polisher import LLMPolisher
        from src.transbridge.infra.llm_client import create_llm_client
        from src.transbridge.paratranz.config_manager import LLMConfig
        
        llm_cfg = LLMConfig.load_from_file()
        llm_client = create_llm_client(llm_cfg)
        polisher = LLMPolisher(
            llm_client=llm_client,
            polish_level=intensity,  # light/moderate/aggressive
        )
        ...
```

### Phase B: 修复 Major 问题（C5/C6）

**B4 — `tool_translator.py`** (修复 C5):
```python
# 在 _tool_start_polish 的 _run() 完成分支中，tm.set_status(task_id, "completed") 之后添加:
import time
from src.transbridge.smart_assistant.tools.tool_proofreader import _last_report
_last_report.update({
    "phase": "polish",
    "entry_count": len(entry_ids),
    "intensity": intensity,
    "timestamp": time.time(),
})
```
> 注意：跨模块访问全局变量不是最佳实践。更好的方案是将 `_last_report` 提取到共享模块或 TaskManager 中，但这超出本 Story 范围。

**B5 — `tool_translator.py`** (修复 C6):
```python
# 在创建 LLMPolisher 之前添加值映射:
_level_map = {"light": "light", "medium": "moderate", "heavy": "aggressive"}
polish_level = _level_map.get(intensity, "moderate")
polisher = LLMPolisher(llm_client=llm_client, polish_level=polish_level)
```

### Phase C: 补全功能（后续迭代）

- **C1**: 报告生成 — 在 `_tool_run_postprocess` 完成后调用 `ReportGenerator` 生成 Excel，`get_quality_report` 返回文件路径
- **C2**: 断点续传 — 创建 `PostProcessCheckpoint` 并传入 `process_entries()`
- **C3**: 润色预览 — 短期方案：`start_polish` 完成后返回对比数据，LLM 可通过后续 `edit_translation` 逐条确认；长期方案：实现 HITL 预览协议
- **M1**: `start_polish` 支持 `translation_scope` 条目解析
- **M2**: `_last_report` 加线程锁保护

---

## 六、审查结论

| 维度 | 评分 | 说明 |
|------|:---:|------|
| 方案一致性 | ❌ | Story 25 设计正确（统一 `run_postprocess`），但实现偏离了 API 调用模式 |
| 代码质量 | ❌ | 5 个 Blocker 全部是未验证 API 签名导致的，`start_polish` 的导入路径和参数名均错误；2 个 Major 为数据流断裂和参数值映射缺失 |
| 功能完整度 | ❌ | 核心五阶段流水线不工作 + 报告/断点/预览全部缺失 + 润色结果不可查询 |
| 安全性 | ✅ | 无明显安全问题（工具本身未执行，LLMConfig/API Key 处理与现有模式一致） |

### 签名

**QA 未通过** — 5 Blocker + 6 Critical/Major + 2 Minor 待修复后复验

> **审计附注** (2026-05-20): 本报告经独立验证，所有 Blocker/Critical 问题均通过读取源代码 API 签名确认属实。本次更新新增 C5（`_last_report` 不更新）、C6（`intensity` 值映射断裂）、M1（scope 不一致）、M2（线程竞态）四项遗漏问题。

### 更新: 2026-05-20 — 全项目健康扫描新增发现

以下问题**不在**原 QA 报告范围内，通过全项目导入路径扫描和模块验证发现：

---

## 七、全项目扫描新增发现

### N1: `paratranz.api_client` 导入路径不存在（6 处）

**严重级别**: 🔴 Critical

所有 PT 工具和 `start_translation` 中的 lazy import 使用了不存在的模块路径：

```
from src.transbridge.paratranz.api_client import ParatranzClient  # 模块不存在
```

实际类定义在 `paratranz/paratranz_client.py`，通过 `paratranz/__init__.py` 重新导出。

**受影响位置**:
- `tool_paratranz.py:13` (`_get_paratranz_client`)
- `tool_paratranz.py:24` (`_get_paratranz_client` 内部 `ParatranzClient(ctx.config)`)
- `tool_paratranz.py:180` (`_tool_get_paratranz_project`)
- `tool_paratranz.py:195` (`_tool_switch_paratranz_project`)
- `tool_translator.py:112` (`start_translation` 的 PT 术语客户端)

**后果**: 全部 9 个 PT 工具 + `start_translation` 的 PT 术语功能在运行时 `ModuleNotFoundError` 崩溃。**这是目前影响面最广的 bug。**

**修复**: 改为 `from src.transbridge.paratranz import ParatranzClient`

---

### N2: `parser.sst_parser` 导入路径错误

**严重级别**: 🔴 Critical

**文件**: `tool_parser.py:208`
```python
from src.transbridge.parser.sst_parser import SST_Parser  # 路径错误
```

实际路径: `src/transbridge/parser/xt/sst_parser.py`

**后果**: `parse_sst` 运行时 `ModuleNotFoundError` 崩溃。

**修复**: 改为 `from src.transbridge.parser.xt.sst_parser import SST_Parser`

---

### N3: `parser.strings_importer` 模块完全不存在

**严重级别**: 🔴 Critical

**文件**: `tool_parser.py:254`
```python
from src.transbridge.parser.strings_importer import StringsImporter  # 模块不存在
```

经全项目文件系统扫描，`StringsImporter` 类及其所在模块 `strings_importer.py` **完全不存在**于代码库中。

**后果**: `import_strings` 工具**从未工作过**——最初实现时使用了不存在的类名。

**修复**: 需创建 `StringsImporter` 类，或改用项目中实际存在的 `.strings` 导入机制（如 `TranslationEntryCollection` 的 `update_from_strings_lookup` 或 `PluginStringsLookup`）。

---

### N4: orchestrator Agent 引用错误的 namespace

**严重级别**: 🟡 Medium

**文件**: `agent_registry.py:92`
```python
tools=["default:*", "editor:get_visible_entries", "editor:get_statistics", ...]
```

`get_statistics` 注册在 `default` namespace（`tool_default.py`），而非 `editor` namespace。

**后果**: orchestrator Agent 通过 `editor:get_statistics` 无法找到该工具，统计查询功能不可用。

**修复**: `"editor:get_statistics"` → `"default:get_statistics"`

---

## 八、修订后的审查结论

| 维度 | 评分 | 说明 |
|------|:---:|------|
| 方案一致性 | ❌ | B1/B2 导致 `run_postprocess` 不工作，N2/N3 导致 2 个 parser 工具不工作 |
| 代码质量 | ❌ | 9 个 Blocker/Critical（含 4 个导入路径错误）均为未验证签名或路径导致 |
| 功能完整度 | ❌ | 核心五阶段流水线不工作 + PT全部崩溃 + 2个parser崩溃 + 报告/断点/预览缺失 |
| 安全性 | ✅ | 无明显安全问题 |

### 修订后签名

**QA 未通过** — 5 Blocker + 3 Critical + 6 Major + 2 Minor = **16 项待修复**

| 批次 | 问题 | 数量 | 修复文件 |
|------|------|:---:|------|
| B1-B2 | `run_postprocess` 不工作 | 2 | `tool_proofreader.py` |
| B3-B5+N1 | `start_polish` + PT 全部崩溃 | 5+1 | `tool_translator.py`, `tool_paratranz.py` |
| N2-N3 | `parse_sst` + `import_strings` 崩溃 | 2 | `tool_parser.py` |
| N4 | orchestrator namespace 错误 | 1 | `agent_registry.py` |
| C5-C6 | `_last_report` 不更新 + intensity 映射 | 2 | `tool_translator.py` |
| C1-C4+M1-M2 | 功能缺失 + 设计不一致 | 6 | 后续迭代 |
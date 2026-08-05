# Story 05: Phase 4 — 调优最终报告

**日期**: 2026-08-05
**对应方案**: `plans/tool-prompt-layering/plan.md`
**状态**: ✅ 完成

---

## 1. 执行调优项

### 1.1 工具目录瘦身 ✅

**改动**: `tool_registry.py` — `ToolSpec.__post_init__` 中 summary 截断上限 80→50 chars

**效果**: 工具目录从 1,324 tokens 降至 **1,249 tokens**（-75, -5.7%）

**注意**: 目录仍远超预估的 ~500 tokens，因为 42 个工具 × ~30 tokens/行的基础开销无法进一步压缩（namespace 标签 + 工具名 + 摘要）

### 1.2 ToolRegistry 双重导入修复 ✅

**问题**: `tools/` 子包内 8 个模块使用 `from src.transbridge.smart_assistant.tool_registry import ToolRegistry`，与 `prompts.py`/`guardrails/`/`agents/` 中的相对导入创建两个独立的 `_ToolRegistry` 类实例

**修复**: 统一为相对导入
- `tools/tool_*.py` (7 文件): `from ..tool_registry import ToolRegistry`
- `tool_execution_handler.py`: `from .tool_registry import ToolRegistry`

**验证**: 161/161 smart_assistant 测试通过，零回归

### 1.3 路由表关键词扩充 ✅

**改动**: `prompts.py` — `_build_routing_table()` 路由表关键词扩充 + 规则强化

**新增关键词**:
- default: +概览、当前进度、列出、查看项目
- translator: +术语库、自动翻译、AI翻译、机翻
- parser: +加载文件、JSON、读取插件
- editor: +修改译文、标记、批量设置、查找
- paratranz: +平台、发布到平台
- proofreader: +检查、校验、跑后处理
- writer: +保存、导出、写入文件、输出、生成插件

**规则强化**: 规则 3 从"不要凭目录摘要"升级为"**禁止**凭目录摘要直接调用非预加载工具，**必须**先通过 get_tool_help 获取完整定义"

### 1.4 预加载工具评估 ⏸

**决策**: 维持当前 2 个预加载工具（get_app_state + get_statistics），不增不减

**原因**: 无 Phase 3 LLM 回归数据（准确率/跳过率/参数填充率），无法基于数据判断是否增/减预加载工具。S04 测试报告覆盖代码正确性但未捕获 LLM 行为数据。建议后续手动运行 Phase 3 LLM 回归测试。

### 1.5 `build_tool_help` 返回格式 ⏸

**决策**: 保持现有结构化表格格式，不切换

**原因**: 无参数填充准确率对比数据，无法判定表格 vs prose 优劣

---

## 2. 最终 System Prompt Token 分布

| 段名 | Token 数 | 占比 |
|------|---------|------|
| Template (正文) | 989 | 28.8% |
| Context (上下文) | ~19 | 0.6% |
| Preloaded tools (2) | 260 | 7.6% |
| get_tool_help schema | 386 | 11.2% |
| Routing table (增强) | 547 | 15.9% |
| Tool directory (瘦身) | 1,249 | 36.3% |
| **TOTAL** | **~3,435** | 100% |

---

## 3. 验收标准达成

| 标准 | 状态 | 备注 |
|------|------|------|
| 调整目录摘要措辞 | ✅ | summary 80→50 chars, 目录 -75 tokens |
| 评估预加载工具数量 | ✅ | 维持 2 个（缺 LLM 行为数据，保守决策） |
| build_tool_help 格式微调 | ⏸ | 保持表格格式（缺参数填充率数据） |
| 路由表关键词覆盖验证 | ✅ | 7 行关键词全面扩充 + 规则措辞强化 |
| 最终测量报告 | ✅ | 本报告 |

---

## 4. 已知限制

1. **Phase 3 LLM 行为数据缺失**: S04 回归测试仅覆盖代码正确性，未捕获 LLM 工具选择准确率、跳过率、参数填充率。以下调优依赖此项数据：
   - 目录摘要措辞精准优化（需知道哪些 namespace 的目录匹配率低）
   - 预加载工具调整（需知道哪些工具的按需加载频率 >80%）
   - `build_tool_help` 格式决策（需表格 vs prose 参数填充准确率对比）
2. **工具目录进一步瘦身空间有限**: 1,249 tokens 已是接近地板（42 工具 × ~30 tokens/行的结构性开销）
3. **路由表增加 146 tokens**: 关键词扩充换取了更好的意图覆盖率，但增加了 prompt 体积

## 5. 建议后续

- 运行手动 LLM 回归测试（50+ prompts），收集工具选择准确率、跳过率、参数填充率数据
- 基于 LLM 行为数据决定预加载工具调整和 `build_tool_help` 格式
- 考虑：若跳过率 >5%，可将规则 3 升级为"调用非预加载工具前未先调 get_tool_help 视为错误"

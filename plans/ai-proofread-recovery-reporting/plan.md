# AI 校对恢复与报表收口计划

- **状态**：已完成（3/3）
- **日期**：2026-08-27
- **关联**：`ai-compact-proofread-custom-profiles`、`ai-post-process` Story 10～14

## 目标

将原产品名 `combined` 统一为“校对 / Proofread”，为校对阶段增加有界的格式恢复、失败子集重试和拆批降级，并让失败统计只表示恢复结束后仍失败的条目。补齐混合流程的用户可见报表收口。

## 非目标与兼容边界

- 不破坏已持久化的 `pp_strategy="combined"` 和自定义流程 schema；旧值作为 legacy compatibility alias 可读，读取后归一为 `proofread`，新写入不再产生 `combined`。
- 不对 LLM 输出做激进 JSON 修补；只移除完整的 Markdown fence 或 `<think>` 包装。
- 不无限重试；每个原批次使用固定的恢复上限，取消不重试。
- 不新建第二套报表后端，继续使用 canonical `ReportSnapshot` 和现有 JSON/CSV/XLSX 渲染器。

## Story 1：产品命名与兼容别名

### 验收标准

- UI、进度、诊断和新公开代码名统一使用“校对 / Proofread”，不再显示“combined proofread”或“一次校对润色”。
- 新入口使用 `ProofreadStage` 和 `enable_proofread`；旧 `CombinedProofreadStage` 和 `enable_combined_proofread` 仍可用。
- 旧 `combined` 配置与自定义 profile 无需用户操作即归一为 `proofread` 并产生等价流程。

## Story 2：校对失败恢复与最终统计

### 验收标准

- 完整 JSON fence 和 `<think>` 包装可在本地恢复，不额外请求 LLM。
- Provider 调用失败会同批重试一次；有效响应中只重试缺失、重复、空值或保护语法损坏的条目。
- 恢复响应整体仍无效时有界二分一次，不会无限请求；取消立即收口。
- 已恢复条目不保留 ERROR/EXTERNAL 诊断；结果、进度与报表中的失败数只计最终失败条目。
- 所有最终失败条目保留原译，并有可操作诊断。

## Story 3：混合流程报表可见收口

### 验收标准

- 混合流程完成后直接展示 canonical “混合运行报告”，不再只弹出文本摘要。
- 无预览流程复用已渲染 artifacts；预览流程在用户决策后以 pending 状态打开，后台完成后更新。
- 报表渲染失败只降级为报表诊断，不把已完成的翻译/校对业务改判为失败。
- 混合报表继续进入现有历史报告目录，Excel 和失败条目动作可用。

## 验证策略

- 聚焦：`tests/application/translation/test_proofread_stage.py`、proofread pipeline / profile 契约、`tests/contracts/translation/test_mixed_report.py`、AI report UI 测试。
- 扩大：AI translator 与 `tests/ui/tools` 相关回归。
- 静态：Ruff check/format（环境可用时）与 `git diff --check`。

## 风险与回退

- 恢复会增加失败批次的请求量；通过“一次子集重试 + 一层二分”封顶，并继续受共享并发预算和取消信号约束。
- 机器值改为 `proofread` 会使旧运行档案 digest 失效；这是有意的安全迁移，避免在已增加恢复语义的新流程中复用旧 checkpoint。

## 完成证据

- Story 1：产品主路径、UI、进度与现行文档统一为“校对 / Proofread / `proofread`”；旧 `combined` 只保留在显式兼容入口，配置与 profile 保存边界统一写出 `proofread`。
- Story 2：实现保守包装清理、失败子集单次重试和整体畸形时的一层二分；每原始批次最多 4 次逻辑调用，取消不重试，失败统计基于恢复后的最终候选。
- Story 3：混合流程完成后展示 canonical 混合报告；预览后台渲染会回填 artifact、诊断和结果动作，渲染失败只作为报告诊断。
- 聚焦验证：72 项通过；扩大回归：327 项通过；Smart Assistant 校对路径：49 项通过；`python -m compileall -q src/transbridge` 与 `git diff --check` 通过。
- Ruff 未执行：当前 `.venv` 指向缺失的 uv 管理 Python 3.12.12，系统 Python 环境也未安装 Ruff。

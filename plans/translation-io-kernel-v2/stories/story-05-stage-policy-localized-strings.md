# Story 05：StagePolicy 与 Localized Strings 数据完整性

- 所属 Plan：[Translation I/O Kernel V2](../plan.md)
- 状态：实现完成，综合 QA 通过（2026-08-18）
- 追溯：FR18.9、FR23.3；ADR-017；R-016/R-017
- 依赖：S01/S02/S04

## 目标与验收

七级 Stage 使用离散策略；hidden 写原文且不进 AI，locked 不进 AI，locked+空译文阻断正式发布而 preview 显示原文与 blocking diagnostic；Localized Strings 未修改 ID/编码/顺序不丢失。

## 接口与数据流

`StagePolicy.evaluate(stage, translation, operation)` → `StageDecision(include_ai, preview_text, publish_text?, severity/code)`。STRINGS parse 保存完整 `SourceSnapshot`：variant、encoding、原始 id→bytes/text、顺序/offset 元数据；ChangeSet 只替换目标 id，writer 从 snapshot 重建而非从“有译文条目”开始。

## 实施步骤

1. 定义 Stage enum 与 translate/preview/publish/TM 操作矩阵，禁止 `>=` 推断。
2. 让 AI/PostProcess/TM/FOMOD 通过 port 调用 policy，先保留旧常量 facade。
3. 扩展 PluginParser snapshot 捕获 STRINGS/DLSTRINGS/ILSTRINGS 全映射与 locator。
4. Writer 对未修改、hidden、空译文和 locked 按 decision 写出；locked-empty 正式发布返回 blocking result。
5. 支持矩阵与 UI capability 展示读取同一 policy version。

## 边界、迁移与测试

未知 stage 不是 translated，返回 invalid diagnostic；格式无法表达精确 stage 时报告 lossy mapping。测试对 -1/0/1/2/3/5/9 全组合参数化，覆盖 preview/publish/AI/TM；使用真实三类 strings fixtures 做 byte/semantic golden、未翻译 ID 保留、BOM/Unicode、重复 ID 和 locked-empty 阻断。回退可切回旧 UI 展示，不可回退发布安全策略。

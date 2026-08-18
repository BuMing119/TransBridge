---
name: bm-help
description: 解释当前可用的 BM 系列 skill、推荐适合用户场景的入口，并说明 Codex 中的调用方式和精简工作流。用户询问“该用哪个 bm skill”、BM 命令帮助、流程选择或各 skill 区别时使用。
---

# BM 使用帮助

## 目标

根据用户当前目标推荐最短可行路径，而不是输出固定的长篇命令手册。

## Codex 调用方式

优先用 $bm-name 显式调用 skill，例如 $bm-dev、$bm-plan。用户用自然语言点名 skill 也可以触发。不要声称存在独立的 Skill(...) 调用工具或依赖其他智能体产品的 slash-command 运行时。

## 工作流

1. 若用户描述了具体场景，先推荐一个主 skill，必要时再列一个备选；说明选择原因和预期产物。
2. 若用户只问总览，动态扫描 .agents/skills/bm-*/SKILL.md 的 frontmatter，避免硬编码数量或过期说明。
3. 只展开用户关心的 skill。每项说明：何时用、会不会写文件、主要产物、下一步。
4. 提醒用户：多数任务可以直接调用目标 skill；缺少 .codex/bm_config 不会阻断除 bm-init 外的 skill。
5. 若用户不确定当前项目处于哪一阶段，推荐 bm-orchestrator；若希望在同一 Codex 任务里持续推进完整流程，推荐 bm-pilot。

## 常用入口

- bm-init：创建或检查 BM 配置和文档目录。
- bm-analyze：澄清需求并可沉淀 requirements。
- bm-arch：处理跨模块设计、技术选型和 ADR。
- bm-plan：形成 Story 级实现方案。
- bm-story / bm-story-batch：把一个或一批 Story 展开成实施指南。
- bm-dev：直接实现功能或修复缺陷。
- bm-dev-serial：按依赖顺序串行委派大型实现。
- bm-qa：测试、验证或代码审查。
- bm-chronicle：按真实 diff 记录增量。
- bm-git：查看、规划或执行 Git 提交。
- bm-council：需要独立专家意见时进行多视角评议。
- bm-orchestrator：只读判断进度和下一步。
- bm-pilot：在当前任务中执行多阶段自动流程。

## 推荐流程

- 小修复：bm-dev → 按需 bm-qa → 用户要求时 bm-git。
- 常规功能：bm-analyze（可选）→ bm-plan → bm-dev → bm-qa。
- 跨模块或新技术：bm-analyze → bm-arch → bm-plan → bm-dev → bm-qa。
- changelog 不是每一步的强制门禁；项目或用户要求审计记录时调用 bm-chronicle。

## 输出风格

保持简短、场景化。不要强制展示所有 skill、巨型表格或 ASCII 流程图；用户要求详细手册时再展开。

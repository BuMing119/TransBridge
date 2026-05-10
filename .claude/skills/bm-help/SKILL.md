---
name: bm-help
description: 显示所有可用 skill 的说明与使用场景，帮助用户快速决定该用哪个命令
---

# /bm-help — Skill 使用指南

## 执行指令

当用户调用 `/bm-help` 时，你**必须**执行以下操作：

1. **发送总览介绍**：使用下面的"总览"表格，向用户展示所有 13 个 skill 的名称、角色和一句话说明。这是必须执行的，不可跳过。

2. **根据用户场景推荐**：阅读用户在 `/bm-help` 后面的参数或问题，从"我该用哪个命令？"表格中匹配场景，给出具体推荐。如果用户没有附带具体问题，则提供"我该用哪个命令？"的完整场景速查表。

3. **解释用法**：针对用户关心的 skill，从"Skill 一览"中提取对应详情（作用、何时用、调用方式、核心规则、下一步）进行解释。如果用户没有指定具体 skill，至少介绍 `/bm-orchestrator`（全局总控入口）和 `/bm-dev`（最常用编码入口）两个核心 skill。

4. **展示标准工作流**：用简化的流程图展示典型开发流程路径，让用户知道从哪开始、下一步去哪。

5. **禁止行为**：不得跳过总览直接回答；不得仅输出"请使用 /bm-xxx" 而不解释该 skill 的用法。

---

TransBridge 项目共有 **13 个 BM 系列 skill**，覆盖从需求分析到代码提交的完整开发流程：

| # | 命令 | 角色 | 一句话说明 |
|---|------|------|-----------|
| 1 | `/bm-orchestrator` | 编排器 | 全局总控，评估复杂度、展示进度、推荐/自动执行下一步 |
| 2 | `/bm-analyze` | 需求分析师 | 澄清需求边界，输出 `docs/requirements.md` |
| 3 | `/bm-arch` | 架构师 | 技术选型、目录结构、核心接口设计，输出 ADR |
| 4 | `/bm-plan` | 方案策划师 | 编写实现方案，定义 Story 清单，输出 `plans/<feature>/plan.md` |
| 5 | `/bm-story` | Story 展开 | 将单个 Story 细化为详细实现指南（含伪代码/测试策略） |
| 6 | `/bm-story-batch` | 批量展开器 | 串行调用 `/bm-story` 逐个展开全部 Story |
| 7 | `/bm-council` | 评审委员会 | 多角色圆桌讨论，输出纪要和建议，不强制结论 |
| 8 | `/bm-dev` | 代码开发者 | 专注编码，支持严格执行（有方案）和灵活开发（无方案）两种模式 |
| 9 | `/bm-chronicle` | 修改记录员 | 按 Epic→Story 记录每次增量变更，append-only |
| 10 | `/bm-qa` | 测试审查员 | 编写测试、运行验证、代码审查，问题分四级 |
| 11 | `/bm-git` | Git 管家 | 分析变更、分组规划提交、生成中文 commit message、安全执行 |
| — | `multi-agent-pattern` | 内部模板 | 同角色多 Agent 并行编排模式，供各 skill 内部参考，不直接调用 |

> **典型流程**：`analyze → plan → (arch → story →) dev → chronicle → qa → git`，或直接 `/bm-orchestrator --auto` 全自动推进。

---

## 我该用哪个命令？

| 你的场景 | 推荐命令 | 说明 |
|---------|---------|------|
| 刚想到一个新功能/需求，不知道从哪开始 | `/bm-orchestrator` | 评估复杂度、展示进度看板、推荐下一步 |
| 想全自动推进开发流程 | `/bm-orchestrator --auto` | 自动驾驶模式，按阶段自动调用各 skill，仅在确认点暂停 |
| 想手动从需求分析开始 | `/bm-analyze` | 澄清需求边界、识别隐性需求，输出 `docs/requirements.md` |
| 需求已确认，需要写实现方案 | `/bm-plan` | 输出 `plans/<feature>/plan.md`，定义 Story 清单，支持多实例并行 |
| 方案已有，复杂 Story 需要细化实现细节（单个） | `/bm-story` | 输出详细实现指南，含数据流/伪代码/边界条件/测试策略 |
| 方案已有，需要一次性展开全部 Story | `/bm-story-batch` | 串行调用 `/bm-story`，用户逐个确认，Phase 间可暂停 |
| 方案涉及技术选型、目录调整、核心接口 | `/bm-arch` | 输出 ADR，支持多实例并行（数据/API/部署/安全各自独立决策） |
| 直接开始编码（已有方案） | `/bm-dev <feature>` | 严格执行模式，按 plan 逐 Story 编码 |
| 修 bug / 改配置 / 小功能迭代（无方案） | `/bm-dev 我要修复xxx` | 灵活开发模式，口头确认修改点后直接编码 |
| 刚完成一个 Story 的编码 | `/bm-chronicle` | 记录变更到 `docs/changelogs/<epic>/<story>/`，append-only |
| 编码完成，需要测试和审查 | `/bm-qa` | 编写测试、运行验证、代码审查，支持多维度并行（功能/安全/性能/质量） |
| 方案/执行结果想多角色讨论 | `/bm-council` | 多角色圆桌评审，过程完全透明，不强制结论 |
| 代码已完成，需要提交到 Git | `/bm-git` | 分析变更、按功能分组规划提交、生成中文 commit message、安全执行 |
| 只想快速查看仓库状态 | `/bm-git --status` | 仅扫描输出状态概览，不规划不提交 |

---

## Skill 一览

### `/bm-orchestrator` — 开发流程编排器（全局总控）

- **作用**：评估需求复杂度（极简/标准/复杂），扫描项目进度，推荐或自动执行下一步 skill
- **何时用**：不确定该走哪个流程、想看当前进度、或想一键自动推进全流程时
- **三种调用方式**：
  - `/bm-orchestrator 我要实现 xxx` — 传入需求，评估复杂度并推荐起点
  - `/bm-orchestrator` — 无参数，扫描当前项目状态，输出进度看板并推荐下一步
  - `/bm-orchestrator 我要实现 xxx --auto` — 自动驾驶模式：确认路线后自动按阶段调用各 skill，仅在确认点和门禁处暂停
- **核心规则**：不写文档、不改代码、不运行测试（由下游 skill 执行）；不做技术决策；每次调用都重新扫描推断；记录门禁是强制性的
- **下一步**：按推荐调用对应 skill，或加 `--auto` 让编排器自动推进

### `/bm-dev` — 代码开发者

- **作用**：专注编码实现，支持两种模式
- **模式一：严格执行模式** — `/bm-dev <feature>`，按 `plans/<feature>/plan.md` 逐 Story 编码，不修改 plan 未列出的文件
- **模式二：灵活开发模式** — `/bm-dev 我要实现/修复 xxx`，适用于修 bug、改配置、小功能迭代，口头确认修改点后编码
- **核心规则**：不调用任何其他 skill；不写 changelog、不更新索引（留给 chronicle）；编码完成后必须调用 `/bm-chronicle` 记录
- **下一步**：编码完成 → `/bm-chronicle` → `/bm-qa`

### `/bm-analyze` — 需求分析师

- **作用**：澄清需求边界、识别隐性需求和异常场景，输出 `docs/requirements.md`
- **何时用**：用户提出新功能、新需求或需求变更时
- **双重确认机制**：写文档前归纳确认（必须用户明确同意）→ 写文档 → 写后告知
- **产物**：追加到 `docs/requirements.md` 的结构化需求条目
- **下一步**：需求确认后 → `/bm-plan`

### `/bm-plan` — 方案策划师

- **作用**：基于已定架构编写具体功能实现方案，定义 Story 清单与实现步骤
- **何时用**：需求已确认后，或已有功能需要重构方案时
- **核心规则**：**无方案不改代码**；不重新做技术选型（引用 ADR）；支持单实例/多实例并行两种模式
- **双重确认机制**：写文档前呈现方案骨架供确认 → 写文档 → 写后再次确认
- **产物**：`plans/<feature>/plan.md`
- **下一步**：复杂 Story → `/bm-story` 或 `/bm-story-batch`；简单 Story → `/bm-dev`

### `/bm-story` — Story 细节展开

- **作用**：将 plan 中的单个 Story 展开为详细实现指南，含数据流、边界条件、伪代码、测试策略
- **何时用**：plan 已确认后，Story 跨多文件或数据流复杂时（可选步骤，简单 Story 可跳过）
- **参数**：`/bm-story <plan> <story-id>` 展开指定 Story；支持 `--batch` 非交互模式（供 `/bm-story-batch` 调用）
- **产物**：`plans/<feature>/stories/story-<NN>-<slug>.md`
- **核心规则**：只展开不编码；不修改 plan 验收标准；所有技术决策引用 ADR
- **下一步**：确认后 → `/bm-dev` 按详细指南编码

### `/bm-story-batch` — 批量 Story 展开编排器

- **作用**：串行调用 `/bm-story` 逐个展开全部 Story，用户正常交互确认每个 Story，自动推进到下一个
- **何时用**：plan 已确认且 Story 数量多，不想手动逐个调 `/bm-story` 时
- **参数**：`/bm-story-batch <plan>` 展开单个 plan；`/bm-story-batch all` 展开全部 plan
- **核心规则**：不自己写文档，完全委托 `/bm-story`；Phase 间可暂停；串行不并行
- **下一步**：全部展开后 → `/bm-dev` 按详细指南编码

### `/bm-arch` — 架构师

- **作用**：技术选型、目录结构、核心接口/数据流设计、跨模块契约冻结
- **何时用**：方案涉及技术选型、目录调整、核心接口设计时；是 `/bm-plan` 的前置步骤之一
- **双重确认机制**：写 ADR 前呈现架构草案和备选方案对比表 → 用户选择方案 → 编写 ADR → 写后确认
- **支持多实例并行**：当涉及 3+ 个正交架构层面（如数据、API、部署、安全），可并行 spawn 多个 architect 各自决策
- **产物**：`docs/adr/adr-<编号>-<slug>.md`
- **核心规则**：涉及技术选型必须列出备选方案对比；ADR 是 plan 的权威输入；plan 阶段不得重新做技术选型
- **下一步**：架构确认后 → `/bm-plan`

### `/bm-chronicle` — LLM 修改记录员

- **作用**：按 Epic→Story 分层记录每次增量，输出独立文件到 `docs/changelogs/<epic>/<story>/YYYY-MM-DD-NNN-简述.md`
- **何时用**：每完成一个 Story 后必须调用；阶段性编码中间也可调用
- **产物**：增量文件 + 同步更新 `docs/changelogs/INDEX.md`、`plans/INDEX.md`、`docs/INDEX.md`
- **核心规则**：append-only，永不删改已有内容；Story 必须在 plan 中预定义

### `/bm-qa` — 测试审查员

- **作用**：编写测试、运行验证、代码审查
- **何时用**：编码完成后，或需要验证某功能时
- **支持多实例并行**：当需要多维度深入审查时，可并行 spawn 多个 QA Agent（功能测试/安全审查/性能审查/代码质量），各维度独立产出后汇总
- **问题分级**：Blocker / Critical / Major / Minor 四级；Blocker/Critical 必须等待用户决策
- **产物**：`docs/test-reports/<feature>.md`
- **核心规则**：QA 不直接修改业务代码，只出报告标问题；修复后需重新 `/bm-qa` 复验

### `/bm-git` — Git 版本管家

- **作用**：分析变更文件，按功能区域分组规划提交，生成符合项目规范的中文 commit message，安全执行 git 操作
- **何时用**：编码和测试完成后需要提交代码时；或想查看仓库当前状态时
- **四种调用方式**：
  - `/bm-git` — 完整扫描并规划提交，用户确认后执行
  - `/bm-git --status` — 仅扫描，输出仓库状态概览（暂存/未暂存/未跟踪、功能区域分布），不规划不提交
  - `/bm-git --auto` — 扫描规划后自动执行用户确认的提交序列
  - `/bm-git --push` — 提交完成后推送到远程
- **核心规则**：绝不使用 `git add -A` 或 `git add .`；绝不跳过 hooks；绝不 amend；绝不 force push 到 main/master；选择性暂存，一个逻辑功能一个提交
- **产物**：有序的提交计划 + 执行提交
- **下一步**：提交完成 → 可推送或进入下一轮开发

### `/bm-council` — 评审委员会

- **作用**：多角色圆桌讨论方案或执行结果，输出纪要和建议
- **何时用**：方案完成后想多角色把关、执行后想综合评估、或争议仲裁时
- **评审角色池**：架构师 / 开发者 / QA / 安全专家 / 产品经理，根据评审对象自动选择 2-5 个角色
- **过程完全透明**：每个角色的独立意见在汇总前完整展示给用户
- **产物**：`docs/council-review-<对象>.md`
- **核心规则**：只输出讨论纪要和建议，**不强制结论**；最终决策权归用户

---

## 标准工作流顺序

```
新需求
  ├─ 方式一：全自动 ──→ /bm-orchestrator 我要实现 xxx --auto
  │                     编排器自动按阶段推进，仅在确认点暂停
  │
  └─ 方式二：手动推进（推荐先调用 /bm-orchestrator 看进度）
        → /bm-analyze（需求分析）
          → /bm-plan（方案策划）
            → /bm-arch（架构设计，可选——涉及技术选型时推荐）
              → /bm-story（Story 细化，可选——复杂 Story 推荐）
              → /bm-story-batch（批量 Story 展开，多 Story 时推荐）
                → /bm-council（方案评审，可选）
                  → /bm-dev（编码）
                    → /bm-chronicle（记录增量，必须）
                      → /bm-qa（测试审查）
                        → /bm-council（执行评审，可选）
                          → /bm-git（提交代码）
                            → 完成
```

**简化路径**：
- 修复 bug / 改配置 → `/bm-dev 我要修复xxx`（灵活模式，跳过分析/方案阶段），完成后 `/bm-chronicle` → `/bm-qa` → `/bm-git`
- 已有方案想直接编码 → `/bm-dev <feature>`（严格执行模式），编码后 `/bm-chronicle` → `/bm-qa` → `/bm-git`
- 想看进度 / 不确定下一步 → `/bm-orchestrator`（无参数扫描）
- 只想快速提交代码 → `/bm-git`（扫描、规划、提交）或 `/bm-git --status`（仅查看状态）

---

## 文档索引位置

| 文档 | 路径 | 维护者 |
|------|------|--------|
| 需求文档 | `docs/requirements.md` | Analyst |
| 方案文档 | `plans/<feature>/plan.md` | Planner |
| Story 详细文档 | `plans/<feature>/stories/story-<NN>-<slug>.md` | Story Detailer |
| 架构决策 | `docs/adr/adr-<编号>-<slug>.md` | Architect |
| 修改记录 | `docs/changelogs/<epic>/<story>/` | Chronicler |
| 测试报告 | `docs/test-reports/<feature>.md` | QA |
| 讨论纪要 | `docs/council-review-<对象>.md` | Council |
| 总索引 | `docs/INDEX.md` / `plans/INDEX.md` | 各角色同步 |

---

## 内部参考

- **`multi-agent-pattern`**：同角色多 Agent 并行的编排模式模板，供各 skill 在执行多实例并行时参考。不直接由用户调用。

---
name: bm-story-batch
description: 批量 Story 展开编排器：串行调用 /bm-story，用户逐个交互确认，自动推进，输出 plans/<feature>/stories/story-<NN>-<slug>.md
---

# /bm-story-batch — 批量 Story 展开编排器

## 参数

- `/bm-story-batch <plan>` — 展开指定 plan 中的所有未展开 Story
- `/bm-story-batch all` — 展开全部已确认 plan 中的所有未展开 Story
- `/bm-story-batch` — 扫描全部已确认 plan，列出待展开 Story 数量供确认

## 触发时机

当用户需要一次性展开大量 Story，但仍希望对每个 Story 保持交互确认时使用。

## 配置前置检查

启动时若 `.claude/bm_config/paths.json` 不存在或内容为空，则**自动调用 `/bm-init`** 进行交互式初始化，等待初始化完成后再继续执行本 skill 的后续步骤。

## 配置文件

启动时读取 `.claude/bm_config/paths.json`，使用以下目录配置：

| 配置键 | 默认值 | 本 skill 用途 |
|--------|--------|--------------|
| `plans_dir` | `plans` | 扫描 Story `{plans_dir}/INDEX.md`，委托 `/bm-story` 写入 |

子路径命名由各 skill 自行约定，不从配置读取。

## 职责

本 skill 是一个**编排器**，不直接生成 Story 文档。核心职责：

1. 扫描目标 plan，确定待展开 Story 清单
2. 与用户确认展开范围（唯一一次范围确认）
3. **串行调用 `/bm-story <plan> <story-id>`** 逐个展开每个 Story
4. 每个 `/bm-story` 正常执行其完整交互流程（骨架确认 → 写文档 → 写后确认）
5. 每个 Story 确认完成后，自动推进到下一个 Story
6. 全部完成后输出汇总

## 禁止事项

- **不直接写 Story 文档**：文档生成完全委托给 `/bm-story`
- **不跳过 Story 的交互确认**：每个 Story 由用户正常确认
- **不并行展开**：Story 之间存在依赖关系，必须按顺序执行
- **不修改 plan 验收标准**
- **不写业务代码**

---

## 工作流

### 前置步骤：扫描

1. 读取 `plans/INDEX.md` 获取全部已确认 plan 列表
2. 扫描 `plans/<feature>/stories/` 下已有的 `story-*.md` 文件，确定待展开范围
3. 列出每个 plan 的 Story 总数、已展开数、待展开数

**前置校验**：
- Plan 不存在 → 终止，提示可用 plan 列表
- Plan 状态为"草稿"→ 终止，提示先确认 plan
- 所有 Story 已展开 → 提示"全部 Story 已展开，无需操作"
- 已有部分 story 文件 → 列出将跳过/将展开的清单

### 步骤 1：范围确认（唯一一次 AskUserQuestion）

向用户展示：

```
待展开 Story 清单:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 1: tavern-llm         → 5 待展开
Phase 2: tavern-character   → 5 待展开
Phase 2: tavern-worldbook   → 4 待展开
Phase 2: tavern-memory      → 5 待展开
Phase 3: tavern-context     → 4 待展开
Phase 4: tavern-frontend    → 6 待展开
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
总计: 29 个 Story 待展开
```

通过 `AskUserQuestion` 确认：
- "确认，按 Phase 顺序逐个展开" (Recommended)
- "只展开 Phase 1（tavern-llm），后续再议"
- "跳到指定 plan（请在 Other 中说明）"
- "取消"

### 步骤 2：串行展开（逐 Story 调用 /bm-story）

用户确认后，按以下顺序逐个调用 `/bm-story`：

```
Phase 1:
  /bm-story tavern-llm 1
  /bm-story tavern-llm 2
  /bm-story tavern-llm 3
  /bm-story tavern-llm 4
  /bm-story tavern-llm 5

Phase 2（按 plan 顺序串行，plan 之间可暂停）:
  /bm-story tavern-character 1
  /bm-story tavern-character 2
  ...
  /bm-story tavern-worldbook 1
  ...
  /bm-story tavern-memory 1
  ...

Phase 3:
  /bm-story tavern-context 1 ~ 4

Phase 4:
  /bm-story tavern-frontend 1 ~ 6
```

**推进规则**：
- 调用 `/bm-story`（正常交互模式，不传 `--batch`）
- 用户正常与 `/bm-story` 交互：确认骨架 → 文档生成 → 确认文档 → **自动清理 plan.md 冗余内容**
- `/bm-story` 完成后，自动展示当前进度并推进到下一个 Story
- 每个 Story 完成后输出进度条：`[5/29] tavern-llm ✓（plan.md 已清理） → 下一个: tavern-character 1`

**Phase 间暂停**：每完成一个 Phase 的全部 Story，向用户展示该 Phase 的完成摘要，并询问是否继续下一 Phase：

> "Phase 1 (tavern-llm) 全部完成：5/5 Story 已展开 → plans/tavern-llm/"
>
> 选项："继续 Phase 2" (Recommended) / "暂停，稍后继续" / "跳过 Phase 2 的某个 plan"

### 步骤 3：输出汇总

全部完成后输出：

```
批量展开完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 1: tavern-llm         → plans/tavern-llm/story-*.md × 5
Phase 2: tavern-character   → plans/tavern-character/story-*.md × 5
Phase 2: tavern-worldbook   → plans/tavern-worldbook/story-*.md × 4
Phase 2: tavern-memory      → plans/tavern-memory/story-*.md × 5
Phase 3: tavern-context     → plans/tavern-context/story-*.md × 4
Phase 4: tavern-frontend    → plans/tavern-frontend/story-*.md × 6
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
总计: 29/29 Story 已展开
```

---

## 核心规则

- **只做编排，不生成文档**：所有 Story 文档由 `/bm-story` 生成
- **串行调用**：Story 之间存在依赖，不并行
- **保持交互**：每个 `/bm-story` 以正常模式运行，用户逐个确认
- **Phase 间可暂停**：每完成一个 Phase 询问是否继续
- **幂等**：跳过已有 `story-*.md` 的 Story
- **不做技术选型**：所有决策引用已有 ADR 和 plan
- **不替代 `/bm-dev`**：story 文档是 dev 的输入
- **plan.md 清理自动完成**：batch 编排器无需额外处理 plan.md 清理。每个 `/bm-story` 调用在其步骤 4（同步更新）中自动执行清理，batch 编排器仅需在汇总阶段做一次快速校验，确认各 Story 的 plan.md 节已被清理
- **所有文件路径引用必须从 `.claude/bm_config/paths.json` 读取，不得硬编码。**

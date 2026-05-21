---
name: bm-pilot
description: 开发流程自动驾驶：评估复杂度、确认路线后自动按阶段调用各 skill，仅在确认点和门禁处暂停
---

# /bm-pilot — 开发流程自动驾驶

## 参数

- `/bm-pilot 我要实现 xxx` — 传入需求，评估复杂度并确认路线后自动推进
- `/bm-pilot` — 无参数，扫描当前进度，自动推进到下一未完成阶段

## 配置前置检查

启动时若 `.claude/bm_config/pilot.json` 或 `.claude/bm_config/paths.json` 不存在或内容为空，则**自动调用 `/bm-init`** 进行交互式初始化，等待初始化完成后再继续执行本 skill 的后续步骤。

## 配置文件

启动时必须读取 `.claude/bm_config/pilot.json` 和 `.claude/bm_config/paths.json`。

### pilot.json — 行为配置

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `dev_skill` | `"bm-dev"` / `"bm-dev-serial"` | 编码阶段使用的 skill，默认 `"bm-dev-serial"` |
| `developer.name` | string | 开发者名称，传递给下游 skill 作为上下文 |
| `developer.email` | string | 开发者邮箱 |
| `skip_stages.story_detail` | bool | 是否跳过 Story 细化阶段，默认 `false`（推荐保持 `false`；仅当所有 Story 均为单文件极简改动时设为 `true`） |
| `skip_stages.story_detail_auto` | bool | Story 细化是否全自动（`true` 时调 `--batch` 无交互），默认 `true` |
| `skip_stages.council_review` | bool | 是否跳过方案评审阶段，默认 `false` |
| `defaults.complexity` | `"auto"` / `"standard"` / `"complex"` | `"auto"` 时自动判定，否则强制使用指定复杂度 |
| `timeout.per_stage_minutes` | int | 单阶段超时提醒（分钟），默认 `30`，设为 `0` 禁用 |

### paths.json — 顶层目录配置

仅定义顶层目录，子路径命名由各 skill 自行约定：

| 配置键 | 默认值 | 用途 |
|--------|--------|------|
| `docs_dir` | `docs` | 需求/索引/ADR/changelog/test-report/council-review 的父目录 |
| `plans_dir` | `plans` | 方案/Story 根目录 |
| `changelogs_dir` | `docs/changelogs` | 变更日志根目录 |
| `adr_dir` | `docs/adr` | ADR 目录 |
| `test_reports_dir` | `docs/test-reports` | 测试报告目录 |
| `council_reviews_dir` | `docs/council-reviews` | 评审纪要目录 |
| `tests_dir` | `tests` | 测试代码根目录 |
| `src_dir` | `src` | 源码根目录 |

所有编码阶段的 skill 调用必须使用 `config.dev_skill` 配置值（如 `Skill(skill=config.dev_skill, args="<feature>")`），不得硬编码。
所有文件扫描（进度推断、门禁检查）必须使用上述目录配置，子路径由各 skill 自行拼接，不得硬编码完整路径。

## 职责

1. **复杂度评估**：根据用户输入，30 秒内判定极简/标准/复杂
2. **路线确认**：呈现评估结果，用户确认后进入自动驾驶循环
3. **自动调度**：按阶段顺序自动 invoke 下游 skill（`Skill` 工具）
4. **记录门禁**：每次阶段切换前强制执行，未通过则自动调 `/bm-chronicle` 修复
5. **暂停管理**：在确认点、Blocker、范围变更时暂停，等待用户决策
6. **进度追踪**：每阶段完成后重新扫描，自动推进到全部完成为止

## 禁止事项

- **不写文档、不改代码、不运行测试**（这些由下游 skill 执行）
- **不做技术决策**（选型、架构判断交给对应 skill）
- **不替代各 skill 的内部确认机制**（预沟通确认、写后确认仍由各 skill 自行处理）
- **不跳过记录门禁**：任何阶段切换前必须执行门禁检查

---

## 自动驾驶流程总览

```
用户输入需求
    │
    ▼
[步骤 1: 复杂度评估 + 路线确认]  ← 唯一一次全程确认（AskUserQuestion）
    │ 用户确认路线后，进入自动循环
    ▼
[步骤 2: 自动循环] ──────────────────────────────────────┐
    │                                                     │
    ├─→ 记录门禁检查（每次阶段切换前强制）                  │
    │   ├─ 未通过 → 自动调用 /bm-chronicle → 回到门禁      │
    │   └─ 通过 → 继续                                     │
    │                                                     │
    ├─→ 自动调用下一阶段 skill（Skill 工具直接 invoke）     │
    │   ├─ 各 skill 内部的 AskUserQuestion 正常触发        │
    │   │   （预沟通确认、写后确认 — 用户正常交互）         │
    │   ├─ bm-qa 发现 Blocker → 暂停，等待用户决策         │
    │   └─ 其他情况 → skill 完成后自动继续                  │
    │                                                     │
    ├─→ 该 stage 所有 Story 完成？                         │
    │   ├─ 否 → 继续同一 skill 下一个 Story                │
    │   └─ 是 → 重新扫描进度 → 进入下一阶段                │
    │                                                     │
    └─→ 所有阶段完成？                                     │
        ├─ 否 → 回到自动循环 ─────────────────────────────┘
        └─ 是 → 最终记录门禁 → 输出汇总 → 结束
```

---

## 步骤 1：复杂度评估与路线确认

### 1.1 匹配已有方案

1. 读取 `docs/INDEX.md` 和 `plans/INDEX.md`
2. 解析用户输入关键词，匹配已有需求/方案条目
3. 若 `plans/<feature>/plan.md` 已存在且状态为"已确认" → 输出进度看板，直接从当前阶段进入自动循环
4. 若无匹配或仅有草稿 → 进入复杂度评估

### 1.2 复杂度评估（30 秒内）

| 检查项 | 极简 | 标准 | 复杂 |
|--------|------|------|------|
| 是否修改现有核心业务逻辑 | 否 | 否 | 是 |
| 是否引入新依赖/新技术 | 否 | 否 | 是 |
| 预估文件数 | ≤2 | 3-5 | 5+ 或跨模块 |

**规则**：任意一项达到"复杂"→ 复杂模式；三项都是"极简"→ 极简模式；其余 → 标准模式。

### 1.3 路线确认（唯一一次全程确认）

通过 `AskUserQuestion` 呈现评估结果和自动驾驶路线，用户确认后进入自动循环：

**极简模式**：
> "复杂度：极简（1-2 文件，无新依赖）。自动驾驶路线：跳过分析/架构/方案 → 直接编码 → 记录 → 测试"
> 选项："确认，开始自动驾驶" / "切换为标准模式" / "补充说明"

**标准模式**：
> "复杂度：标准（常规功能，3-5 文件）。自动驾驶路线：分析 → 方案 → 编码 → 记录 → 测试"
> 选项："确认，开始自动驾驶" / "切换为极简" / "切换为复杂" / "补充说明"

**复杂模式**：
> "复杂度：复杂（跨模块/新技术）。自动驾驶路线：分析 → 架构 → 方案 → 编码 → 记录 → 测试"
> 选项："确认，开始自动驾驶" / "切换为标准" / "补充说明"

---

## 步骤 2：自动循环

用户确认路线后，进入自动驾驶循环。pilot 按阶段顺序自动 invoke 下游 skill。

### 2.1 记录门禁（每次阶段切换前强制）

**每次阶段切换前**，pilot 必须执行记录门禁检查：

1. **检测最近修改的文件**：扫描以下目录，获取项目最近一次修改时间：
   - 代码目录：`server/`、`web/`
   - 文档目录：`docs/requirements.md`、`docs/adr/*.md`、`plans/*/plan.md`、`docs/INDEX.md`、`plans/INDEX.md`
   - 通过 git diff 或文件系统时间戳确认
2. **检测最新 changelog 增量时间**：读取 `docs/changelogs/INDEX.md`，确认最新增量文件的日期
3. **判定**：
   - 若最近文件修改时间 **晚于** 最新 changelog 增量时间 → **阻断**，自动调用 `/bm-chronicle` 记录
   - 若一致或 changelog 更新 → 放行

4. **索引一致性检查**（门禁放行前强制执行）：
   对比以下 INDEX 文件，发现不一致立即修复（不阻断，但必须在放行前完成修复）：
   - `docs/changelogs/INDEX.md` 中各 Epic 的实际 Story/增量条目数 vs `docs/INDEX.md` 修改记录表中的 Story 数/增量文件数
   - `plans/<epic>/plan.md` 中的实际状态 vs `plans/INDEX.md` 中记录的对应条目状态
   - `docs/adr/*.md` 中是否有 `### 更新:` 节但 `docs/INDEX.md` ADR 表中未标注更新日期

5. **自动修复**：
   - 门禁失败 → pilot **自动调用** `/bm-chronicle`（`Skill` 工具 invoke）
   - 若 chronicle 能自动确定归属（从 plan 匹配到文件路径）→ 记录完成后自动继续
   - 若 chronicle 无法确定归属 → **暂停**，让用户指定"本次增量属于哪个 Story？"
   - 记录完成 → 门禁通过 → 继续下一阶段

### 2.2 自动调用下游 skill

门禁通过后，pilot 通过 `Skill` 工具直接 invoke 下游 skill，传入拼接好的参数：

```
Skill(skill="bm-analyze", args="<用户需求描述>")
Skill(skill="bm-arch", args="<从用户需求和扫描上下文拼接的参数>")
Skill(skill="bm-plan", args="<feature-name>")
Skill(skill=config.dev_skill, args="<feature>")
Skill(skill="bm-chronicle", args="记录 <epic>/<story> 的增量")
Skill(skill="bm-qa", args="<feature>")
```

每次调用前，pilot 将当前进度看板和上下文摘要作为 prompt 的前置信息传递给下游 skill。

### 2.3 阶段推进逻辑

按以下顺序检测，自动推进到第一个"未完成的必要阶段"并调用对应 skill：

| 阶段 | 检测信号 | 判断标准 | 自动调用 |
|------|---------|---------|---------|
| **分析** | `docs/requirements.md` + `docs/INDEX.md` | 存在匹配的需求条目且内容完整 → 完成 | `/bm-analyze` |
| **架构** | `docs/adr/*.md` | 复杂模式下，已有 ADR 覆盖当前需求的所有架构层面 → 完成；需扩展已有 ADR → 待架构补充；全新架构领域 → 待架构 | `/bm-arch` |
| **方案** | `plans/<feature>/plan.md` | 已确认 → 完成；草稿 → 需继续；需追加 Story 到已有 plan → 待方案补充；新 Epic → 待方案 | `/bm-plan` |
| **Story 细化** | `plans/<feature>/stories/story-*.md` | 所有 Story 存在对应详细文档且状态为"已确认" → 完成；仅当全部 Story 为极简类型（单文件、无新依赖）时自动跳过 | `story_detail_auto: true` → `/bm-story-batch`（全自动）；`false` → `/bm-story`（逐个确认） |
| **编码** | 代码文件修改 + `docs/changelogs/INDEX.md` | 存在编码产出 → 完成 | `config.dev_skill`（当前为 `<feature>`） |
| **记录** | `docs/changelogs/<epic>/<story>/` 文件 | 存在对应增量文件，且 Story/Epic 状态已更新 → 完成 | `/bm-chronicle` |
| **测试** | `docs/test-reports/` | 存在对应测试报告 → 完成 | `/bm-qa` |

**防御性检测**：若方案涉及跨模块接口但无对应 ADR（即使当前判定为标准模式），先调 `/bm-arch` 补充架构决策，再进入 `/bm-plan`。

**Story 循环**：编码阶段若 plan 有多个 Story，每完成一个 Story 的编码 → 自动调 `/bm-chronicle` 记录 → 自动继续下一个 Story → 全部 Story 完成后进入测试阶段。

---

## 暂停点（必须等待用户交互）

自动驾驶在以下情况**暂停**，等待用户决策后继续：

| 暂停触发 | 场景 | 恢复方式 |
|---------|------|---------|
| **路线确认** | 复杂度评估完成，首次进入前 | 用户选择"确认路线"后自动开始 |
| **预沟通确认** | bm-arch 步骤 4、bm-plan 步骤 4、bm-story 步骤 2 | 各 skill 内部的 AskUserQuestion 正常呈现，用户选择后继续 |
| **写后确认** | bm-arch/bm-plan/bm-story 文档完成后 | 同上，用户确认后 skill 返回，pilot 继续 |
| **Blocker/Critical** | bm-qa 发现阻塞级问题 | 用户选择处理方式后继续（修复/接受风险/终止） |
| **记录门禁阻断** | 阶段切换前发现未记录变更且 chronicle 无法自动确定归属 | 用户指定归属后继续 |
| **范围变更** | 用户中途改变需求范围 | 重新评估复杂度，确认新路线后继续 |

---

## 自动跳过（无需用户交互）

以下情况 pilot **自动推进**，不询问用户。受 `skip_stages` 配置影响：

- 阶段完成后的门禁检查通过 → 直接进入下一阶段
- Story 完成且该 Epic 下还有未完成 Story → 自动继续下一个 Story
- 极简模式下的快速路径 → 跳过分析/架构/方案，直接调 `config.dev_skill`
- dev skill 完成一个 Story 的编码 → 自动调 `/bm-chronicle`，然后继续下一 Story
- 标准模式下架构阶段不存在 → 自动跳过
- `skip_stages.story_detail: true` → 跳过 Story 细化阶段（仅推荐用于纯极简 Story 的 plan；跳过会导致编码阶段缺乏详细数据流/边界条件指导，可能降低实现质量）
- `skip_stages.council_review: true` → 跳过方案评审阶段

---

## 终止条件

- 所有阶段完成 + 最终门禁通过 → 输出汇总，结束
- 用户在任何暂停点选择"终止" → 输出当前进度，结束
- bm-qa 发现 Blocker 且用户选择"终止当前方案" → 结束
- 连续 3 次门禁检查失败 → 暂停，询问用户

---

## 进度看板

每次暂停或阶段推进时，输出当前进度看板：

```
进度看板: <需求标题>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[✓] 需求分析    → docs/requirements.md
[○] 架构设计    → 待调用 /bm-arch（复杂模式必需）
[○] 方案策划    → 
[○] Story 细化  →
[○] 编码实现    → 
[○] 记录归档    → 
[○] 测试审查    → 

当前阶段: 架构设计
自动驾驶中 → 正在调用 /bm-arch ...
```

---

## 特殊场景处理

### 用户中途改变需求范围
- 重新评估复杂度
- 若降级（复杂→标准）：告知用户可跳过架构阶段
- 若升级（标准→复杂）：告知用户需补架构阶段

### 检测到多份相关文档
- 列出所有匹配项，让用户选择继续哪一条线
- 例如："检测到 2 个相关 plan：login/plan.md / auth-refactor/plan.md，请选择"

### 架构变更属于已有 ADR 覆盖范围
- 检测到新需求涉及已有 ADR 覆盖的领域
- 调用 `/bm-arch` 时注明"将在 `docs/adr/<已有-adr>` 尾部追加更新节，而非新建 ADR"
- 在进度看板中标注：`[⚠] 架构设计 → <已有-adr> 需扩展`

### Story 追加到已有 Epic plan
- 检测到新需求涉及的代码路径已归入已有 Epic
- 调用 `/bm-plan` 时注明"将在 `plans/<epic>/plan.md` 中追加 Story 节，而非新建 plan"
- 在进度看板中标注：`[⚠] 方案策划 → <已有-plan> 需追加 Story`

### 产出物存在但明显不完整
- 不自动判定为完成
- 向用户说明检测到的状态，由用户选择"继续当前阶段"或"推进到下一阶段"

---

## 规则

- **启动时读取 `.claude/bm-pilot.json`**：所有配置项覆盖默认行为
- **每次调用都重新扫描推断**，不依赖持久状态文件
- **只确认一次路线**：在步骤 1 完成路线确认，之后自动推进
- **pilot 直接 invoke 下游 skill**：通过 `Skill` 工具自动调用，不等待用户手动输入
- **阶段切换只做一次确认**：不叠加到各 skill 的内部确认之上
- **极简需求不强行套流程**：直接调用 `config.dev_skill`
- **编码阶段使用 `config.dev_skill`**：调用时传入 feature 名，不得硬编码 skill 名
- **记录门禁是强制性的**：每次阶段切换前必须执行记录门禁检查，门禁失败时自动调 `/bm-chronicle` 修复
- **暂停点最小化**：仅在路线确认、各 skill 内部确认、Blocker、门禁无法自动修复时暂停
- **传递开发者信息**：调用下游 skill 时，将 `config.developer` 信息作为上下文传递

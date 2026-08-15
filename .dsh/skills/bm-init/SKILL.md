---
name: bm-init
description: 项目初始化向导：交互式引导配置 bm_config，搭建目录骨架，初始化索引文件
---

# /bm-init — 项目初始化向导

## 参数

- `/bm-init` — 交互式初始化：检测当前状态，逐项引导配置
- `/bm-init --check` — 仅检查，输出缺失项清单，不做任何修改
- `/bm-init --force` — 覆盖已有配置为默认值（谨慎，会先备份）

## 职责

1. **状态检测**：扫描 `.dsh/bm_config/`、目录结构、索引文件，判断哪些已存在、哪些缺失
2. **交互式配置引导**：逐项呈现配置项，提供推荐选项和说明，让用户选择
3. **生成配置文件**：写入 `pilot.json` 和 `paths.json`
4. **搭建目录骨架**：按 `paths.json` 创建所有目录
5. **初始化索引文件**：写入标准表头的空索引

## 禁止事项

- **不创建源码文件**：只建目录和索引，不碰 `src/`
- **不修改已有配置**（除非 `--force`）：已有文件默认跳过
- **不初始化 git**：git 操作交给 `/bm-git`
- **不安装依赖**：环境搭建不属于本 skill 范围

---

## 步骤 1：状态检测（静默扫描）

检测以下项目，记录状态（✓ 已存在 / ○ 缺失 / ⚠ 存在但不完整）：

### 1.1 配置文件

| 检测项 | 路径 |
|--------|------|
| 行为配置 | `.dsh/bm_config/pilot.json` |
| 路径配置 | `.dsh/bm_config/paths.json` |

### 1.2 目录结构

从 `paths.json`（或默认值）检测以下目录：

| 目录 | 默认路径 |
|------|---------|
| 文档根目录 | `docs/` |
| ADR 目录 | `docs/adr/` |
| 变更日志目录 | `docs/changelogs/` |
| 测试报告目录 | `docs/test-reports/` |
| 评审纪要目录 | `docs/council-reviews/` |
| 方案目录 | `plans/` |
| 测试代码目录 | `tests/` |

### 1.3 索引文件

| 索引文件 | 默认路径 |
|---------|---------|
| 文档索引 | `docs/INDEX.md` |
| 方案索引 | `plans/INDEX.md` |
| 变更日志索引 | `docs/changelogs/INDEX.md` |

---

## 步骤 2：呈现状态并确认范围

向用户展示检测结果：

```
项目初始化状态
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
配置文件:
  [○] pilot.json        — 缺失，将创建
  [○] paths.json        — 缺失，将创建

目录:
  [✓] docs/             — 已存在
  [○] docs/adr/         — 将创建
  [○] docs/changelogs/  — 将创建
  ...

索引:
  [○] docs/INDEX.md     — 将创建
  ...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
将创建: 2 配置文件 + 5 目录 + 3 索引文件
```

通过 `ask_user_question` 确认：
- "开始交互式配置" (Recommended)
- "全部使用默认值，跳过引导"
- "仅创建目录和索引，跳过配置"
- "取消"

---

## 步骤 3：交互式配置引导

逐项引导用户配置。每项提供推荐值和简短说明，用户可选择推荐值或自定义。

### 3.1 开发者信息

**ask_user_question 1 — 开发者名称**：
- 问题："请输入开发者名称（会传递给下游 skill 作为上下文）"
- 选项：从 git config 自动检测（如 "BuMing"）(Recommended) / "自定义输入"

**ask_user_question 2 — 开发者邮箱**：
- 问题："请输入开发者邮箱"
- 选项：从 git config 自动检测 (Recommended) / "自定义输入" / "跳过"

### 3.2 编码 Skill 选择

**ask_user_question 3 — dev_skill**：
- 问题："选择默认的编码 skill："
  - "bm-dev-serial — 串行执行，避免并发限流（适合 DeepSeek 等限流模型）" (Recommended)
  - "bm-dev — 并行执行，速度更快（适合 Claude 等不限流模型）"

**说明**：此项决定 `/bm-pilot` 自动驾驶时调用哪个编码 skill。可随时在 `pilot.json` 中修改。

### 3.3 复杂度判定

**ask_user_question 4 — defaults.complexity**：
- 问题："复杂度判定策略？"
  - "auto — 自动判定（推荐），根据文件数/跨模块/新技术自动决定" (Recommended)
  - "standard — 始终使用标准流程（分析→方案→编码→记录→测试）"
  - "complex — 始终使用完整流程（分析→架构→方案→编码→记录→测试）"

### 3.4 阶段跳过

**ask_user_question 5 — skip_stages.story_detail**：
- 问题："是否跳过 Story 细化阶段？（将 plan 中的 Story 展开为独立详细文档）"
  - "不跳过 — 复杂 Story 展开为独立文档，编码前有详细指南" (Recommended)
  - "跳过 — Story 保持在 plan 中，加快流程"

**ask_user_question 6 — skip_stages.council_review**：
- 问题："是否跳过方案评审阶段？（多角色圆桌讨论）"
  - "跳过 — 不自动评审，有需要时手动调用 /bm-council" (Recommended)
  - "不跳过 — 方案完成后自动多角色评审"

### 3.5 超时设置

**ask_user_question 7 — timeout.per_stage_minutes**：
- 问题："单阶段超时提醒（分钟）？"
  - "30 分钟（推荐）" (Recommended)
  - "60 分钟"
  - "不限制（设为 0）"

### 3.6 路径自定义（可选）

**ask_user_question 8 — 路径自定义**：
- 问题："是否需要自定义输出路径？"
  - "使用默认路径（推荐）" (Recommended)
  - "自定义 — 在 Other 中输入 JSON 键值对"

若用户选择默认 → 跳过，使用内置默认值。
若选择自定义 → 允许用户输入要覆盖的路径键值对（如 `{"docs_dir": "my-docs", "tests_dir": "my-tests"}`），其余保持默认。

---

## 步骤 4：生成配置并搭建骨架

### 4.1 写入 pilot.json

根据用户选择生成 `.dsh/bm_config/pilot.json`。若目录不存在则先创建。

### 4.2 写入 paths.json

根据用户选择（或默认值）生成 `.dsh/bm_config/paths.json`。

### 4.3 创建目录

根据 `paths.json` 创建所有目录（已存在则跳过）：

```
docs/adr/
docs/changelogs/
docs/test-reports/
docs/council-reviews/
plans/
tests/
```

### 4.4 初始化索引文件

若索引文件不存在，写入标准表头：

**`docs/INDEX.md`**：
```markdown
# 文档索引

## 需求清单

| 需求 | 状态 | 日期 | 摘要 |
|------|------|------|------|

## 架构决策记录 (ADR)

| ADR | 标题 | 状态 | 日期 |
|-----|------|------|------|

## 修改记录

（详见 `docs/changelogs/INDEX.md`）

## 测试报告

| 功能 | 日期 | 结果 |
|------|------|------|
```

**`plans/INDEX.md`**：
```markdown
# 方案索引

| 方案 | 需求 | 状态 | 日期 | 摘要 |
|------|------|------|------|------|
```

**`docs/changelogs/INDEX.md`**：
```markdown
# 变更日志索引

（各 Epic 的增量记录由 `/bm-chronicle` 自动追加）
```

---

## 步骤 5：输出汇总

```
初始化完成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
配置文件:
  [✓] .dsh/bm_config/pilot.json
  [✓] .dsh/bm_config/paths.json

目录:
  [✓] docs/adr/
  [✓] docs/changelogs/
  [✓] docs/test-reports/
  [✓] docs/council-reviews/
  [✓] plans/
  [✓] tests/

索引:
  [✓] docs/INDEX.md
  [✓] plans/INDEX.md
  [✓] docs/changelogs/INDEX.md

配置摘要:
  dev_skill:      bm-dev-serial
  developer:      BuMing
  complexity:     auto
  skip_story:     false
  skip_council:   true
  timeout:        30min
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
下一步: 调用 /bm-orchestrator 开始开发，或 /bm-pilot 自动驾驶
```

---

## --check 模式（仅检查）

用户调用 `/bm-init --check` 时，只执行步骤 1 的状态检测，输出缺失项清单，不做任何修改。

## --force 模式

用户调用 `/bm-init --force` 时：
1. 若 `pilot.json` 或 `paths.json` 已存在 → 备份为 `.bak` 后缀后覆盖
2. 索引文件已存在 → 不覆盖（索引文件有内容积累，覆盖会丢数据）
3. 目录 → 正常创建
4. 其余流程同交互模式

## 内置默认值

当用户选择"全部使用默认值"时，使用以下配置：

| 配置项 | 默认值 |
|--------|--------|
| `developer.name` | git config `user.name`（或空） |
| `developer.email` | git config `user.email`（或空） |
| `dev_skill` | `bm-dev-serial` |
| `defaults.complexity` | `auto` |
| `skip_stages.story_detail` | `false` |
| `skip_stages.council_review` | `true` |
| `timeout.per_stage_minutes` | `30` |

## 规则

- **已有文件默认不覆盖**（除非 `--force`）：保护用户已做的配置和索引积累
- **目录创建幂等**：已存在的目录静默跳过
- **索引文件仅初始化空壳**：不写入任何项目具体内容
- **不做 git 操作**：不 `git init`、不 `git add`、不 commit
- **所有路径从配置读取**：创建目录和索引时使用 `paths.json` 中的配置值

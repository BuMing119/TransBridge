---
name: bm-chronicle
description: LLM修改记录员：按 Epic→Story 分层，每次增量输出独立文件到 docs/changelogs/<epic>/<story>/YYYY-MM-DD-NNN-简述.md，append-only
---

# 角色：LLM 修改记录员 (Chronicler)

## 触发时机

- 每完成一个 Story 的全部编码后，**必须**调用 `/bm-chronicle`
- 一个 Story 编码量很大时，可以**阶段性**调用（例如每完成 2-3 个实现步骤）
- **文档变更后也必须记录**：
  - bm-analyze 结束后 → 记录 `docs/requirements.md` 的需求条目新增/修改
  - bm-arch 结束后 → 记录 `docs/adr/*.md` 的新建或更新
  - bm-plan 结束后 → 记录 `plans/*/plan.md` 的方案/Story 变更
- 用户主动要求记录时

## 配置前置检查

启动时若 `.dsh/bm_config/paths.json` 不存在或内容为空，则**自动调用 `/bm-init`** 进行交互式初始化，等待初始化完成后再继续执行本 skill 的后续步骤。

## 配置文件

启动时读取 `.dsh/bm_config/paths.json`，使用以下目录配置：

| 配置键 | 默认值 | 本 skill 用途 |
|--------|--------|--------------|
| `changelogs_dir` | `docs/changelogs` | 增量文件 `{changelogs_dir}/{epic}/{story}/{date}-{seq}-{summary}.md`，索引 `{changelogs_dir}/INDEX.md` |
| `plans_dir` | `plans` | 同步更新 `{plans_dir}/INDEX.md` |
| `docs_dir` | `docs` | 同步更新 `{docs_dir}/INDEX.md` |

子路径命名由各 skill 自行约定，不从配置读取。

## 职责

1. 收集本次修改涉及的文件和变更摘要（每个文件改了什么、为什么改）
2. 确定增量所属的 **Epic → Story**
3. 创建独立增量文件 `docs/changelogs/<epic-slug>/<story-slug>/YYYY-MM-DD-NNN-简述.md`
4. 同步更新 `docs/changelogs/INDEX.md`
5. 同步更新 `plans/INDEX.md` 和 `docs/INDEX.md` 中的状态

## 工作流

### 第一步：读取上下文

1. 读取 `docs/changelogs/INDEX.md`（确定已有增量编号，避免 NNN 冲突）
2. 读取对应 `plans/<feature>/plan.md`，提取：
   - Epic 名称与 slug（从 plan 文件名 `<feature>` 或元数据推断）
   - Story 清单与各 Story 的 slug（从 story 文件名 `story-NN-slug` 提取）
3. 读取 `docs/INDEX.md` 和 `plans/INDEX.md`

### 第二步：识别增量归属

通过以下方式确定本次增量属于哪个 Story（按优先级）：

**A. 代码文件变更**：
1. **用户直接指定**：用户说"记录到用户注册 Story"
2. **从 plan 推断**：读取已有 plan 中 Story 的实现步骤，匹配本次修改的文件路径
3. **从 git 历史推断**：查看最近 commit message 或询问用户

**B. 文档文件变更**（docs/ 或 plans/ 下的文件）：
1. **按文件路径前缀匹配已有 Epic**：
   - `docs/adr/*.md` → 检查 ADR 引用方（被哪些 plan 引用），归入引用方的 Epic changelog
   - `plans/<epic>/plan.md` → 直接归入对应 Epic
   - `docs/requirements.md` → 检查需求条目关联的 Epic，归入对应 Epic；若关联多个 Epic，在各相关 Epic 下分别记录
   - `docs/INDEX.md` 或 `plans/INDEX.md` → 跟随主要变更文件一起记录
2. **跨模块文档**（如 ADR 被多个 Epic 引用）：在各相关 Epic 下分别建增量文件，或按主要引用方归属
3. 匹配规则同 bm-plan：文件路径前缀 + plan 涉及文件反向索引

若无法确定 → **询问用户**："本次增量属于哪个 Story？"

### 第三步：确定文件名与编号

1. **Epic slug**：从 plan 文件名派生（如 `tavern-llm`）
2. **Story slug**：从 story 文件名派生（如 `story-01-base-adapter-interface`）
3. **NNN 编号**：检查 `docs/changelogs/<epic-slug>/<story-slug>/` 目录下现有文件的最大编号，新文件编号为 `max+1`（如已存在 `001` 和 `002`，则新文件为 `003`）。若目录不存在则从 `001` 开始
4. **文件名**：`YYYY-MM-DD-NNN-简述.md`（简述从变更摘要自动派生，不超过 30 字）

### 第四步：创建增量文件

在 `docs/changelogs/<epic-slug>/<story-slug>/` 下创建增量文件，确保目录存在（不存在则创建）。

#### 增量文件格式

```markdown
# <序号>: <简短描述>

**日期**: YYYY-MM-DD
**类型**: 增/改/删/移
**关联**: Epic: <Epic名称> > Story <编号>: <Story名称>

## 修改文件

### `path/to/file1.py` (增)
- **修改内容**: 具体描述新建了什么、代码的关键位置和逻辑
- **原因**: 为什么要新建这个文件，解决什么问题

### `path/to/file2.py` (改)
- **修改内容**: 具体描述修改了哪些函数/方法/配置，原来的行为是什么，现在变成什么
- **原因**: 为什么要做这个修改

### `path/to/file3.py` (删)
- **修改内容**: 原文件内容简述
- **原因**: 为什么删除（迁移到新位置/不再需要/被替代）
```

**格式要求**：
- 每个被修改的文件一个 `###` 段落，标注操作类型（增/改/删/移），**代码文件**和**文档文件**（docs/adr/*.md、plans/*/plan.md、docs/requirements.md）均需记录
- 「修改内容」必须精确到函数/类/配置项级别（代码）或段落/Story/决策级别（文档），而非"修改了一些代码"
- 「原因」必须解释业务或架构动机，而非"按方案要求"
- 文件路径使用从项目根目录开始的相对路径

### 第五步：同步更新索引（全部三项均为强制性）

1. **更新 `docs/changelogs/INDEX.md`**：
   - 若对应 Epic/Story 行已存在 → 在表格中追加新的增量文件行
   - 若不存在 → 创建 Epic/Story 表格段落，追加行
   - 格式：
     ```
     | Story N: <名称> | [NNN-简述](<epic>/<story>/YYYY-MM-DD-NNN-简述.md) | YYYY-MM-DD |
     ```

2. **扫描并修复 `plans/INDEX.md`**（强制执行，不得跳过）：
   - 对比 changelog 中该 Epic 的实际 Story 数 vs `plans/INDEX.md` 中记录的 Story 数，不一致则更新
   - 检查 `plans/<epic>/plan.md` 的实际状态 vs `plans/INDEX.md` 中记录的状态，不一致则更新
   - 检查主方案（如 `plans/tavern.md`）的 Story 总数是否正确（对比各子方案 Story 之和），过期则更新
   - 若本次变更涉及 plan 文件本身的修改（如状态更新、Story 追加），同步更新 `plans/INDEX.md` 中的摘要和日期

3. **扫描并修复 `docs/INDEX.md`**（强制执行，不得跳过）：
   - 对比 changelog 中该 Epic 的实际增量文件数 vs `docs/INDEX.md` 中记录的增量文件数，不一致则更新
   - 检查 `docs/adr/*.md` 文件是否被修改过——对比 ADR 文件内容中的"更新"节日期 vs `docs/INDEX.md` ADR 表中的日期，若 ADR 有更新节但表中未标注"（更新: YYYY-MM-DD）"，添加标注
   - 若本次为测试报告产出，在测试报告表中追加新行
   - 若 Epic 状态变更，更新需求状态

**关键规则**：第 2、3 项不是"若有变更则更新"，而是"每次记录后主动扫描三个 INDEX 文件之间的一致性，发现任何不一致立即修复"。这包括但不限于：Story 计数、增量文件计数、状态字符串、ADR 更新日期。

## 完整示例

假设本次增量属于 `tavern-llm` Epic 的 `story-02-openai-adapter` Story，已有 1 条记录：

**创建文件** `docs/changelogs/tavern-llm/story-02-openai-adapter/2026-05-01-002-添加温度参数支持.md`：

```markdown
# 002: 添加 temperature 参数透传支持

**日期**: 2026-05-01
**类型**: 改
**关联**: Epic: LLM 统一接入层 > Story 2: OpenAI 适配器

## 修改文件

### `server/app/llm/openai_adapter.py` (改)
- **修改内容**: `chat()` 和 `chat_stream()` 方法新增 `temperature` 参数（默认 None），当传入时添加到请求体的 `temperature` 字段。原实现硬编码 `temperature=0.7`，现改为由调用方控制，None 时使用 API 默认值
- **原因**: 前端 Settings 页面需要允许用户调节生成温度以控制回复的随机性/创造性

### `server/app/llm/claude_adapter.py` (改)
- **修改内容**: 同上，`chat()` 和 `chat_stream()` 新增 `temperature` 参数透传到 Anthropic Messages API 的 `temperature` 字段
- **原因**: 与 OpenAI 适配器保持一致的用户体验，temperature 是跨厂商通用参数

### `server/tests/llm/test_openai_adapter.py` (改)
- **修改内容**: `TestChat` 类新增 `test_chat_with_temperature` 测试，验证传入 `temperature=0.3` 时请求体包含 `"temperature": 0.3`
- **原因**: 确保 temperature 参数正确序列化到 API 请求中
```

**更新** `docs/changelogs/INDEX.md`，在 `tavern-llm` 表格中追加：
```
| Story 2: OpenAI 适配器 | [002-添加温度参数支持](tavern-llm/story-02-openai-adapter/2026-05-01-002-添加温度参数支持.md) | 2026-05-01 |
```

## 核心规则

- **Append-only**：只创建新文件，禁止修改、删除已有的增量文件
- **Epic→Story 目录分层**：增量文件必须放在对应的 `docs/changelogs/<epic>/<story>/` 目录下，不得平铺在 changelogs/ 根层
- **Story 必须在 plan 中预定义**：plan 阶段未定义的 Story，编码阶段不得新增（如需新增，先更新 plan）
- **覆盖文档变更**：不仅要记录代码文件（.py/.ts/.tsx），也要记录文档文件变更——`docs/requirements.md`（需求）、`docs/adr/*.md`（架构决策）、`plans/*/plan.md`（方案）。文档变更按文件路径归入已有 Epic 的 changelog 目录
- **增量归属优先匹配已有 Epic**：代码和文档文件变更都优先按文件路径归入已有 Epic；匹配失败时才可创建新 Epic 目录。规则同 bm-plan 步骤 3.5
- **日期统一使用 ISO 格式**：`YYYY-MM-DD`
- **文件路径使用相对路径**，从项目根目录开始
- **简述不超过 30 字**：从变更摘要自动派生，中文优先
- **编号 NNN 三位零填充**：`001`, `002`, ..., `999`
- **所有文件路径引用必须从 `.dsh/bm_config/paths.json` 读取，不得硬编码。**

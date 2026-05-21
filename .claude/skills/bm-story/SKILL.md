---
name: bm-story
description: Story 细节展开：将 plan 中的单个 Story 拆分为详细实现指南，输出 plans/<feature>/stories/story-<NN>-<slug>.md，含数据流、边界条件、伪代码、测试策略
---

# /bm-story — Story 细节展开

## 参数

- `/bm-story <plan> <story-id>` — 展开指定 plan 中的指定 Story
- `/bm-story <plan> <story-id> --batch` — 非交互模式：跳过骨架确认和写后确认，直接生成已确认文档（供 `/bm-story-batch` 调用）
- `/bm-story <plan>` — 列出该 plan 中所有 Story，由用户选择展开哪个
- `/bm-story` — 扫描全部已确认 plan，列出所有可用 Story 供选择

`<plan>` 为 `plans/` 下的文件名（不含 `.md`），`<story-id>` 为 Story 编号，如 `1`、`1.2`、`2.1`。

## 触发时机

Plan 已确认后、编码前。当某个 Story 复杂度较高（跨多个文件、多步骤、复杂数据流），需要在编码前明确实现细节时调用。

**Story 展开是推荐默认流程**：每个 Story 在编码前应展开为详细实现指南，以获得数据流、边界条件、伪代码和测试策略指导。仅极简 Story（单文件、无新依赖）可直接由 `/bm-dev` 按验收标准编码，跳过展开。

## 配置前置检查

启动时若 `.claude/bm_config/paths.json` 不存在或内容为空，则**自动调用 `/bm-init`** 进行交互式初始化，等待初始化完成后再继续执行本 skill 的后续步骤。

## 配置文件

启动时读取 `.claude/bm_config/paths.json`，使用以下目录配置：

| 配置键 | 默认值 | 本 skill 用途 |
|--------|--------|--------------|
| `plans_dir` | `plans` | 方案 `{plans_dir}/{feature}/plan.md`，Story `{plans_dir}/{feature}/stories/story-{NN}-{slug}.md`，索引 `{plans_dir}/INDEX.md` |
| `adr_dir` | `docs/adr` | 读取架构决策 |

子路径命名由各 skill 自行约定，不从配置读取。

## 职责

1. 从 plan 中提取单个 Story 的全部上下文
2. 展开为详细实现指南：数据流、函数签名、边界条件、伪代码
3. 规划每步的测试策略
4. 输出 `plans/<feature>/stories/story-<NN>-<slug>.md`

## 禁止事项

- **不写代码**：只输出设计文档，不修改任何业务代码
- **不重新做技术选型**：所有技术决策引用 ADR 和 plan
- **不修改 plan 的验收标准与 Story 边界**：验收标准和 Story 边界由 plan 定义，story 阶段只展开不修改。但会在 plan.md 中追加 `**详细文档**:` 链接，并清理旧版冗余的 `**实现步骤**:` 节（向后兼容清理）
- **不修改 story 的验收标准**：验收标准以 plan 为准
- **不替代 `/bm-dev`**：story 文档是 dev 的输入，dev 仍需按 step 编码

---

## 工作流

### 模式判断

若调用参数包含 `--batch`，进入**批量模式**：
- 跳过步骤 2（骨架预沟通）
- 跳过步骤 5（写后确认）
- 步骤 3 中 Story 文档状态直接设为"已确认"
- 步骤 4 同步更新照常执行
- 完成后输出简短摘要（≤3 行），无需用户交互

若调用参数不含 `--batch`，进入**交互模式**，执行全部 5 个步骤。

### 前置步骤

1. 读取 `plans/<plan>/plan.md`
2. 读取 `docs/adr/*.md`（获取 ADR 上下文）
3. 读取 `plans/INDEX.md`（确认 plan 状态）

**前置校验**：
- Plan 不存在 → 终止，提示"该 plan 不存在，请先调用 `/bm-plan`"
- Plan 状态为"草稿"→ 终止，提示"plan 尚未确认，请先确认 plan"
- 未找到指定 Story → 列出该 plan 中所有 Story，让用户选择

### 步骤 1：收集 Story 上下文

从 plan 中提取目标 Story 的：
- Story 名称与验收标准
- 实现步骤与涉及文件
- 所在 plan 的功能边界（范围内/外）
- 所在 plan 的架构依赖（引用哪些 ADR）

同时检查：
- 该 Story 依赖的其他 Story（同 plan 内前面的 Story）
- 该 Story 依赖的其他 plan 的接口（跨 plan 依赖）

### 步骤 2：预沟通（写文档前）

**此阶段严禁输出正式文档，只呈现展开骨架供用户确认。**

向用户展示：

1. **Story 信息回顾**：名称、验收标准、涉及文件
2. **展开计划**：
   - 数据流走向（文字描述或 ASCII 图）
   - 关键函数/类签名草案
   - 边界条件列表
   - 每步测试思路
3. **建议的文件名**：`story-<NN>-<slug>.md`（slug 从 Story 名称自动派生）

通过 `AskUserQuestion` 提供选项：
- "骨架合理，编写详细文档" (Recommended)
- "调整展开维度（如：不需要测试策略 / 需要更详细的伪代码）"
- "调整边界条件覆盖"
- "Other — 补充意见"

### 步骤 3：编写 Story 详细文档

用户确认骨架后，写入 `plans/<feature>/stories/story-<NN>-<slug>.md`。

#### 文档格式

```markdown
# Story <编号>: <名称>

**所属方案**: `plans/<plan>/plan.md`
**技术模块**: <backend / frontend / db / ...>
**状态**: 草稿
**创建日期**: <日期>

## 前置依赖

### 上游 Story
- Story X.X（同 plan）：已完成 → 提供 <接口/数据模型>

### 跨 Plan 依赖
- `<plan>/plan.md` → `<接口/函数>`（ADR 已冻结）

### 引用的架构决策
- ADR-XXX: <决策摘要>

## 验收标准

（从 plan 原样复制）

- [ ] <标准 1>
- [ ] <标准 2>

## 数据流

（描述本 Story 涉及的数据如何在各层之间流动）

```
用户输入 → Router（参数校验）→ Service（业务逻辑）→ DB/LLM → 响应
```

## 关键接口

### 函数签名

```python
# 每个新增或修改的函数/方法
async def function_name(param1: Type1, param2: Type2) -> ReturnType:
    """一句话描述"""
```

### 数据结构

```python
# Story 涉及的 Pydantic model / dataclass / TypedDict
class SomeModel(BaseModel):
    field: type  # 说明
```

## 实现步骤

### 步骤 1: <步骤标题>

**涉及文件**: `path/to/file.py`（修改/新建）

**实现要点**:
- 做什么
- 不做什么（边界）

**边界条件**:
- 输入为空时 → ...
- 输入异常时 → ...
- 依赖不可用时 → ...

**伪代码/设计思路**:
```python
# 关键逻辑示意，非完整代码
def key_logic():
    ...
```

**测试策略**:
- 单测：测试 <场景>，预期 <结果>
- 集成测试（如需要）：...

### 步骤 2: <步骤标题>
（同上结构）

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `server/app/xxx.py` | 修改 | 新增 xxx 函数 |
| `server/app/yyy.py` | 新建 | Yyy 类 |
| `server/tests/test_xxx.py` | 新建 | 单元测试 |

## 风险与注意事项

- **风险 1**: ... → 缓解：...
- **注意 1**: ...（容易踩坑的点）
```

### 步骤 4：同步更新

1. **清理并更新 plan.md**：在 `plans/<plan>/plan.md` 中找到对应 Story 节（通过 Story 名称或编号匹配），执行以下操作：
   a. **移除旧版冗余内容**：若该 Story 节内存在 `**实现步骤**:` 标题行，将该标题行及其下所有步骤行（以数字序号 `1.`, `2.` 等开头的行）整体删除。保留验收标准等其他内容不变。
   b. **更新详细文档链接**：若已有 `**详细文档**:` 行，将其替换为新路径；若为占位符（`> 详细实现指南见 ...`）或不存在，则替换/追加为：
      ```
      **详细文档**: `plans/<feature>/stories/story-<NN>-<slug>.md`
      ```
   c. **清理后验证**：该 Story 节应仅包含 Story 标题、`**验收标准**:` 清单、`**详细文档**:` 链接。
2. 更新 `plans/INDEX.md`，在对应 plan 行下方追加子行（若已存在则更新）：
   ```
   | [story-<NN>-<slug>.md](<feature>/story-<NN>-<slug>.md) | Story <编号>: <名称> | 草稿 | <日期> | <一句话摘要> |
   ```

### 步骤 5：写后确认

详细文档编写完成后，通过 `AskUserQuestion` 向用户呈现摘要并确认：

呈现内容：
- Story 名称与编号
- 数据流概要
- 关键接口清单
- 边界条件覆盖列表
- 文件变更清单

提供选项：
- "确认，可进入开发" (Recommended)
- "调整边界条件覆盖"
- "调整接口设计"
- "Other — 补充意见"

用户确认后 → 更新 story 文档状态为"已确认"。

---

## 核心规则

- **不做技术选型**：所有技术决策引用已有 ADR 和 plan
- **不改 plan 验收标准**：只能展开实现细节，不能修改或新增验收标准
- **不改业务代码**：只写设计文档，编码留给 `/bm-dev`
- **Story 边界不可越界**：不涉及 plan 中定义在范围外的内容
- **跨 Story 依赖必须显式标注**：若本 Story 依赖同 plan 内其他 Story 的输出，必须在"前置依赖"中声明
- **展开粒度适度**：伪代码而非完整代码，设计思路而非逐行注释
- **状态流转**：`草稿 → 已确认`（用户确认后更新）
- **plan.md 清理是步骤 4 的强制性部分**：每次展开 Story 后必须清理 plan.md 中的 `**实现步骤**:` 冗余内容，确保 plan.md 保持索引化。清理操作视为文档同步，不属于"修改 plan 验收标准"的禁止范围
- **任何文件修改都必须记录**：Story 文档确认后，**必须**调用 `/bm-chronicle` 记录本次增量（含文档变更），未记录不得进入编码阶段
- **所有文件路径引用必须从 `.claude/bm_config/paths.json` 读取，不得硬编码。**

## 与 `/bm-dev` 的关系

`/bm-dev` 在执行 Story 时：
1. 检测 `plans/<feature>/stories/story-<NN>-<slug>.md` 是否存在
2. 若存在且状态为"已确认"→ 优先参考 story 文档中的详细指导，按步骤实现
3. 若不存在 → 直接按 plan 中的实现步骤编码

Story 文档是 plan 和 dev 之间的**可选加速层**：有则更精准，无则不阻塞。

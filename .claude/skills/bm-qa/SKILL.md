---
name: bm-qa
description: 测试审查员：编写测试、运行验证、最终代码审查
---

# 角色：测试审查员 (QA/Reviewer)

## 触发时机

编码完成后调用 `/bm-qa`，或用户需要验证某功能时。

## 配置前置检查

启动时若 `.claude/bm_config/paths.json` 不存在或内容为空，则**自动调用 `/bm-init`** 进行交互式初始化，等待初始化完成后再继续执行本 skill 的后续步骤。

## 配置文件

启动时读取 `.claude/bm_config/paths.json`，使用以下目录配置：

| 配置键 | 默认值 | 本 skill 用途 |
|--------|--------|--------------|
| `plans_dir` | `plans` | 读取方案 `{plans_dir}/{feature}/plan.md` |
| `changelogs_dir` | `docs/changelogs` | 了解变更上下文 `{changelogs_dir}/INDEX.md` |
| `docs_dir` | `docs` | 文档索引 `{docs_dir}/INDEX.md` |
| `test_reports_dir` | `docs/test-reports` | 测试报告 `{test_reports_dir}/{epic-slug}-{level}-{date}.md` |
| `tests_dir` | `tests` | 测试代码 `{tests_dir}/{module}/test_{file}.py` |

子路径命名由各 skill 自行约定，不从配置读取。

## 职责

1. 编写并运行测试
2. 验证功能是否符合 `plans/<feature>/plan.md`
3. 代码审查（安全、性能、风格）
4. 输出测试报告和审查结论

## 测试组织规范

### 文件大小限制

- **单文件上限 400 行**（软限制）。超过 400 行的测试文件应拆分为多个聚焦文件。
- 拆分策略：按测试类/测试区域拆分为独立文件。例如一个文件中包含 Test A-E 五个测试类，应拆为 2-3 个文件（如 `test_param_validation.py`、`test_config_equivalence.py`、`test_report_system.py`）。
- 存量超限文件应在下次接触时拆分，不要求一次性全部重构。

### 目录结构

- 测试文件必须放在对应模块子目录下：`tests/{module}/test_{file}.py`
- `tests/` 根目录不得有散落的测试文件（`conftest.py` 和 `__init__.py` 除外）
- 每个测试包（模块子目录）必须有 `conftest.py` 存放共享 fixtures
- 根 `tests/conftest.py` 存放跨模块共享的 fixtures（`make_entry`、`make_test_collection`、`MockAppContext`、`MockSignal`、`make_llm_config`、`MockToolSpec` 等）

### 框架标准

- **新测试统一使用 pytest**（函数式或 pytest 测试类）
- 存量 `unittest.TestCase` 测试接触即迁移到 pytest
- 使用 `from unittest.mock import MagicMock, patch` 进行 mock，不要 `import unittest`
- **禁止 `sys.path.insert` 硬编码**：`pyproject.toml` 已配置 `pythonpath = ["src"]`，直接使用 `from src.transbridge.xxx import Yyy`

### 共享 Fixtures

- 项目 `tests/conftest.py` 已提供以下共享资源：
  - `make_entry(eid, original, translation, stage, context)` — TranslationEntry 工厂
  - `make_test_collection(n)` — 创建含多变 stage/context 的 TranslationEntryCollection
  - `MockAppContext(collection)` — 完整 ViewModel mock（filter_state/labels/scope/selection/slots/signals）
  - `MockSignal` — Qt signal mock
  - `MockToolSpec` — 工具 spec mock（用于护栏测试）
  - `make_llm_config(**overrides)` — LLMConfig mock（含全部 pp_* 字段）
- 新测试文件优先使用共享 fixtures，避免重复定义 `make_entry`/`MockAppContext` 等
- 如需模块特定 fixtures，在模块的 `conftest.py` 中定义，通过 `from tests.conftest import ...` 复用

## 工作流

### 前置步骤（通用）

1. 读取 `plans/<feature>/plan.md` 和 `docs/changelogs/INDEX.md`
2. 读取 `docs/INDEX.md`

### 步骤 3：规模判定（30 秒内）

分析审查范围与维度：

| 判定结果 | 条件 | 下一步 |
|---------|------|--------|
| **单实例模式** | 常规功能审查，标准维度即可覆盖 | 步骤 4a |
| **多实例并行模式** | 需要多维度专项深入审查（安全/性能/功能/质量） | 步骤 4b |

### 步骤 4a：单实例模式（1 个 qa）

5. 编写/运行测试（单元测试、集成测试）
6. 审查代码实现与方案的一致性
7. 写入 `docs/test-reports/{epic-slug}-qa-{date}.md`
8. 记录待更新索引项，由 `/bm-chronicle` 或 `/bm-dev` 统一写入 `docs/INDEX.md` 和 `plans/INDEX.md`

### 步骤 4b：多实例并行模式（N 个 qa + 汇总）

5. **主会话拆分**：按审查维度拆分为 N 个子任务，常见维度：
   - **功能测试**：按 plan 的验收标准逐项验证、边界用例、集成流程
   - **安全审查**：OWASP Top 10、注入、XSS、越权、敏感信息泄露、依赖漏洞
   - **性能审查**：时间复杂度、N+1 查询、内存泄漏、响应时间基准
   - **代码质量审查**：代码规范、可维护性、重复代码、圈复杂度、命名一致性
   各维度定义统一的审查报告格式和严重级别标准
6. **并行 spawn N 个 qa Agent**，各负责一个审查维度
7. 各 qa 按标准格式产出各自的审查报告片段，放入 `docs/test-reports/{epic-slug}-qa-{date}/` 子目录
8. **主会话汇总**：合并问题清单，去重（同一问题被多个维度发现时合并），按严重级别排序
9. 整合为统一的 `docs/test-reports/{epic-slug}-qa-full-{date}.md`
10. 记录待更新索引项，由 `/bm-chronicle` 或 `/bm-dev` 统一写入 `docs/INDEX.md` 和 `plans/INDEX.md`

## QA 报告命名规范

### 文件命名

统一 `docs/test-reports/` 下的命名模式：

```
{epic-slug}-{level}-{date}.md
```

| 组成部分 | 说明 | 示例 |
|---------|------|------|
| `epic-slug` | 小写 kebab-case，与 plan feature 名一致 | `agent-tool-expansion`, `smart-assistant` |
| `level` | `qa`（单实例）/ `qa-full`（多维度）/ `qa-verify`（复验）/ `qa-round{N}`（迭代轮次） | `qa`, `qa-full`, `qa-verify`, `qa-round3` |
| `date` | `YYYY-MM-DD` 格式 | `2026-05-21` |

**示例**：
- `agent-tool-expansion-qa-2026-05-21.md` — 单实例审查
- `smart-assistant-qa-full-2026-05-14.md` — 多维度并行审查汇总
- `llm-chat-qa-verify-2026-05-15.md` — 修复后复验

### 路径规则

- **单实例 QA**：报告直放 `docs/test-reports/{epic-slug}-qa-{date}.md`
- **多实例并行 QA**：
  - 维度片段放 `docs/test-reports/{epic-slug}-qa-{date}/` 子目录（文件名为 `function.md`、`security.md`、`performance.md`、`code-quality.md`）
  - 汇总报告合并到 `docs/test-reports/{epic-slug}-qa-full-{date}.md`
  - 汇总完成后子目录可保留或删除

### QA Changelog 规范

QA 相关的 changelog 条目统一放在 story 目录下：

```
docs/changelogs/{epic-slug}/{story-slug}/YYYY-MM-DD-NNN-QA-{简述}.md
```

- **单 story 的 QA 审查**：直接放在对应 story 目录内
- **跨 story 的 QA 审查**：使用 `qa-review` 作为 story slug
- **QA 修复**：文件名加 `-fix` 后缀：`YYYY-MM-DD-NNN-QA-fix-{简述}.md`
- **废弃模式**：`{epic}/qa-fix/` 独立目录不再用于新工作（存量保留）

## 测试报告格式

```markdown
## <功能名称> — 测试报告

**日期**: <日期>
**对应方案**: `plans/<feature>/plan.md`

### 测试覆盖
| 测试项 | 状态 | 备注 |
|--------|------|------|
| ... | ✅/❌ | ... |

### 审查结论
- **方案一致性**: ✅/❌ <说明>
- **代码质量**: ✅/❌ <说明>
- **安全性**: ✅/❌ <说明>

### 发现的问题
- [ ] <问题1>
- [ ] <问题2>

### 签名
QA 通过 / 需修复
```

## 严重级别与审查维度标准

### 严重级别定义

| 级别 | 定义 | 响应要求 |
|------|------|---------|
| **Blocker** | 数据损坏、程序崩溃、安全漏洞、功能彻底不可用 | 必须立即修复，阻塞所有后续开发 |
| **Critical** | 核心功能严重异常、重大数据风险、安全隐患 | 必须在 QA 签字前修复 |
| **Major** | 特定条件下的行为不正确、中等 UX 问题 | 应在发布前修复 |
| **Minor** | 风格不一致、轻微 UX 打磨、优化机会 | 可推迟到 backlog |

### 审查维度范围

| 维度 | 范围 | 典型检查项 |
|------|------|-----------|
| **功能测试** (function) | Plan 验收标准、边界用例、集成流程 | 功能缺失、行为错误、边界情况 |
| **安全审查** (security) | OWASP Top 10、注入、XSS、认证、敏感数据 | SQL/命令注入、路径遍历、Token 泄露 |
| **性能审查** (performance) | 时间复杂度、N+1 查询、内存泄漏 | 大数据量 O(n²)、大内存分配、主线程阻塞 I/O |
| **代码质量** (code-quality) | 规范、可维护性、重复、复杂度 | DRY 违规、圈复杂度 >10、魔法数字 |

## 发现问题后的处理流程（关键变更）

根据问题严重程度分级处理：

### Minor / Major 级别问题
1. QA 在测试报告中列出问题清单
2. 返回主会话（Developer）修复
3. 修复完成后，用户重新调用 `/bm-qa` 进行复验
4. 复验通过后，方可更新状态为"已实现"

### Blocker / Critical 级别问题
1. QA **暂停流程**，向用户汇报问题清单（含严重级别、影响范围、修复建议）
2. **使用选项交互呈现**：通过 `AskUserQuestion` 工具提供选项：
   - "按 QA 建议修复后复验" (Recommended)
   - "调整方案范围（重新进入 /bm-plan）"
   - "接受风险，继续开发（记录风险）"
   - "终止当前方案"
   - "Other — 补充说明"
3. 用户决策后执行对应路径

## 规则

- **Blocker/Critical 问题必须等待用户决策**，不得自动返回修复
- **QA 不直接修改业务代码**：只写测试、出报告、标问题
- **多实例模式下，各维度使用统一的严重级别标准**（Blocker/Critical/Major/Minor），便于汇总排序
- 未通过测试的代码必须标注问题
- 审查需检查：是否超方案范围（跨功能改动）、是否有安全漏洞、是否遵循项目规范
- 通过后更新 `plans/<feature>/plan.md` 状态为"已实现"
- **任何文件修改都必须记录**：测试报告完成后，**必须**调用 `/bm-chronicle` 记录本次增量（含报告产出），未记录不得视为流程结束
- **所有文件路径引用必须从 `.claude/bm_config/paths.json` 读取，不得硬编码。**
- **测试文件不超过 400 行**，超过时按测试区域拆分
- **测试文件必须放入模块子目录**：`tests/{module}/test_{file}.py`，不得散落在 `tests/` 根目录
- **新测试使用 pytest**，禁止 `sys.path.insert` 硬编码
- **QA 报告按统一命名规范**：`{epic-slug}-{level}-{date}.md`

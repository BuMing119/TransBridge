---
name: bm-git
description: Git版本管家：分析最近新增和修改文件，按功能分组规划提交，生成符合项目规范的中文commit message，安全执行git操作
---

# /bm-git — Git 版本管家

## 参数

- `/bm-git` — 完整扫描并规划提交（步骤1-4）
- `/bm-git --status` — 仅扫描，输出当前仓库状态概览，不规划提交
- `/bm-git --auto` — 扫描规划后自动执行用户确认的提交序列
- `/bm-git --push` — 提交完成后推送到远程（需先完成提交规划）

## 职责

1. **状态扫描**：运行 `git status`、`git diff --stat`、`git diff --cached --stat`、`git log --oneline -10`，按功能区域归类所有变更文件
2. **关联映射**：将变更文件与 `docs/changelogs/INDEX.md` 中的 Epic/Story 进行交叉匹配，无法匹配的按目录/功能区域分组
3. **提交规划**：生成有序的提交计划——基础设施 → 数据层 → 业务逻辑 → UI，每条提交包含文件列表和中文 commit message
4. **安全执行**：选择性 `git add` 指定文件，绝不使用 `git add -A` 或 `git add .`，不跳过 hooks，不 amend，不 force push

## 禁止事项

- **绝不使用 `git add -A` 或 `git add .`**：每次只添加属于同一逻辑组的文件
- **绝不跳过 Git hooks**：不使用 `--no-verify`、`--no-gpg-sign` 等跳过钩子的参数
- **绝不 amend 已有提交**：始终创建新提交
- **绝不 force push 到 main/master**：如用户要求 force push 到主分支则警告拒绝
- **不修改 git config**
- **不执行 `git reset --hard`、`git checkout --`、`git clean -f` 等破坏性命令**（除非用户明确要求）
- **不操作未跟踪的敏感文件**（.env、credentials 等）

---

## 步骤一：状态扫描

### 1.1 收集原始数据

并行执行以下命令：

```bash
git status                    # 当前分支、暂存/未暂存/未跟踪文件
git diff --stat               # 未暂存的修改统计
git diff --cached --stat      # 已暂存的修改统计
git log --oneline -10         # 最近10条提交（学习commit message风格）
```

### 1.2 文件归类

按文件路径前缀将每个变更文件归入功能区域：

| 路径前缀 | 功能区域 |
|---------|---------|
| `src/transbridge/ai_translator/` | AI翻译 |
| `src/transbridge/converter/` | 数据层（TranslationEntry/Collection） |
| `src/transbridge/parser/` | 解析器 |
| `src/transbridge/writer/` | 写入器 |
| `src/transbridge/paratranz/` | ParaTranz集成 |
| `src/transbridge/persistence/` | 持久化 |
| `src/transbridge/ui/tools/ai_translator/` | AI翻译UI |
| `src/transbridge/ui/tools/smart_assistant/` | 智能助手 |
| `src/transbridge/ui/workbench/` | 工作台UI |
| `src/transbridge/ui/main_window.py` | 主窗口 |
| `src/transbridge/ui/context.py` | 全局上下文 |
| `docs/` | 文档 |
| `plans/` | 方案 |
| `scripts/` | 脚本 |
| `tests/` | 测试 |

同时标注每个文件的操作类型：
- **增**（新增文件，staged as "new file" 或 untracked）
- **改**（已跟踪文件的修改）
- **删**（已删除的文件）

### 1.3 与 Changelog 交叉匹配

读取 `docs/changelogs/INDEX.md`，提取各 Epic 的目录名和 Story 信息。将变更文件路径与已知 Epic 进行匹配：

- 文件路径包含 Epic 目录关键词 → 标记属于该 Epic
- 同一个 Epic 下有多个 Story → 尽量匹配到具体 Story
- 无法匹配任何已知 Epic → 标记为"未关联"，按功能区域独立分组

### 1.4 特殊项检测

- **CRLF 警告**：git status 中如有 `CRLF` 相关警告，记录受影响的文件
- **未跟踪的应忽略文件**：检测 `__pycache__/`、`.idea/`、`build/`、`dist/`、`*.spec`、`*.pyc` 等应加入 `.gitignore` 但尚未忽略的文件
- **跨区域文件**：单个文件涉及多个功能区域的修改（如 `main_window.py` 同时包含智能助手和工作台改动），标记为需人工审查
- **大变更文件**：单个文件超过 200 行变更，标记提醒

---

## 步骤二：提交分组

### 2.1 分组原则

按以下优先级将文件归入提交组：

1. **同一 Epic/Story** 的文件放在同一提交组
2. **同一功能区域**的文件放在同一提交组（无 Epic 关联时）
3. **依赖顺序**：基础设施（persistence/converter）→ 数据层 → 业务逻辑 → UI → 文档
4. **新增文件和修改文件**可合并到同一提交，前提是属于同一逻辑功能
5. **不相关的变更**严格分开，不强行合并

### 2.2 提交排序

提交按以下层次自底向上排列：

```
第1层: 基础设施（persistence、converter 数据模型等）
第2层: 核心逻辑（ai_translator、parser、writer 等）
第3层: UI组件（新增的UI模块、卡片、对话框等）
第4层: UI集成（main_window、workbench 等将新组件接入的改动）
第5层: 文档与方案（docs/、plans/）
```

### 2.3 未跟踪文件处理

对于未跟踪文件，分类处理：

- **属于当前功能的新文件** → 建议加入对应提交组
- **应加入 .gitignore** → 建议更新 `.gitignore`（`__pycache__/`、`.idea/`、`build/`、`dist/`）
- **不确定归属** → 列出并询问用户

---

## 步骤三：生成提交计划

### 3.1 Commit Message 规范

基于项目历史 commit 风格总结：

- **语言**：中文，主动语态
- **常用动词前缀**：`添加`（新增功能/文件）、`修复`（bug修复）、`优化`（改进）、`重构`（结构调整）、`初始化`（初始搭建）、`统一`（规范化/常量化）、`接入`（集成外部模块）
- **格式**：`<动词><模块/功能>`，如 `添加智能助手面板框架`、`统一Stage常量定义`
- **长度**：10-60 字符
- **多行**：必要时用正文补充说明（`-m "标题" -m "正文"`）

### 3.2 计划输出格式

以表格形式呈现提交计划：

```
提交计划: <当前分支>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[1] 基础设施: 添加项目持久化数据模型
    文件: 4 个 (增)
    - src/transbridge/persistence/__init__.py
    - src/transbridge/persistence/project.py
    - src/transbridge/persistence/variant_store.py
    - src/transbridge/persistence/workspace.py

[2] 数据层: 统一Stage常量定义
    文件: 2 个 (改)
    - src/transbridge/converter/translation_entry.py
    - src/transbridge/converter/translation_entry_collection.py

[3] AI翻译: 添加后处理检查点与润色预览
    文件: 3 个 (2增/1改)
    - ...

...
```

同时展示：
- **CRLF 警告清单**（如有）
- **建议更新 .gitignore 的路径**（如有）
- **需人工审查的混合修改文件**（如有）

### 3.3 用户确认

用 `AskUserQuestion` 呈现提交计划，选项包括：

- "按计划执行全部提交" (Recommended)
- "仅执行选中的提交（请在Other中指定编号，如 1,3,5）"
- "调整分组后再执行"
- "仅查看状态，不提交"

---

## 步骤四：执行提交

### 4.1 逐提交执行

对用户确认的每个提交组：

1. **暂存文件**：`git add <file1> <file2> ...`（只添加该组的文件，不添加其他文件）
2. **检查暂存区**：`git diff --cached --stat` 确认只暂存了目标文件
3. **创建提交**：`git commit -m "..."`（使用 HEREDOC 格式避免转义问题）
4. **验证**：`git status` 确认提交成功

### 4.2 执行期间错误处理

- **pre-commit hook 失败** → 分析 hook 输出，修复问题后重新创建新提交（不 amend）
- **CRLF 警告** → 不阻断，但记录并提醒用户
- **合并冲突** → 暂停，告知用户手动解决

### 4.3 推送（--push 模式）

所有提交完成后：
1. 确认远程分支状态：`git fetch` + 检查是否有新提交
2. 若有冲突风险，先 `git pull --rebase`（仅在不是 main 分支时）
3. `git push`（**绝不 force push**）

---

## --status 模式（仅扫描）

用户调用 `/bm-git --status` 时，只执行步骤一，以精简格式输出：

```
Git 状态概览: main
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
暂存区: 22 个新文件 (待提交)
未暂存: 14 个修改文件
未跟踪: X 个文件

功能区域分布:
  AI翻译        5 文件 (2暂存/3修改)
  智能助手      10 文件 (10暂存/0修改)
  持久化         4 文件 (4暂存/0修改)
  工作台UI       4 文件 (2暂存/2修改)
  数据层         2 文件 (0暂存/2修改)
  ...

← 最近提交: d5b0989 大修功能前的最后一个版本
⚠ CRLF 警告: 5 个文件
⚠ 建议加入 .gitignore: __pycache__/, .idea/, build/, dist/
```

不生成提交计划，不执行任何操作。

---

## 核心规则

- **选择性暂存**：每次 `git add` 只添加属于同一逻辑组的文件，绝不使用通配符或 `-A`
- **不跳过 hooks**：无论任何情况，不使用 `--no-verify` 或等效参数
- **新提交优先**：始终创建新提交，不 amend 已有提交
- **主分支保护**：不 force push 到 main/master
- **先规划后执行**：向用户展示完整提交计划并确认后才开始执行
- **CRLF 不阻断**：CRLF 警告记录但不阻止提交
- **中文 commit message**：使用中文，10-60 字符，动词开头，主动语态
- **提交粒度**：一个逻辑功能一个提交，不过粗（全挤一起）也不过细（每个文件一个提交）
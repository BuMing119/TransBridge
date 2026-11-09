# FR32 存储重做架构与实施规划

- 日期：2026-11-09。
- Epic：`project-storage-redesign`；阶段：planning，不是已实现 Story。
- 需求：[FR32](../../../requirements/project-storage-redesign.md)。
- 产物：[ADR-054](../../../adr/054-project-storage-and-independent-tasks.md)、[实施计划](../../../../plans/project-storage-redesign/plan.md)。
- 状态：架构提议、计划与三份 Story 草稿完成；文档验证完成；实现和运行时验收未开始。
- 授权边界：用户调用 bm-pilot 继续推进，并明确“先不写代码”。本轮未修改生产代码、测试、依赖、锁文件或用户数据，未提交/推送。

## 增量归属

本轮开始时 `docs/requirements.md`、`docs/INDEX.md` 已有前一轮 FR32 需求修改，独立 FR32 需求文件也已创建。不能将这些文件相对 Git HEAD 的全部差异算成本轮新写内容。本轮仅在这些文件更新设计/计划入口与阶段状态；前一轮条款改写不在本次变更清单重记。

Git 工作区还可能显示其他任务造成的换行检查警告；只记录以下可由本次操作直接归属的文档，不记录其他工作线的代码或性能成果。现有历史 changelog 均未改动。

## 文件与具体变化

- 新增 `docs/adr/054-project-storage-and-independent-tasks.md`：比较压缩 JSON、分片日志与 SQLite 三个候选，推荐每项目 SQLite 与独立任务库；定义最小翻译字段、按次插件输入、分页工作副本、稀疏保存、任务合入凭据、术语引用、便携新格式和旧链退役。默认 rollback journal 的理由基于当前运行时与官方文档，不声称性能已验证。
- 新增 `plans/project-storage-redesign/plan.md`：建立 S01～S10，列出依赖、文件落点、验收与未来测试命令；记录超限文件责任审查、性能场景和候选预算；九组 FR32 完整映射。全部 Story 保持未开始。
- 新增 `plans/project-storage-redesign/stories/story-02-source-extraction.md`：细化字段消费者审查、插件/路径禁存、多来源原子创建、翻译文件原生字段保真与输入释放。
- 新增 `plans/project-storage-redesign/stories/story-03-working-copy-save.md`：细化未加载内容、dirty patch、查询视图、保存期间继续编辑、提交确认与原子故障语义。
- 新增 `plans/project-storage-redesign/stories/story-06-result-commit-reconciliation.md`：细化项目/任务跨库非原子边界、待保存状态、项目内凭据、任务回执对账、删除/回收及八类故障点。
- 修改 `docs/adr/018-project-session-persistence-v2.md`、`035-authoritative-project-mutations-and-recoverable-save.md`、`049-task-result-lifecycle.md`、`050-application-preflight-and-optional-source-reference.md`、`051-self-contained-project-storage.md`：仅修订状态头，明确被新需求调整的范围及 ADR-054 替代提议；正文历史理由与验证事实保留。
- 修改 `docs/requirements/project-storage-redesign.md`：保留已确认行为，增加 ADR/计划链接与“提议/草稿、未实现”的状态；技术细节由相应文档维护，不把提议写成已批准需求。
- 修改 `docs/requirements.md`：仅在 FR32 入口段补充后续架构/计划链接。
- 修改 `docs/INDEX.md`、`plans/INDEX.md`：增加本轮产物与准确阶段状态。
- 新增本记录；修改 `docs/changelogs/INDEX.md` 为本记录提供入口。

## 实际验证

- 读取 AGENTS、BM 配置、相关 skill、FR32、旧 ADR、入口及数据/任务调用链；复读新增 ADR、plan 与三个 Story，核对三方合入、未加载保存和兼容边界。
- 使用 `.venv/Scripts/python.exe -B -` 只读核验 Python/SQLite 版本与相关源文件行数：Python 3.12.12、SQLite 3.50.4；未打开或改写用户数据库。
- 查询 SQLite 官方 WAL、隔离与 Backup API，以及 Python 3.12 sqlite3 文档；引用在 ADR-054 对应决策附近。未安装依赖或改 journal 配置。
- stdin Python 文档校验：首轮验证 19 个本地链接/锚点、10 个 Story、9 组 FR32 对应关系及 3 份详细指南，全部通过；后续新增索引和本记录纳入最终复验。
- stdin Python 代码路径核验：49 个明确既有代码/测试路径存在；不存在的目标为文档明确标记的拟新增接口、模块、测试或 benchmark，没有把它们冒充现有 API。
- `git diff --check -- docs plans` 通过；最终对本轮文档再做定向检查。Git 发出部分 LF/CRLF 转换提示，不是检查失败。
- `git diff --name-only -- src tests scripts pyproject.toml uv.lock` 在 Git 归一化后无输出、退出码 0；本轮未写这些文件。`git diff --cached --stat` 无暂存变更。

本轮未运行 pytest、Ruff、GUI、性能 benchmark、故障注入或 SQLite 原型：交付物为文档，用户明确不写代码，新增测试与原型尚不存在。计划中的命令均为后续验证建议，不计为通过证据。未生成任务临时目录，无本轮临时文件需清理。

## 未完成项与限制

S01～S10 开发、真实格式往返、任务独立运行、多窗口/崩溃验证与性能验收均未执行，遵守本轮范围。技术提议尚未由用户确认；具体格式标识数值、字段矩阵和参考机预算在相应 Story 固化。不存在旧项目迁移或自动清理承诺；已有旧目录占用不会因文档落盘减少。
